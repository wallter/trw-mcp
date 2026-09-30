"""``trw-mcp env gc``: remove the version venvs a shared env no longer uses (SWAP-VENV-GC).

Belongs to the shared-server CLI (``_cli.py``). Every ``swap --version`` builds ``<envs_dir>/<env>/venv-<V>`` (and a
``venv-<V>+distill-<Y>`` fork when trw-distill differs) and never removes the old one, so a canary env grows by one
venv per cut (9.8 GB after ~50 cuts, 2026-09-30).

What may be removed -- ALL must hold, and anything unreadable or unexpected is kept and reported, never deleted:

* a real directory (``lstat``: never a symlink or its target) directly in the env dir, named exactly
  ``venv-<V>`` or ``venv-<V>+distill-<Y>`` (the names ``build_version_venv`` creates);
* positive proof it is a venv: a regular ``pyvenv.cfg`` inside, just read, with a ``home =`` line;
* no ``envs.json`` entry (for ANY env) points into it, and it is not the version the env's live server publishes;
* its base version parses and is not among the newest *keep* base versions (their forks are kept with them);
* on ``--apply`` only: its ``<venv>.lock`` is taken non-blocking (a swap building it makes it "in use", skipped),
  and ``envs.json`` is re-read under that lock just before removal.

Dry run is the default: it prints what would go and removes nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

from trw_mcp.shared_server._records import SharedPaths, SharedServerError, read_env_map, read_live_record, validate_env

__all__ = ["GcDecision", "plan_gc", "run_gc"]

_VENV_NAME = re.compile(r"^venv-(?P<base>[A-Za-z0-9][A-Za-z0-9._-]*?)(?:\+distill-(?P<distill>[A-Za-z0-9._-]+))?$")


@dataclass(frozen=True)
class GcDecision:
    """One entry of the env dir: removed (``remove``) or kept, with the reason either way."""

    path: Path
    remove: bool
    reason: str


def _referenced(paths: SharedPaths, env_dir: Path) -> set[str]:
    """Names of the venvs in *env_dir* that any env's ``envs.json`` interpreter lives in."""
    names: set[str] = set()
    for python in read_env_map(paths).values():
        try:
            relative = Path(python).relative_to(env_dir)
        except ValueError:  # trw-fail-silent-allow: an interpreter outside this env dir references none of its venvs
            continue
        if relative.parts:
            names.add(relative.parts[0])
    return names


def _is_owned_venv(venv: Path) -> str | None:
    """``None`` when *venv* is provably a venv we may remove, else why it is kept (positive proof only)."""
    try:
        st = os.lstat(venv)
    except OSError as exc:
        return f"kept: cannot inspect it ({type(exc).__name__})"
    if not stat.S_ISDIR(st.st_mode):
        return "kept: not a real directory (a symlink or a file is never followed or removed)"
    cfg = venv / "pyvenv.cfg"
    try:
        if not stat.S_ISREG(os.lstat(cfg).st_mode):
            return "kept: pyvenv.cfg is not a regular file"
        text = cfg.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return f"kept: pyvenv.cfg unreadable ({type(exc).__name__}), so not provably a venv"
    if not any(line.split("=", 1)[0].strip() == "home" for line in text.splitlines() if "=" in line):
        return "kept: pyvenv.cfg has no home line, so not provably a venv"
    return None


def plan_gc(paths: SharedPaths, env: str, *, keep: int) -> list[GcDecision]:
    """What ``env gc`` would do for *env*, one decision per ``venv-*`` entry, newest first. Removes nothing."""
    if keep < 1:
        raise SharedServerError("--keep must be at least 1: the newest version venv is always kept")
    env_dir = paths.envs_dir / validate_env(env)
    try:
        entries = sorted(os.listdir(env_dir))
    except FileNotFoundError:  # trw-fail-silent-allow: an env with no venv dir has nothing to collect
        return []
    live = read_live_record(paths, env)
    live_version: Version | None = None
    if live is not None:
        try:
            live_version = Version(live.version)
        except InvalidVersion:
            # Cannot tell which venv the running server uses: keep every version venv rather than guess.
            why = f"kept: {env}'s live version {live.version!r} does not parse"
            return [GcDecision(env_dir / name, False, why) for name in entries if _VENV_NAME.match(name)]
    referenced = _referenced(paths, env_dir)
    parsed: list[tuple[Version, str]] = []
    decisions: list[GcDecision] = []
    for name in entries:
        match = None if name.endswith(".lock") else _VENV_NAME.match(name)
        if match is None:
            continue  # memory/, security/, <venv>.lock and anything else: not a version venv, not ours to judge
        try:
            parsed.append((Version(match["base"]), name))
        except InvalidVersion:
            decisions.append(GcDecision(env_dir / name, False, "kept: version does not parse"))
    newest = sorted({version for version, _ in parsed}, reverse=True)[:keep]
    for version, name in sorted(parsed, reverse=True):
        venv = env_dir / name
        if name in referenced:
            reason = "kept: envs.json points an env at it"
        elif version == live_version:
            reason = f"kept: {env}'s live server runs {version}"
        elif version in newest:
            reason = f"kept: among the newest {keep} version(s)"
        else:
            reason = _is_owned_venv(venv) or ""
        decisions.append(GcDecision(venv, not reason, reason or "unused: no env record references it"))
    return decisions


def run_gc(paths: SharedPaths, env: str, *, keep: int, apply: bool) -> list[str]:
    """Report lines for ``env gc``; with *apply*, remove each removable venv under its lock."""
    from trw_mcp.shared_server._ops import _venv_lock

    lines: list[str] = []
    for decision in plan_gc(paths, env, keep=keep):
        if not decision.remove:
            lines.append(f"keep    {decision.path.name}: {decision.reason}")
            continue
        if not apply:
            lines.append(f"would remove {decision.path.name}: {decision.reason}")
            continue
        # The BASE venv's lock, for a fork too: build_version_venv holds venv-<V>.lock while it builds venv-<V>+distill-Y.
        base = decision.path.with_name(decision.path.name.split("+distill-", 1)[0])
        try:
            with _venv_lock(base):
                # Re-check under the lock: a swap may have pointed an env at it since the plan was made.
                if decision.path.name in _referenced(paths, decision.path.parent):
                    lines.append(f"keep    {decision.path.name}: an env now points at it")
                    continue
                lines.append(_remove_proven_venv(decision.path))
        except SharedServerError as exc:
            lines.append(f"keep    {decision.path.name}: in use ({exc})")
    return lines


_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _remove_proven_venv(venv: Path) -> str:
    """Remove the EXACT directory proven to be a venv, through a descriptor on it; never by a name that could move.

    The venv is opened ``O_NOFOLLOW`` and its ``pyvenv.cfg`` proven through that descriptor. Its contents are then
    removed relative to the same descriptor (subdirectories by ``shutil.rmtree(name, dir_fd=...)``, which never
    follows a link), so a file, link or directory swapped in at the venv's NAME is never touched. The emptied
    directory is finally ``rmdir``-ed by name: that can only ever remove an EMPTY directory, so if the name now holds
    anything else the call fails and it is kept.
    """
    try:
        fd = os.open(venv, _DIR_FLAGS)
    except OSError as exc:
        return f"keep    {venv.name}: cannot open it as a real directory ({type(exc).__name__})"
    try:
        if (why := _cfg_proof_at(fd)) is not None:
            return f"keep    {venv.name}: {why}"
        failures = _empty_at(fd)
    finally:
        os.close(fd)
    if failures:
        return f"partly removed {venv.name}: {failures} entr(y/ies) could not be removed; the rest is gone"
    try:
        os.rmdir(venv)
    except OSError as exc:
        return f"emptied {venv.name}, but its name now holds something else ({type(exc).__name__}); that is kept"
    return f"removed {venv.name}"


def _empty_at(dir_fd: int) -> int:
    """Remove every entry of the directory open as *dir_fd*, never following a link; returns the failure count."""
    failures = 0
    for entry in list(os.scandir(dir_fd)):
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.name, dir_fd=dir_fd)
            else:
                os.unlink(entry.name, dir_fd=dir_fd)
        except FileNotFoundError:  # trw-fail-silent-allow: already gone is the goal state
            continue
        except OSError:  # trw-fail-silent-allow: counted and reported by the caller, never raised part-way
            failures += 1
    return failures


def _cfg_proof_at(dir_fd: int) -> str | None:
    """``None`` when the directory open as *dir_fd* holds a regular ``pyvenv.cfg`` with a ``home`` line."""
    try:
        cfg_fd = os.open("pyvenv.cfg", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    except OSError as exc:
        return f"pyvenv.cfg unreadable ({type(exc).__name__}), so not provably a venv"
    with os.fdopen(cfg_fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return "pyvenv.cfg is not a regular file"
        text = handle.read(65536).decode("utf-8", errors="replace")
    if not any(line.split("=", 1)[0].strip() == "home" for line in text.splitlines() if "=" in line):
        return "pyvenv.cfg has no home line, so not provably a venv"
    return None
