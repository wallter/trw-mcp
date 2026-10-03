"""Operator verbs for shared envs: swap, env create and status (the doctor row lives in ``_doctor``).

``swap`` points ONE env at an interpreter: ``--python`` (a venv, a venv dir, or
a worktree whose ``.venv`` has trw-mcp installed editable), ``--src`` (a linked
git worktree of this repo with no venv: this interpreter, serving with
``PYTHONPATH=<wt>/trw-mcp/src:<wt>/trw-memory/src``) or ``--version``
(an isolated venv under the env dir, installed from the local wheelhouse with
``uv --offline``; never a network index). When the env is serving, a successor
starts beside it, flips the record and drains it; proxies follow the record on
their next call. Other envs are untouched.

``env create`` gives a non-stable env its own ``TRW_USER_DIR`` (so its own
memory daemon and store). The checkout's memory grants are copied so its token
authenticates there; ``--seed-from`` copies a consistent snapshot of the source
store (``VACUUM INTO`` over a registered connection) together with its quarantine
ledger and review queue: a store never lands without the ledger that filters it
(PRD-CORE-333), and a file that exists but cannot be looked up or copied refuses
the seed. The signing key and the audit, anomaly and rate-limit history stay fresh
per env. The source is only read, and nothing at the target is ever overwritten.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import structlog
from trw_memory._tree_removal import remove_tree

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _embeddings
from trw_mcp.shared_server._distill_spec import _distill_spec
from trw_mcp.shared_server._records import (
    STABLE,
    SharedPaths,
    SharedServerError,
    env_python,
    env_pythonpath,
    read_env_map,
    read_live_record,
    read_token,
    set_env_python,
    validate_env,
)
from trw_mcp.shared_server._reuse import fork_current, forked_venv, reuse_venv

_SWAP_SECONDS = 120.0

logger = structlog.get_logger(__name__)


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=600, check=False, env=env)  # noqa: S603 -- fixed argv
    if done.returncode != 0:
        raise SharedServerError(f"`{' '.join(argv)}` failed ({done.returncode}): {done.stderr.strip()[-1200:]}")
    return done.stdout.strip()


def _probe_env(pythonpath: str | None = None) -> dict[str, str]:
    """This process's environment minus PYTHONPATH/PYTHONHOME, plus *pythonpath* only when the env records one."""
    clean = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    return clean if pythonpath is None else {**clean, "PYTHONPATH": pythonpath}


def resolve_python(path: Path) -> Path:
    """An interpreter path, a venv dir, or a dir holding ``.venv`` (a worktree) -> its python."""
    for candidate in (path, path / "bin" / "python", path / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return candidate
    raise SharedServerError(f"no python at {path} (tried it, bin/python and .venv/bin/python)")


def _git_common_dir(path: Path) -> Path:
    """The repo's shared ``.git`` dir for *path*; raises (naming git's error) when *path* is in no repo."""
    return Path(_run(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"])).resolve()


def worktree_pythonpath(src: Path, project_root: Path) -> str:
    """``<wt>/trw-mcp/src:<wt>/trw-memory/src`` for *src*, a git worktree of *project_root*'s repo; else refuse."""
    worktree = src.expanduser().resolve()
    refusal = f"{worktree} is not a git worktree of this repo ({project_root}); --src takes one (`git worktree list`)"
    try:
        same_repo = _git_common_dir(worktree) == _git_common_dir(project_root)
    except (SharedServerError, OSError) as exc:  # the refusal carries git's own reason
        raise SharedServerError(f"{refusal}: {exc}") from exc
    if not same_repo:
        raise SharedServerError(refusal)
    dirs = [worktree / "trw-mcp" / "src", worktree / "trw-memory" / "src"]
    missing = [str(d) for d in dirs if not d.is_dir()]
    if missing:
        raise SharedServerError(f"{worktree} has no {' or '.join(missing)}; nothing changed")
    return os.pathsep.join(str(d) for d in dirs)


def _importable(python: Path, module: str) -> bool:
    try:
        _run([str(python), "-c", f"import {module}"], env=_probe_env())
    except (
        SharedServerError,
        OSError,
    ):  # trw-fail-silent-allow: a failed (or unrunnable) import IS this probe's answer
        return False
    return True


def wheelhouse_for(config: SharedMcpConfig) -> Path:
    return Path(config.wheelhouse).expanduser()


def _pip_install(python: Path, wheelhouse: Path, specs: list[str | None]) -> None:
    """Offline install of *specs*: trw_* wheels from *wheelhouse*, third-party deps from uv's offline cache.

    Never ``--no-index``: the wheelhouse holds only trw_* wheels, so an index-free resolve cannot find e.g. anyio.
    ``--compile-bytecode`` pays the .pyc compile here, before the swap: uv skips it by default, and a venv
    without it made the swapped-in daemon's first recall compile torch/scipy/transformers (~53 s under load).
    """
    _run(
        [
            *("uv", "pip", "install", "--quiet", "--offline", "--compile-bytecode", "--find-links", str(wheelhouse)),
            *("--python", str(python)),
            *[spec for spec in specs if spec],
        ],
        env=_probe_env(),  # never the swapper's PYTHONPATH/PYTHONHOME
    )


def _present(path: Path) -> bool:
    """Does *path* exist (as a link or not)? Only ``FileNotFoundError`` means absent; any other error propagates."""
    try:
        os.lstat(path)
    except FileNotFoundError:  # trw-fail-silent-allow: absence IS this probe's answer
        return False
    except OSError as exc:  # unreadable is not absent: refuse rather than build over it
        raise SharedServerError(f"cannot inspect {path} ({type(exc).__name__}: {exc}); nothing was changed") from exc
    return True


@contextlib.contextmanager
def _venv_lock(venv: Path) -> Iterator[None]:
    """Exclusive, non-blocking ``<venv>.lock`` beside *venv*; another holder refuses this swap."""
    try:
        venv.parent.mkdir(parents=True, exist_ok=True)
        handle = open(venv.parent / f"{venv.name}.lock", "a")  # noqa: SIM115 -- closed in the finally below
    except OSError as exc:
        raise SharedServerError(f"cannot lock {venv} ({type(exc).__name__}: {exc}); nothing was changed") from exc
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SharedServerError(f"another swap is building {venv}; retry") from exc
        yield
    finally:
        handle.close()  # releases the lock; the lock file itself is never deleted


def _unusable(venv: Path) -> SharedServerError:
    return SharedServerError(f"{venv} exists but is not usable; remove it manually (nothing was deleted)")


def build_version_venv(
    paths: SharedPaths,
    env: str,
    version: str,
    config: SharedMcpConfig,
    *,
    with_distill: str | None = None,
    embeddings: bool = True,
) -> Path:
    """``<envs_dir>/<env>/venv-<version>`` with trw-mcp==*version* and trw-distill (T2 hints) from the wheelhouse.

    Nothing this call did not create is ever deleted: under an exclusive ``venv-<version>.lock`` an existing
    venv is reused when its python imports trw_mcp, and refused ("exists but is not usable") otherwise. A reused
    venv lacking trw-distill gets it via ``reuse_venv`` (a failed install keeps the venv and reports on stderr,
    and refuses the swap only for an explicit ``--with`` pin). A reused venv whose trw-distill differs from the
    resolved spec (an explicit ``--with``: any difference; the auto-selected wheelhouse wheel: only if newer) is
    left untouched: a fresh ``venv-<version>+distill-<Y>`` is built (or reused when it already carries that
    distill), and the old venv stays as the fallback. A later plain ``swap --version V`` resolves the base
    ``venv-V``, so an explicit ``--with`` pin does not persist; old venvs and forks are never removed here --
    ``trw-mcp env gc`` removes the ones no env uses (dry run unless ``--apply``). A venv absent at the path gets trw-mcp and trw-distill (*with_distill*, else the
    highest compatible wheelhouse wheel) installed offline, and only that new dir is removed if anything fails.
    With no distill wheel and no *with_distill*, trw-mcp alone is installed and stderr says distill was skipped.

    With *embeddings* (the project's ``embeddings_enabled``) every returned venv also has ``trw-memory[all]`` at its
    own trw-memory version: a venv without sentence-transformers would serve keyword-only recall silently. A failed
    extras install refuses the swap, naming what is missing and how to fetch it (``_embeddings.install``); a new
    venv is then removed like any failed build, an existing one is kept.
    """
    wheelhouse = wheelhouse_for(config)
    distill = _distill_spec(wheelhouse, with_distill)  # validates --with before any probe
    if not wheelhouse.is_dir():  # before any other filesystem action
        raise SharedServerError(f"no local wheelhouse at {wheelhouse}; build one, or pass --python")
    venv = paths.envs_dir / validate_env(env) / f"venv-{validate_env(version)}"
    pinned = with_distill is not None
    with _venv_lock(venv):
        target = venv
        if _present(venv):
            python = venv / "bin" / "python"
            if not (_present(python) and _importable(python, "trw_mcp")):
                raise _unusable(venv)
            fork = forked_venv(venv, distill, pinned=pinned)
            if fork is None:
                reuse_venv(venv, wheelhouse, distill, pinned=pinned)
                if embeddings:
                    _embeddings.install(python, wheelhouse)
                return python
            print(f"trw-distill differs from {venv}; using {fork}; old venv kept at {venv}", file=sys.stderr)
            target = fork
            if _present(fork):
                if not (distill and fork_current(fork / "bin" / "python", distill.partition("==")[2])):
                    raise _unusable(fork)
                reuse_venv(fork, wheelhouse, distill, pinned=pinned)
                if embeddings:
                    _embeddings.install(fork / "bin" / "python", wheelhouse)
                return fork / "bin" / "python"
        if distill is None:
            print(
                f"trw-distill skipped: no compatible trw_distill wheel in {wheelhouse} and no --with", file=sys.stderr
            )
        python = target / "bin" / "python"
        try:  # target was absent under the lock: this call created it, so only it is removed on failure
            _run(["uv", "venv", "--quiet", str(target)])
            _pip_install(python, wheelhouse, [f"trw-mcp=={version}", *([distill] if distill else [])])
            if embeddings:
                _embeddings.install(python, wheelhouse)
        except BaseException:
            remove_tree(target, purpose="failed version venv build")
            raise
        return python


def swap(
    paths: SharedPaths,
    env: str,
    python: Path,
    *,
    project_root: Path,
    expect: str | None,
    pythonpath: str | None = None,
) -> str:
    """Point *env* at *python* (serving with *pythonpath* when given); hot-swap it when serving. A one-line report."""
    from trw_mcp.shared_server._proxy import spawn_server, wait_published

    probe_env = _probe_env(pythonpath)
    probe = "import trw_mcp; print(trw_mcp.__version__); print(trw_mcp.__file__)"
    version, _, module_file = _run([str(python), "-c", probe], env=probe_env).partition("\n")
    if pythonpath is not None and not Path(module_file).resolve().is_relative_to(
        Path(pythonpath.split(os.pathsep)[0]).resolve()
    ):
        raise SharedServerError(f"{python} imports trw_mcp from {module_file}, not {pythonpath}; nothing changed")
    if expect is not None and version != expect:
        raise SharedServerError(f"{python} runs trw-mcp {version}, not {expect}; nothing changed")
    if validate_env(env) != STABLE and not _memory_dir(paths, env).is_dir():
        ensure_env(paths, env, seed_from=None)
    set_env_python(paths, env, python, pythonpath=pythonpath)
    _record_launcher(paths, env, python, pythonpath)  # the next client to autostart this store's daemon starts THIS one
    old = read_live_record(paths, env)
    if old is None:
        source = python if pythonpath is None else f"{python}, PYTHONPATH={pythonpath}"
        return f"{env} -> trw-mcp {version} ({source}); starts on the next proxy call"
    proc = spawn_server(paths, env, project_root=str(project_root), successor=True)
    new = wait_published(paths, env, proc, seconds=_SWAP_SECONDS, not_pid=old.pid)
    report = f"{env}: pid {old.pid} (v{old.version}) draining -> pid {new.pid} (v{new.version})"
    if expect is not None and new.version != expect:  # the successor's own published version, not the probe's
        report += f" -- WARNING: the successor serves v{new.version}, expected {expect}"
    return report


def _record_launcher(paths: SharedPaths, env: str, python: Path, pythonpath: str | None) -> None:
    """Write *env*'s store its launcher record, naming *python* and the trw-memory it serves (trw-memory's API)."""
    from trw_memory.daemon import DaemonPaths, write_launcher_record

    write_launcher_record(
        DaemonPaths(user_memory_dir=_memory_dir(paths, env)),
        python,
        env_memory_version(paths, env),
        pythonpath=pythonpath,
    )


def register_launcher(paths: SharedPaths, env: str) -> bool:
    """Boot-time self-registration: this server's own interpreter becomes its store's launcher unless one is current.

    Covers an env swapped before launcher records existed (no record until its next ``swap``) and a hot-swap
    successor. Skipped when this process is not the interpreter *env* is recorded to run (then the version
    it would write is not that interpreter's). Returns whether it wrote.
    """
    from trw_memory import __version__ as memory_version
    from trw_memory.daemon import DaemonPaths, register_launcher_record

    recorded = env_python(paths, env)
    if os.path.abspath(recorded) != os.path.abspath(sys.executable):
        return False
    return register_launcher_record(
        DaemonPaths(user_memory_dir=_memory_dir(paths, env)),
        Path(recorded),
        memory_version,
        pythonpath=env_pythonpath(paths, env),
    )


def env_memory_version(paths: SharedPaths, env: str) -> str:
    """The trw-memory version *env*'s recorded interpreter (and PYTHONPATH) will start the next daemon with."""
    return _run(
        [env_python(paths, env), "-c", "from trw_memory._version import __version__; print(__version__)"],
        env=_probe_env(env_pythonpath(paths, env)),
    )


def drain_env_daemon(paths: SharedPaths, env: str, *, project_root: Path) -> str:
    """Drain *env*'s trw-memory daemon by the drain handshake (never a signal); one report line, or refuse.

    The env's own memory dir (its ``TRW_USER_DIR``) decides which ``daemon.json`` is read; stable uses the
    default dir, so a swapper with ``TRW_USER_DIR`` set is refused rather than aimed at another store. The
    record is read first: absent means nothing to drain (exit 0, no grant or probe needed). The pre-read can
    go stale before the drain; the handshake re-proves the record and its refusal reason is then reported.
    """
    from trw_memory.daemon import DaemonInfo, DaemonPaths, DiscoveryAbsent, DiscoveryInvalid, _discovery, _upgrade
    from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH, read_checkout_grant
    from trw_memory.exceptions import DaemonAuthError

    if validate_env(env) == STABLE and os.environ.get("TRW_USER_DIR"):
        raise SharedServerError(
            "TRW_USER_DIR is set in this shell, so stable's daemon dir cannot be resolved as the proxy "
            "resolves it; unset TRW_USER_DIR and retry; no daemon was drained"
        )
    daemon_paths = DaemonPaths(user_memory_dir=_memory_dir(paths, env))
    record = _discovery.read_live_discovery(daemon_paths)
    if isinstance(record, DiscoveryAbsent):
        return f"no daemon running for env {env}; nothing to drain"
    if isinstance(record, DiscoveryInvalid):
        raise SharedServerError(f"{record.path} cannot be trusted ({record.reason}); no daemon was drained")
    if not isinstance(record, DaemonInfo):
        raise SharedServerError(f"unexpected discovery answer {type(record).__name__}; no daemon was drained")
    try:
        token = read_checkout_grant(project_root)
    except DaemonAuthError as exc:
        raise SharedServerError(
            f"no usable memory grant for the drain (looked for {project_root / CHECKOUT_TOKEN_RELPATH} and "
            f"its parents): {exc}"
        ) from exc
    mine = env_memory_version(paths, env)
    why = _upgrade.drain_daemon(daemon_paths, token=token, mine=mine)
    if why:
        raise SharedServerError(f"daemon pid {record.pid} (v{record.version}) was not drained: {why}")
    return f"daemon pid {record.pid} (v{record.version}) drained; next recall starts v{mine}"


def _memory_dir(paths: SharedPaths, env: str) -> Path:
    from trw_memory.user_paths import resolve_user_memory_dir

    user_dir = paths.user_dir(env)
    return resolve_user_memory_dir(create=False) if user_dir is None else user_dir / "memory"


def ensure_env(paths: SharedPaths, env: str, *, seed_from: str | None) -> Path:
    """Create *env*'s memory dir with the checkout's grants; with *seed_from*, snapshot-copy that store."""
    if validate_env(env) == STABLE:
        raise SharedServerError("stable keeps the default memory dir; it is never created or seeded here")
    target = _memory_dir(paths, env)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = _memory_dir(paths, seed_from or STABLE)
    if (source / "daemon-grants.json").is_file():
        shutil.copy2(source / "daemon-grants.json", target / "daemon-grants.json")  # keeps 0600
    if seed_from is None:
        return target
    if (target / "memory.db").exists():
        raise SharedServerError(f"{target / 'memory.db'} already exists; seeding never overwrites a store")
    if not (source / "memory.db").is_file():
        raise SharedServerError(f"no store to seed from at {source / 'memory.db'}")
    from trw_memory.storage import _snapshot

    store = target / "memory.db"
    staging = Path(tempfile.mkdtemp(prefix=".memory.db.seeding-", dir=target))  # private, 0700: no name collision
    staged = staging / "memory.db"
    seeded: list[Path] = []
    notes: dict[str, str] = {}
    try:
        try:
            _snapshot.create_snapshot(source / "memory.db", staged)  # VACUUM INTO over a registered connection
        except _snapshot.SnapshotError as exc:
            raise SharedServerError(f"could not snapshot {source / 'memory.db'}: {exc}") from exc
        # The store never lands without its quarantine ledger: a seeded store would otherwise serve
        # every identity the source blocks (PRD-CORE-333). The review queue (quarantine.db) travels
        # too, so the new env can review what the source holds (D9); the signing key
        # and the audit, anomaly and rate-limit history stay fresh per env. Anything that exists but
        # cannot be copied refuses the seed, and the store is published last.
        for label, field in _CARRIED:
            try:
                notes[label] = _carry(source, target, field, seeded)
            except (_SeedError, OSError) as exc:
                raise _refusal(paths, env, seed_from, store, seeded, f"the {label} {exc}") from exc
        try:
            _snapshot.publish_no_clobber(staged, store)  # the link is the gate: an existing store is never replaced
        except OSError as exc:
            what = "already exists; seeding never overwrites a store" if isinstance(exc, FileExistsError) else exc
            raise _refusal(paths, env, seed_from, store, seeded, f"could not publish {store}: {what}") from exc
    finally:
        remove_tree(staging, purpose="env seed staging directory")
    logger.info("shared_env_seeded", env=env, source=seed_from, **{k.replace(" ", "_"): v for k, v in notes.items()})
    return target


# What a seed carries beside the store, in publish order (D9). Not carried: the ed25519 signing
# key and the audit / anomaly / rate-limit history, which stay fresh per env, and the
# ``security/quarantine/`` directory (recall_filter's optional shadow jsonl; nothing in src
# writes it today, a known issue).
_CARRIED = (
    ("quarantine ledger", "quarantine_ledger_path"),
    ("review queue", "quarantine_db_path"),
)


class _SeedError(Exception):
    """A carried security file exists but could not be copied; the message says why."""


def _security_path(memory_dir: Path, field: str) -> Path:
    """The *field* security path the memory daemon serving *memory_dir* reads (its config's derivation)."""
    from trw_memory.models.config import MemoryConfig
    from trw_memory.security.startup import resolve_security_path

    config = MemoryConfig(storage_path=str(memory_dir), memory_single_store_path=str(memory_dir / "memory.db"))
    return resolve_security_path(config, field)


def _carry(source: Path, target: Path, field: str, seeded: list[Path]) -> str:
    """Copy *source*'s *field* SQLite file to *target*'s, never over an existing one; a log note."""
    from trw_memory.security.quarantine_ledger import LedgerSeedError, seed_ledger
    from trw_memory.storage import _snapshot

    src, dst = _security_path(source, field), _security_path(target, field)
    if src == dst:
        return f"one file serves both envs ({src})"
    try:
        seed = seed_ledger if field == "quarantine_ledger_path" else _snapshot.seed_no_clobber
        copied = seed(src, dst) is not None
    except LedgerSeedError as exc:
        raise _SeedError(str(exc).removeprefix("the quarantine ledger ")) from exc
    except FileExistsError as exc:
        raise _SeedError(f"cannot be seeded: {dst} already exists, and seeding never overwrites it") from exc
    except (_snapshot.SnapshotError, OSError) as exc:
        raise _SeedError(f"{src} could not be copied to {dst} ({type(exc).__name__}: {exc})") from exc
    if not copied:
        return f"none at {src}"
    seeded.append(dst)
    return f"copied from {src}"


def _refusal(
    paths: SharedPaths, env: str, seed_from: str, store: Path, seeded: list[Path], why: str
) -> SharedServerError:
    """The seed's refusal; after anything was seeded it names each file and the retry, never deleting one."""
    if not seeded:
        return SharedServerError(why if "no store was seeded" in why else f"{why}; no store was seeded")
    # Never delete or replace a seeded file: a daemon may already have appended decisions to the ledger.
    return SharedServerError(
        f"{why}. Already seeded: {', '.join(str(p) for p in seeded)}; the store {store} was not, and nothing was "
        f"deleted. To retry, the user can check that no daemon serves {env} (`trw-mcp status --shared`), then "
        f"remove the incomplete env directory `rm -r {paths.user_dir(env)}` and run "
        f"`trw-mcp env create {env} --seed-from {seed_from}` again."
    )


def status_rows(paths: SharedPaths) -> list[dict[str, Any]]:
    """Each env's interpreter and, when serving, the server's own status answer."""
    rows = []
    mapping = read_env_map(paths)
    for env in paths.envs() or [STABLE]:
        row: dict[str, Any] = {"env": env, "python": mapping.get(env, "(the proxy's interpreter)")}
        info = read_live_record(paths, env)
        if info is None:
            row["state"] = "stopped (starts on the next proxy call)"
        else:
            try:
                answer = httpx.get(
                    info.url.removesuffix("/mcp") + "/admin/status",
                    timeout=5.0,
                    headers={"authorization": f"Bearer {read_token(paths)}"},
                )
                row.update(answer.json(), state="serving")
            except (httpx.HTTPError, ValueError) as exc:
                row.update(pid=info.pid, version=info.version, state=f"unresponsive ({type(exc).__name__})")
        rows.append(row)
    return rows
