"""PRD-FIX-140-FR04 — the PostToolUse hook leaves change evidence for an UNPINNED session.

Before this branch the hook exited when it could not resolve a run, so a session
that never called ``trw_init`` recorded NOTHING when it edited files. The deliver
gate's change-evidence clause therefore measured 0 for every unpinned session and
could never block on "this session changed code" — the client-side hook papered
over that by blocking every missing build record instead, which is the over-block
PRD-FIX-140 removes.

These tests run the REAL shipped script and then feed its output to the REAL
reader and gate, so the writer and the reader are proven to agree on the record
shape and on the session key.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

import trw_mcp.tools._delivery_helpers  # noqa: F401  (import-cycle order guard)
from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo
from trw_mcp.tools._delivery_event_checks import unpinned_session_changed_files

_REPO_ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
_BUNDLED_HOOK = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "hooks" / "post-tool-event.sh"
_PROJECTED_HOOK = _REPO_ROOT / ".claude" / "hooks" / "post-tool-event.sh"
_SESSION = "sess-unpinned-1"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".trw" / "context").mkdir(parents=True)
    return root


def _run_hook(project_root: Path, tool: str, file_path: str, hook: Path = _BUNDLED_HOOK) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": file_path}, "session_id": "host-1"})
    return subprocess.run(
        ["sh", str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "CLAUDE_PROJECT_DIR": str(project_root),
            "TRW_SESSION_ID": _SESSION,
        },
        check=False,
    ).returncode


def _records(project_root: Path) -> list[dict[str, Any]]:
    stream = project_root / ".trw" / "context" / "session-events.jsonl"
    if not stream.is_file():
        return []
    return [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines() if line.strip()]


class TestUnpinnedEditsAreRecorded:
    def test_an_edit_with_no_pinned_run_writes_one_attributed_record(self, tmp_path: Path) -> None:
        root = _project(tmp_path)

        assert _run_hook(root, "Edit", str(root / "src" / "a.py")) == 0

        records = _records(root)
        assert len(records) == 1
        assert records[0]["event"] == "file_modified"
        assert records[0]["session_id"] == _SESSION
        assert records[0]["file"] == "src/a.py", "the path must be repo-relative so it dedupes on read"
        assert records[0]["tool"] == "Edit"
        assert records[0]["pinned"] is False

    @pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
    def test_every_edit_tool_is_recorded(self, tmp_path: Path, tool: str) -> None:
        root = _project(tmp_path)

        assert _run_hook(root, tool, str(root / "src" / "a.py")) == 0
        assert len(_records(root)) == 1

    @pytest.mark.parametrize("tool", ["Read", "Grep", "Bash"])
    def test_a_non_editing_tool_records_nothing(self, tmp_path: Path, tool: str) -> None:
        root = _project(tmp_path)

        assert _run_hook(root, tool, str(root / "src" / "a.py")) == 0
        assert _records(root) == []

    def test_the_hook_never_blocks_the_tool(self, tmp_path: Path) -> None:
        """Fail-open: an unwritable stream must not turn into a non-zero exit."""
        root = _project(tmp_path)
        (root / ".trw" / "context" / "session-events.jsonl").mkdir()

        assert _run_hook(root, "Edit", str(root / "src" / "a.py")) == 0

    def test_no_project_root_is_survivable(self, tmp_path: Path) -> None:
        assert _run_hook(tmp_path / "missing", "Edit", "src/a.py") == 0

    @requires_monorepo
    def test_the_projection_is_byte_identical_to_the_bundle(self) -> None:
        assert _PROJECTED_HOOK.read_bytes() == _BUNDLED_HOOK.read_bytes()


class TestTheReaderSeesWhatTheHookWrote:
    """End-to-end: hook output -> reader -> gate, with no fixture in between."""

    def test_the_reader_counts_distinct_hook_written_paths(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        for path in ("src/a.py", "src/b.py", "src/a.py"):
            assert _run_hook(root, "Edit", str(root / path)) == 0

        assert unpinned_session_changed_files(root / ".trw", _SESSION) == 2

    def test_another_sessions_edits_are_not_counted(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        assert _run_hook(root, "Edit", str(root / "src" / "a.py")) == 0

        assert unpinned_session_changed_files(root / ".trw", "a-different-session") == 0

    def test_an_unpinned_session_that_edited_code_is_blocked_by_trw_deliver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The P1, closed end to end: hook-written evidence reaches the delivery decision."""
        from trw_mcp.models.config import get_config
        from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

        root = _project(tmp_path)
        (root / ".trw" / "context" / "ceremony-state.json").write_text(
            json.dumps({"session_started": True, "session_build_results": {}}), encoding="utf-8"
        )
        threshold = int(get_config().deliver_gate_unclassified_change_threshold)
        for index in range(threshold):
            assert _run_hook(root, "Edit", str(root / f"src/mod_{index}.py")) == 0

        monkeypatch.setenv("TRW_SESSION_ID", _SESSION)
        results: dict[str, Any] = {}
        errors: list[str] = []

        blocked = evaluate_delivery_gates({}, cast("Any", results), errors, None, root / ".trw", False, "")

        assert blocked is True, "an unpinned session that edited code delivered without a build check"
        assert f"{threshold} file(s)" in str(results["delivery_blocked"])
        assert results["missing_gate"] == "build_check"

    def test_the_same_session_with_a_passing_build_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-vacuity for the block above: the evidence, not the fixture, decides."""
        from datetime import datetime, timedelta, timezone

        from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

        root = _project(tmp_path)
        assert _run_hook(root, "Edit", str(root / "src" / "a.py")) == 0
        # The pass is recorded AFTER the edit (stamped 2 s ahead so the hook's
        # second-resolution record cannot tie with it): a pass that predates the
        # last edit is stale and would block (P1-1, release-verify 2026-09-17).
        passed_at = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
        (root / ".trw" / "context" / "ceremony-state.json").write_text(
            json.dumps(
                {
                    "session_started": True,
                    "session_build_results": {_SESSION: "passed"},
                    "session_build_results_at": {_SESSION: passed_at},
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setenv("TRW_SESSION_ID", _SESSION)
        results: dict[str, Any] = {}

        assert evaluate_delivery_gates({}, cast("Any", results), [], None, root / ".trw", False, "") is False
