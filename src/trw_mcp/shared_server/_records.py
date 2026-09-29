"""Where a shared env lives on disk: discovery record, token, interpreter map, memory dir.

Reuses the trw-memory daemon's primitives rather than a parallel copy: the
record IS a :class:`~trw_memory.daemon._discovery.DaemonInfo` (loopback-only
URL, pid plus OS process start for liveness) written through the same 0600
atomic ``write_secret_file`` path, and a symlinked or unreadable secret raises
instead of reading as absent.

Per project, ``.trw/runtime/shared-mcp/`` holds ``<env>.json`` (record),
``<env>.lock`` (claim), ``<env>.log`` (server stderr), ``envs.json`` (env ->
interpreter), ``envs-pythonpath.json`` (env -> the ``PYTHONPATH`` a ``swap --src``
serves it with), ``<env>.serving-env`` (the allowlisted environment of the env's first confirmed start,
replayed for swap successors) and ``token``. A non-stable env's memory daemon and store live in
``<envs_dir>/<env>`` (its ``TRW_USER_DIR``), so a newer major never opens
stable's store; stable keeps the default user dir.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trw_memory.daemon._discovery import DISCOVERY_SCHEMA_VERSION, DaemonInfo
from trw_memory.daemon._paths import read_secret_file, write_secret_file
from trw_memory.storage._pid_liveness import process_start

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig

STABLE = "stable"
_ENV_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
SESSION_HEADER = "mcp-session-id"
DRAINING_HEADER = "x-trw-mcp-draining"
BUSY_HEADER = "x-trw-mcp-busy"
_MAP_FILES = frozenset({"envs.json", "envs-pythonpath.json"})  # beside the records, never an env's record


class SharedServerError(RuntimeError):
    """A shared-server operation refused; the message names the remedy."""


def validate_env(name: str) -> str:
    """Return *name* when it is a valid env name (``stable``, ``dev``, ``8.0.0.dev3``), else raise."""
    if not _ENV_RE.match(name):
        raise SharedServerError(f"env {name!r} must match {_ENV_RE.pattern} (e.g. stable, dev, 8.0.0.dev3)")
    return name


@dataclass(frozen=True)
class SharedPaths:
    """Every file one project's shared servers own, derived from one directory."""

    root: Path
    token: Path
    envs_dir: Path

    @classmethod
    def resolve(cls, trw_dir: Path, config: SharedMcpConfig) -> SharedPaths:
        root = trw_dir / "runtime" / "shared-mcp"
        token = Path(config.token_path).expanduser() if config.token_path else root / "token"
        return cls(root=root, token=token, envs_dir=Path(config.envs_dir).expanduser())

    def record(self, env: str) -> Path:
        return self.root / f"{validate_env(env)}.json"

    def lock(self, env: str) -> Path:
        return self.root / f"{validate_env(env)}.lock"

    def log(self, env: str) -> Path:
        return self.root / f"{validate_env(env)}.log"

    def user_dir(self, env: str) -> Path | None:
        """``TRW_USER_DIR`` for *env*; ``None`` keeps stable on the default user dir."""
        return None if validate_env(env) == STABLE else self.envs_dir / env

    def envs(self) -> list[str]:
        """Every env with a record or an interpreter."""
        names = {p.stem for p in self.root.glob("*.json") if p.name not in _MAP_FILES}
        return sorted(names | set(read_env_map(self)))


def ensure_token(paths: SharedPaths) -> str:
    """The bearer every proxy presents; minted once at 0600 when absent."""
    existing = read_secret_file(paths.token)
    if existing is not None and existing.strip():
        return existing.strip()
    token = secrets.token_urlsafe(32)
    write_secret_file(paths.token, token)
    return token


def read_token(paths: SharedPaths) -> str:
    raw = read_secret_file(paths.token)
    if raw is None or not raw.strip():
        raise SharedServerError(f"no shared-server token at {paths.token}; a server mints it when it starts")
    return raw.strip()


def read_live_record(paths: SharedPaths, env: str) -> DaemonInfo | None:
    """The env's record while its process runs; a stale or other-schema record reads as absent."""
    raw = read_secret_file(paths.record(env))
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload.pop("PYTHONPATH", None)  # older records held it; it is ignored now, not invalid
        if not isinstance(payload, dict):
            raise TypeError("not a JSON object")
        if payload.get("schema_version") != DISCOVERY_SCHEMA_VERSION:
            return None
        info = DaemonInfo.model_validate(payload)
    except (ValueError, TypeError) as exc:  # never read as "absent": that would start a second server beside a live one
        raise SharedServerError(f"{paths.record(env)} is not a valid record ({exc}); inspect it") from exc
    return info if info.is_live(paths.lock(env)) else None


def publish_record(paths: SharedPaths, env: str, *, url: str, version: str) -> DaemonInfo:
    """Atomically point *env* at THIS process (the swap flip)."""
    info = DaemonInfo(
        pid=os.getpid(),
        url=url,
        started_at=datetime.now(timezone.utc).isoformat(),
        version=version,
        process_start=process_start(os.getpid()),
        capabilities=["drain"],
    )
    write_secret_file(paths.record(env), info.model_dump_json())
    return info


def withdraw_record(paths: SharedPaths, env: str) -> None:
    """Remove the record only while it still names this process (a successor's flip stays)."""
    raw = read_secret_file(paths.record(env))
    try:
        mine = raw is not None and json.loads(raw).get("pid") == os.getpid()
    except (ValueError, AttributeError):
        mine = False  # not ours to judge: leave it for the operator (read_live_record names it)
    if mine:
        paths.record(env).unlink(missing_ok=True)


def read_env_map(paths: SharedPaths) -> dict[str, str]:
    raw = read_secret_file(paths.root / "envs.json")
    return dict(json.loads(raw)) if raw else {}


def read_pythonpath_map(paths: SharedPaths) -> dict[str, str]:
    raw = read_secret_file(paths.root / "envs-pythonpath.json")
    return dict(json.loads(raw)) if raw else {}


def set_env_python(paths: SharedPaths, env: str, python: Path, *, pythonpath: str | None = None) -> None:
    """Point *env* at *python*; *pythonpath* is recorded beside it, and a swap without one clears it."""
    env = validate_env(env)
    sources = read_pythonpath_map(paths)
    if pythonpath:
        sources[env] = pythonpath
    else:
        sources.pop(env, None)
    write_secret_file(paths.root / "envs-pythonpath.json", json.dumps(sources, sort_keys=True))
    mapping = read_env_map(paths)
    mapping[env] = str(python)
    write_secret_file(paths.root / "envs.json", json.dumps(mapping, sort_keys=True))


def env_pythonpath(paths: SharedPaths, env: str) -> str | None:
    """The ``PYTHONPATH`` *env* is served with (a ``swap --src`` worktree), else ``None``."""
    return read_pythonpath_map(paths).get(validate_env(env)) or None


def env_python(paths: SharedPaths, env: str) -> str:
    """The interpreter that serves *env*; stable defaults to this one, others must be swapped in first."""
    mapped = read_env_map(paths).get(validate_env(env))
    if mapped:
        return mapped
    if env == STABLE:
        return sys.executable
    raise SharedServerError(f"env {env!r} has no interpreter; run `trw-mcp swap --env {env} --python <path>`")


# PYTHONPATH is deliberately absent: it belongs to the env's swap record (``env_pythonpath``) alone.
_SERVING_ENV_KEYS = ("PATH", "HOME")
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASS", "CREDENTIAL", "AUTH", "PWD", "COOKIE", "BEARER", "DSN")


def _secret_looking(name: str, value: str) -> bool:
    return any(m in name.upper() for m in _SECRET_MARKERS) or ("://" in value and "@" in value)


def serving_env_keys(environ: Mapping[str, str]) -> list[str]:
    """The keys of *environ* worth recording: ``TRW_*``, PATH, HOME; never a secret-looking one."""
    return sorted(
        k for k, v in environ.items() if (k.startswith("TRW_") or k in _SERVING_ENV_KEYS) and not _secret_looking(k, v)
    )


def serving_env_path(paths: SharedPaths, env: str) -> Path:
    """One 0600 file per env (no shared map, so no cross-env lost update)."""
    return paths.root / f"{validate_env(env)}.serving-env"


def env_serving_env(paths: SharedPaths, env: str) -> dict[str, str] | None:
    """The environment *env* was first started with (allowlisted), else ``None``.

    A recorded key is replayed for every later swap, even one that points at an obsolete store; to re-record
    (for example after re-creating the env), delete ``<env>.serving-env`` and let the next start write it.
    PYTHONPATH is never recorded here: ``swap --src``'s ``env_pythonpath`` is the only source of it.

    Absent, unreadable or corrupt all read as ``None`` (an older env): a bad record must never block a spawn.
    """
    try:
        raw = read_secret_file(serving_env_path(paths, env))
        if not raw:
            return None
        payload = json.loads(raw)
        if isinstance(payload, dict):
            payload.pop("PYTHONPATH", None)  # older records held it; it is ignored now, not invalid
        if not isinstance(payload, dict) or not all(
            isinstance(k, str) and isinstance(v, str) and "\0" not in v for k, v in payload.items()
        ):
            raise TypeError("not a str->str object")
        if serving_env_keys(payload) != sorted(payload):
            raise ValueError("a key outside the allowlist or on the deny-list")
        return dict(payload)
    except (
        ValueError,
        TypeError,
        OSError,
    ):  # trw-fail-silent-allow: logged, and None IS the documented answer (older env fallback)
        print(
            f"trw-mcp: serving-env record for {env!r} is unreadable; ignoring it", file=sys.stderr, flush=True
        )  # stdout is the MCP channel; never the values
        return None


def record_serving_env(paths: SharedPaths, env: str, environ: Mapping[str, str]) -> None:
    """Record *environ*'s allowlisted keys for *env* unless one is already readable (first start wins)."""
    if env_serving_env(paths, env) is not None:
        return
    write_secret_file(
        serving_env_path(paths, env), json.dumps({k: environ[k] for k in serving_env_keys(environ)}, sort_keys=True)
    )
