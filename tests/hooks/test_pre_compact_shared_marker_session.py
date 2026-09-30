"""E2E-INC-031: a session with no identity in its environment never replays another session's compaction.

With neither TRW_SESSION_ID nor the client session variable set, pre-compact.sh writes the project-wide
``pre_compact_state.json`` (the MCP server reads that same file, so its name stays). It resolved the run from the
payload's ``session_id``; another session compacting next replayed that run as its own "RECOVERED" state. The
snapshot now records the session key it was written for, and session-start.sh replays a shared marker only for
the same key.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from _ownership_harness import BUNDLED_HOOKS, OWN_RUN_ID, SESSION_ID, project, shell_env

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh hooks")


def _hook(root: Path, name: str, payload: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(BUNDLED_HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=shell_env(root),  # no TRW_SESSION_ID, no client session variable
        timeout=60,
        check=False,
    )


def _compact_then_start(tmp_path: Path, starting_session: str) -> str:
    root, _ = project(tmp_path)
    _hook(root, "pre-compact.sh", {"session_id": SESSION_ID, "trigger": "auto"})
    assert (root / ".trw" / "context" / "pre_compact_state.json").is_file(), "the shared marker was not written"
    return _hook(root, "session-start.sh", {"source": "compact", "session_id": starting_session}).stdout


def test_another_session_does_not_recover_the_compacted_sessions_run(tmp_path: Path) -> None:
    out = _compact_then_start(tmp_path, "99999999-8888-7777-6666-000000000000")

    assert "RECOVERED" not in out
    assert OWN_RUN_ID not in out


def test_the_same_session_still_recovers_its_run(tmp_path: Path) -> None:
    out = _compact_then_start(tmp_path, SESSION_ID)

    assert "RECOVERED: Run at" in out and OWN_RUN_ID in out
