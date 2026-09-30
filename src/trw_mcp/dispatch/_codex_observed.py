"""What a codex child reports it actually ran, read from its own session rollout (CODEX-P0-A S2).

Belongs to the ``trw_mcp.dispatch`` package. ``codex exec --json`` events carry no model or effort,
but codex writes a rollout per session, ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl``,
whose ``turn_context`` names the model and effort and whose ``session_meta`` names the CLI version.
This is CLIENT-REPORTED evidence (what the CLI says it sent), never server-verified, and the record says so.

Reads are bounded (``_MAX_LINES`` lines, ``_MAX_BYTES`` bytes), confined to the sessions directory, and
keep only those three values -- no prompt, response or tool content is retained. Anything missing is
``"unknown"`` with a reason; an observation is never copied from what the argv applied.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path, PurePosixPath

from trw_mcp._checkout_access import open_under

__all__ = ["codex_home_dir", "newest_rollout", "observe_codex", "read_rollout", "with_observed"]

_MAX_LINES = 200
_MAX_BYTES = 2_000_000
_CHUNK = 65_536
_UNKNOWN = "unknown"


def codex_home_dir() -> Path | None:
    """The codex home (``$CODEX_HOME`` or ``~/.codex``); ``None`` for a relative or ``..``-bearing override."""
    home = os.environ.get("CODEX_HOME", "").strip()
    if not home:
        return Path.home() / ".codex"
    path = Path(home)
    return path if path.is_absolute() and ".." not in path.parts else None


def _thread_id(raw_stdout: str) -> str | None:
    for line in raw_stdout.splitlines()[:50]:
        try:
            event = json.loads(line)
        except ValueError:  # trw-fail-silent-allow: a non-JSON stdout line is not the thread.started event
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            tid = event.get("thread_id")
            return tid if isinstance(tid, str) and tid.replace("-", "").isalnum() else None
    return None


def _candidates(home: Path, pattern: str) -> list[str]:
    """Rollout paths relative to *home*, newest name first; opening each still refuses any symlink."""
    return sorted((str(p.relative_to(home)) for p in home.glob(f"sessions/*/*/*/{pattern}")), reverse=True)


def newest_rollout(home: Path) -> str | None:
    """The newest ``sessions/…/rollout-*.jsonl`` under *home* by name, relative to it."""
    return max(_candidates(home, "rollout-*.jsonl"), key=lambda rel: Path(rel).name, default=None)


def _bounded_lines(fd: int) -> list[bytes]:
    """At most ``_MAX_LINES`` complete lines, never reading past ``_MAX_BYTES`` in total."""
    buffer, read = b"", 0
    lines: list[bytes] = []
    while read < _MAX_BYTES and len(lines) < _MAX_LINES:
        chunk = os.read(fd, min(_CHUNK, _MAX_BYTES - read))
        if not chunk:
            break
        read += len(chunk)
        *complete, buffer = (buffer + chunk).split(b"\n")
        lines.extend(complete)
    return lines[:_MAX_LINES]


def read_rollout(home: Path, relative: str) -> dict[str, object]:
    """``{"model", "effort", "cli_version", "by"}`` from one rollout under *home*; ``reason`` names a gap.

    *relative* must be a plain ``sessions/…`` path. The file is opened from *home*'s parent, so neither the
    home itself nor any component below it may be a symlink (``open_under`` follows only its anchor); it is
    opened non-blocking, must be a regular file, and the read is bounded while it happens
    (reviews W1-CODEX-P0A-S2-r1/r2).
    """
    found: dict[str, object] = {"model": _UNKNOWN, "effort": _UNKNOWN, "cli_version": _UNKNOWN, "by": "client-rollout"}
    parts = PurePosixPath(relative).parts
    if PurePosixPath(relative).is_absolute() or not parts or parts[0] != "sessions" or ".." in parts:
        return {**found, "reason": "rollout_refused"}
    try:
        fd = open_under(home.parent, f"{home.name}/{relative}")
    except OSError:
        return {**found, "reason": "rollout_unreadable"}
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {**found, "reason": "rollout_unreadable"}
        lines = _bounded_lines(fd)
    except OSError:
        return {**found, "reason": "rollout_unreadable"}
    finally:
        os.close(fd)
    for raw in lines:
        try:
            event = json.loads(raw)
        except (
            ValueError,
            RecursionError,
        ):  # trw-fail-silent-allow: a malformed or too-deep line carries no key; reported as key_absent
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "session_meta" and isinstance(payload.get("cli_version"), str):
            found["cli_version"] = payload["cli_version"]
        elif event.get("type") == "turn_context":
            for key in ("model", "effort"):
                if found[key] == _UNKNOWN and isinstance(payload.get(key), str):
                    found[key] = payload[key]
    if found["model"] == _UNKNOWN or found["effort"] == _UNKNOWN:
        found["reason"] = "key_absent"
    return found


def observe_codex(raw_stdout: str, codex_home: Path | None = None) -> dict[str, object]:
    """``{"model", "effort", "cli_version", "by"}``; each missing value is ``"unknown"`` plus a ``reason``."""
    unknown: dict[str, object] = {
        "model": _UNKNOWN,
        "effort": _UNKNOWN,
        "cli_version": _UNKNOWN,
        "by": "client-rollout",
    }
    thread_id = _thread_id(raw_stdout)
    if thread_id is None:
        return {**unknown, "reason": "no_thread_id"}
    home = codex_home or codex_home_dir()
    if home is None:
        return {**unknown, "reason": "codex_home_refused"}
    found = _candidates(home, f"rollout-*-{thread_id}.jsonl")
    if not found:
        return {**unknown, "reason": "rollout_missing"}
    return read_rollout(home, found[0])


def with_observed(
    policy: dict[str, dict[str, object]], client: str, raw_stdout: str, codex_home: Path | None = None
) -> dict[str, dict[str, object]]:
    """*policy* with each knob's ``observed`` value and, only when they differ, a ``mismatch`` flag.

    Only a client whose registry entry names an ``observation_source`` is annotated; every other
    client is left unchanged (client ids stay in the registry module).
    """
    from trw_mcp.dispatch._client_observation import OBSERVATION_SOURCES

    if OBSERVATION_SOURCES.get(client) != "codex-rollout":
        return policy
    seen = observe_codex(raw_stdout, codex_home)
    out = {name: dict(record) for name, record in policy.items()}
    for knob in ("model", "effort"):
        if knob not in out:
            continue
        observed = seen[knob]
        out[knob]["observed"] = observed
        applied = out[knob].get("applied")
        if observed != _UNKNOWN and applied is not None and observed != applied:
            out[knob]["mismatch"] = True
    out["observation"] = {k: v for k, v in seen.items() if k not in ("model", "effort")}
    return out
