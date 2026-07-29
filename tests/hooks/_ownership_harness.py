"""Shared fixture builders for the hook run-ownership tests.

Every project this module builds is deliberately hostile in the same way the live
repository is: it always contains a FOREIGN run whose id sorts strictly after the
session's own, so the run a recency heuristic would pick is never the right one. A
fixture holding a single run cannot fail on the bug these tests guard, so none of
them hold one.

Used by ``test_run_ownership.py`` (the primitive + PRD-FIX-118's own two hooks)
and ``test_hook_ownership_decisions.py`` (the six hooks migrated by ledger
UF-047).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = _TESTS_ROOT.parent.parent
BUNDLED_HOOKS = _TESTS_ROOT.parent / "src" / "trw_mcp" / "data" / "hooks"
MIRROR_HOOKS = REPO_ROOT / ".claude" / "hooks"

#: The client session id the hook shell and the MCP server both observe.
CLIENT_SESSION_VAR = "CLAUDE_CODE_SESSION_ID"
SESSION_ID = "11111111-2222-3333-4444-555555555555"

#: Lexicographically LATER than the owned run -- this is what newest-run-wins
#: would select, and what must never be adopted.
OWN_RUN_ID = "20260101T000000Z-0000owned"
FOREIGN_RUN_ID = "29991231T235959Z-ffffalien"

FILE_MODIFIED = '{"ts":"2026-01-01T00:00:00Z","event":"file_modified"}'
CHECKPOINT = '{"ts":"2026-01-01T00:00:00Z","event":"checkpoint"}'
BUILD_CHECK = '{"ts":"2026-01-01T00:00:00Z","event":"build_check_complete"}'
DELIVER_COMPLETE = '{"ts":"2026-01-01T00:00:00Z","event":"trw_deliver_complete"}'


def mk_run(
    root: Path,
    task: str,
    run_id: str,
    *,
    events: int = 0,
    event_lines: tuple[str, ...] | None = None,
    phase: str | None = None,
) -> Path:
    """Create a run directory. ``events`` seeds N file_modified rows."""
    run = root / ".trw" / "runs" / task / run_id
    (run / "meta").mkdir(parents=True, exist_ok=True)
    run_yaml = f"task: {task}\ncomplexity_class: MINIMAL\n"
    if phase is not None:
        run_yaml += f"phase: {phase}\n"
    (run / "meta" / "run.yaml").write_text(run_yaml, encoding="utf-8")
    lines = list(event_lines) if event_lines is not None else [FILE_MODIFIED] * events
    if lines:
        (run / "meta" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run


def write_pins(root: Path, pins: object) -> None:
    path = root / ".trw" / "runtime" / "pins.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pins if isinstance(pins, str) else json.dumps(pins), encoding="utf-8")


def write_hook_env(root: Path, client_id: str = "claude-code") -> Path:
    """Generate .trw/runtime/hook-env.sh with the REAL production writer."""
    from trw_mcp.bootstrap._file_ops import _write_hook_env_file
    from trw_mcp.models.config._profiles import resolve_client_profile

    return _write_hook_env_file(root / ".trw", resolve_client_profile(client_id))


def shell_env(root: Path, **extra: str) -> dict[str, str]:
    """A minimal environment: nothing ambient leaks in, so identity is explicit."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "CLAUDE_PROJECT_DIR": str(root),
    }
    env.update(extra)
    return env


def project(tmp_path: Path, *, own_pin: bool = True, extra_pins: int = 2) -> tuple[Path, Path]:
    """A project root with a foreign, newer run that recency would prefer.

    Returns ``(root, own_run)``. ``own_run`` exists on disk either way; only the
    pins.json entry is conditional, so an "unowned" case differs from an owned one
    by exactly one fact: whether this session's key is in the pin store.
    """
    root, own, _foreign = build_project(
        tmp_path, own_pin=own_pin, own_events=3, foreign_events=18, extra_pins=extra_pins
    )
    return root, own


def build_project(
    tmp_path: Path,
    *,
    own_pin: bool = True,
    own_events: int = 0,
    foreign_events: int = 0,
    own_event_lines: tuple[str, ...] | None = None,
    foreign_event_lines: tuple[str, ...] | None = None,
    own_phase: str | None = None,
    foreign_phase: str | None = None,
    extra_pins: int = 2,
) -> tuple[Path, Path, Path]:
    """The general form of :func:`project`. Returns ``(root, own_run, foreign_run)``."""
    root = tmp_path / "proj"
    (root / ".trw" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / ".trw" / "context").mkdir(parents=True, exist_ok=True)
    own = mk_run(root, "owned-task", OWN_RUN_ID, events=own_events, event_lines=own_event_lines, phase=own_phase)
    foreign = mk_run(
        root,
        "foreign-task",
        FOREIGN_RUN_ID,
        events=foreign_events,
        event_lines=foreign_event_lines,
        phase=foreign_phase,
    )
    pins: dict[str, dict[str, object]] = {
        f"foreign-key-{i}": {"run_path": str(foreign), "pid": 424242 + i} for i in range(extra_pins)
    }
    if own_pin:
        pins[SESSION_ID] = {"run_path": str(own), "pid": 999999}
    write_pins(root, pins)
    return root, own, foreign


def run_hook(
    hook: Path,
    root: Path,
    *,
    payload: object = None,
    identified: bool = True,
    **extra: str,
) -> subprocess.CompletedProcess[str]:
    """Execute a hook exactly as a client would: real shell, real stdin payload.

    ``identified=False`` withholds the client session variable, which is how a
    client that publishes no identity at all reaches the legacy branch.
    """
    env = dict(extra)
    if identified:
        env.setdefault(CLIENT_SESSION_VAR, SESSION_ID)
    return subprocess.run(
        ["sh", str(hook)],
        input="" if payload is None else json.dumps(payload),
        capture_output=True,
        text=True,
        env=shell_env(root, **env),
        timeout=60,
        check=False,
    )
