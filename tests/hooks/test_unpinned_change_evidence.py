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

    def test_a_fresh_checkout_without_a_context_dir_still_records_the_edit(self, tmp_path: Path) -> None:
        """codex-a P2: a missing ``.trw/context`` must not turn an edit into zero evidence."""
        root = tmp_path / "project"
        (root / ".trw").mkdir(parents=True)

        assert _run_hook(root, "Edit", str(root / "src" / "a.py")) == 0

        assert [record["event"] for record in _records(root)] == ["file_modified"]

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


def _stamp(offset_seconds: int = 0) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_unreadable_edit(root: Path, ts: str) -> None:
    """What post-tool-event.sh writes when jq is absent: an edit it could not read (T29)."""
    stream = root / ".trw" / "context" / "session-events.jsonl"
    with stream.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": ts, "event": "change_evidence_unknown", "reason": "jq_unavailable"}) + "\n")


class TestAJqLessHostIsUnknownNotZero:
    """T29: a host that could not read its edits must not look like one that made none.

    Before this, the gate counted only ``file_modified`` rows. A jq-less hook that
    records nothing it cannot parse therefore produced an honest-looking zero, and
    an unpinned session that edited code delivered with no build check.
    """

    def test_an_unpinned_session_on_a_jq_less_host_is_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

        root = _project(tmp_path)
        (root / ".trw" / "context" / "ceremony-state.json").write_text(
            json.dumps({"session_started": True, "session_build_results": {}}), encoding="utf-8"
        )
        _record_unreadable_edit(root, _stamp())
        assert unpinned_session_changed_files(root / ".trw", _SESSION) is None, "unknown, not zero"

        monkeypatch.setenv("TRW_SESSION_ID", _SESSION)
        results: dict[str, Any] = {}
        blocked = evaluate_delivery_gates({}, cast("Any", results), [], None, root / ".trw", False, "")

        assert blocked is True, "a jq-less host delivered as if nothing had changed"
        assert "could not be read" in str(results["delivery_blocked"])

    def test_a_pinned_run_on_a_jq_less_host_counts_as_uncomputable(self, tmp_path: Path) -> None:
        from trw_mcp.tools._deliver_gate_mode import count_session_changed_files, resolve_deliver_gate_decision

        root = _project(tmp_path)
        run = root / ".trw" / "runs" / "task" / "run-1"
        (run / "meta").mkdir(parents=True)
        _record_unreadable_edit(root, _stamp())

        files_changed = count_session_changed_files(events=[], run_path=run, session_id=_SESSION)

        assert files_changed is None
        assert resolve_deliver_gate_decision(
            mode="block_coding", task_type="docs", build_check_missing=True, files_changed=files_changed
        )

    def test_an_unreadable_edit_after_the_passing_build_makes_it_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime, timedelta, timezone

        from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

        root = _project(tmp_path)
        passed_at = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
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
        _record_unreadable_edit(root, _stamp())

        monkeypatch.setenv("TRW_SESSION_ID", _SESSION)
        assert evaluate_delivery_gates({}, cast("Any", {}), [], None, root / ".trw", False, "") is True

    def test_a_marker_from_before_this_server_started_does_not_block(self, tmp_path: Path) -> None:
        """Non-vacuity: once jq is installed, an old marker stops mattering at the next server start."""
        from trw_mcp.tools._delivery_event_checks import PROCESS_STARTED_AT

        root = _project(tmp_path)
        _record_unreadable_edit(root, (PROCESS_STARTED_AT.replace(microsecond=0)).strftime("%Y-01-01T00:00:00Z"))

        assert unpinned_session_changed_files(root / ".trw", _SESSION) == 0
