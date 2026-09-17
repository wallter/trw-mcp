"""PRD-FIX-140 FR01-FR05 — one delivery authority.

The bundled PreToolUse hook used to derive its own delivery verdict from the
project-global ``.trw/context/build-status.yaml``. These tests cover the demotion
(the hook is a diagnostic), the two rules that had to move server-side before the
demotion was safe (an unpinned session's recorded build FAILURE, and a run whose
LATEST build check failed), and the pinned-run decisions that must not change.

The hook cases execute the real shipped script through ``subprocess`` — the
previous harness was a ``.sh`` file no runner invoked, so its assertions had never
run in CI or in ``make check``.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

# ``_delivery_event_checks`` and ``_delivery_helpers`` are import-cycle partners:
# the facade must be imported FIRST or the sibling's module-level import of
# ``_check_complexity_drift`` lands on a partially initialised module. Expressed as
# a call, not an import statement, so the isort pass cannot reorder it below the
# imports it has to precede.
importlib.import_module("trw_mcp.tools._delivery_helpers")

from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates
from trw_mcp.tools._deliver_gate_mode import (
    resolve_deliver_gate_decision,
    resolve_unpinned_gate_decision,
)
from trw_mcp.tools._delivery_event_checks import (
    latest_build_check_failed,
    unpinned_build_failure_recorded,
    unpinned_build_passed,
    unpinned_session_changed_files,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_HOOK = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "hooks" / "pre-tool-deliver-gate.sh"
_PROJECTED_HOOK = _REPO_ROOT / ".claude" / "hooks" / "pre-tool-deliver-gate.sh"
_DELIVER_PAYLOAD = '{"tool_name":"mcp__trw__trw_deliver"}'


def _run_hook(project_root: Path, payload: str = _DELIVER_PAYLOAD) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(_BUNDLED_HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "CLAUDE_PROJECT_DIR": str(project_root)},
        check=False,
    )


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".trw" / "context").mkdir(parents=True)
    return root


class TestBundledHookIsDiagnosticOnly:
    """FR01 — the hook reports evidence and never decides."""

    @pytest.mark.parametrize(
        ("label", "build_status", "ceremony_state"),
        [
            ("passed receipt", "tests_passed: true\nscope: unit\n", None),
            ("failed receipt", "tests_passed: false\nscope: unit\n", None),
            ("timed-out receipt", "tests_passed: false\ntimed_out: true\n", None),
            ("no receipt", None, None),
            ("failed ceremony state", None, {"build_check_result": "failed", "last_build_check_ts": "2020-01-01"}),
            ("stale passing state", None, {"build_check_result": "passed", "last_build_check_ts": "2020-01-01"}),
        ],
    )
    def test_the_hook_never_blocks(
        self,
        tmp_path: Path,
        label: str,
        build_status: str | None,
        ceremony_state: dict[str, object] | None,
    ) -> None:
        root = _project(tmp_path)
        if build_status is not None:
            (root / ".trw" / "context" / "build-status.yaml").write_text(build_status, encoding="utf-8")
        if ceremony_state is not None:
            (root / ".trw" / "context" / "ceremony-state.json").write_text(json.dumps(ceremony_state), encoding="utf-8")

        result = _run_hook(root)

        assert result.returncode == 0, f"{label}: hook exited {result.returncode}"
        assert "BLOCKED" not in result.stdout + result.stderr, f"{label}: hook still emits a verdict"
        assert "DELIVER-GATE (diagnostic)" in result.stdout, f"{label}: no evidence line"

    def test_the_hook_reports_the_evidence_it_found(self, tmp_path: Path) -> None:
        root = _project(tmp_path)
        (root / ".trw" / "context" / "build-status.yaml").write_text(
            "tests_passed: false\nscope: 'pytest tests -q'\n", encoding="utf-8"
        )

        stdout = _run_hook(root).stdout

        assert "tests_passed=false" in stdout
        assert "pytest tests -q" in stdout

    def test_a_non_deliver_tool_is_ignored(self, tmp_path: Path) -> None:
        result = _run_hook(_project(tmp_path), '{"tool_name":"Read"}')

        assert result.returncode == 0
        assert result.stdout == ""

    def test_a_missing_project_directory_is_survivable(self, tmp_path: Path) -> None:
        assert _run_hook(tmp_path / "does-not-exist").returncode == 0

    def test_the_projection_is_byte_identical_to_the_bundle(self) -> None:
        assert _PROJECTED_HOOK.read_bytes() == _BUNDLED_HOOK.read_bytes()

    def test_no_blocking_predicate_survives_in_either_copy(self) -> None:
        for copy in (_BUNDLED_HOOK, _PROJECTED_HOOK):
            body = copy.read_text(encoding="utf-8")
            assert "exit 2" not in body, f"{copy} re-derives a verdict"
            assert "deliver-override-audit.jsonl" not in body, f"{copy} still writes the retired audit file"


class TestEveryDeliverRouteReachesTheServerGate:
    """FR02 — the control-flow invariant, asserted on OUTCOMES not callees."""

    def test_an_accepted_delivery_without_build_evidence_reaches_the_gate(self, tmp_path: Path) -> None:
        """A pinned coding run with a missing build check is BLOCKED end to end."""
        results: dict[str, Any] = {}
        errors: list[str] = []
        gate_result: dict[str, object] = {
            "delivery_blocked": "Delivery blocked: no passing trw_build_check for task_type=coding",
            "missing_gate": "build_check",
            "blocked_task_type": "coding",
        }

        blocked = evaluate_delivery_gates(
            gate_result, cast("Any", results), errors, None, cast("Any", tmp_path), False, ""
        )

        assert blocked is True
        assert "delivery_blocked" in results
        assert results["success"] is False

    def test_a_refused_delivery_never_needs_the_gate(self, tmp_path: Path) -> None:
        """An invalid run_path returns blocked BEFORE any gate — enforcement-neutral."""
        from fastmcp import FastMCP

        from tests.conftest import get_tools_sync
        from trw_mcp.tools.ceremony import register_ceremony_tools

        server = FastMCP("test")
        register_ceremony_tools(server)
        deliver_fn = cast("Callable[..., dict[str, Any]]", get_tools_sync(server)["trw_deliver"].fn)
        project = _project(tmp_path)

        with patch("trw_mcp.state._paths.resolve_project_root", return_value=project):
            result = deliver_fn(run_path=str(tmp_path / "outside" / "run"))

        assert result["success"] is False
        assert "delivery_blocked" in result

    def test_the_offline_route_declares_itself_ungated(self) -> None:
        """``trw-mcp local deliver`` is not gated and must keep saying so."""
        source = (_REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "server" / "_subcommands_misc.py").read_text(
            encoding="utf-8"
        )

        assert "gate_evaluated: false" in source

    def test_unused_override_intent_is_recorded(self, tmp_path: Path) -> None:
        """The observation the hook's JSONL line used to carry survives as an event."""
        from tests._structlog_capture import capture_logs

        with capture_logs() as logs:
            evaluate_delivery_gates(
                {},
                cast("Any", {}),
                [],
                None,
                cast("Any", tmp_path),
                True,
                '{"failed_command": "x", "residual_risk": "y", "owner": "z", "expiry_iso": "2099-01-01"}',
            )

        assert any(entry.get("event") == "deliver_override_intent_unused" for entry in logs)


class TestServerGateDecisionMatrix:
    """FR03 — the pinned-run rules are unchanged."""

    def test_a_research_run_with_no_changes_stays_advisory(self) -> None:
        assert not resolve_deliver_gate_decision(
            mode="block_coding", task_type="research", build_check_missing=True, files_changed=0
        )

    def test_a_docs_run_with_no_changes_stays_advisory(self) -> None:
        assert not resolve_deliver_gate_decision(
            mode="block_coding", task_type="docs", build_check_missing=True, files_changed=0
        )

    def test_a_coding_run_without_a_build_blocks(self) -> None:
        assert resolve_deliver_gate_decision(
            mode="block_coding", task_type="coding", build_check_missing=True, files_changed=0
        )

    def test_an_uncomputable_change_count_blocks(self) -> None:
        assert resolve_deliver_gate_decision(
            mode="block_coding", task_type="research", build_check_missing=True, files_changed=None
        )

    def test_an_unreadable_mode_still_evaluates_the_change_clause(self) -> None:
        assert resolve_deliver_gate_decision(
            mode="advisory",
            task_type="research",
            build_check_missing=True,
            files_changed=None,
            mode_from_fallback=True,
        )

    def test_an_explicit_advisory_mode_never_blocks(self) -> None:
        assert not resolve_deliver_gate_decision(
            mode="advisory", task_type="coding", build_check_missing=True, files_changed=99
        )


class TestUnpinnedStartedSessionIsGated:
    """FR04 — an unpinned delivery that recorded a FAILURE is blocked."""

    @staticmethod
    def _state(tmp_path: Path, payload: object) -> Path:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        if payload is not None:
            (trw_dir / "context" / "ceremony-state.json").write_text(json.dumps(payload), encoding="utf-8")
        return trw_dir

    def test_unpinned_started_session_with_failed_evidence_blocks(self, tmp_path: Path) -> None:
        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {"sid": "failed"}})

        assert unpinned_build_failure_recorded(trw_dir, "sid") is True
        blocked, mode = resolve_unpinned_gate_decision()
        assert blocked is True
        assert mode in {"block_coding", "block_all"}

    def test_a_session_that_never_started_ceremony_is_untouched(self, tmp_path: Path) -> None:
        assert unpinned_build_failure_recorded(self._state(tmp_path, None), "sid") is False

    def test_a_missing_build_record_with_no_recorded_changes_stays_advisory(self, tmp_path: Path) -> None:
        """A docs-only unpinned session is advisory — the L-eWzn over-block must not return."""
        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {}})

        assert unpinned_build_failure_recorded(trw_dir, "sid") is False
        assert unpinned_session_changed_files(trw_dir, "sid") == 0
        assert resolve_unpinned_gate_decision(0)[0] is False

    def test_an_unpinned_session_that_changed_code_without_a_build_blocks(self, tmp_path: Path) -> None:
        """The CLAUDE.md / CONSTITUTION 1.a rule, restored for the unpinned path."""
        trw_dir = self._state(
            tmp_path,
            {"session_started": True, "session_build_results": {}, "files_modified_since_checkpoint": 4},
        )

        assert unpinned_session_changed_files(trw_dir, "sid") == 4
        blocked, mode = resolve_unpinned_gate_decision(4)
        assert blocked is True
        assert mode in {"block_coding", "block_all"}

    def test_session_scoped_file_modified_events_are_counted(self, tmp_path: Path) -> None:
        """Distinct paths from the session-scoped stream, attributed by session_id."""
        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {}})
        events = trw_dir / "context" / "session-events.jsonl"
        events.write_text(
            "\n".join(
                json.dumps(rec)
                for rec in (
                    {"event": "file_modified", "session_id": "sid", "file": "a.py"},
                    {"event": "file_modified", "session_id": "sid", "file": "./a.py"},
                    {"event": "file_modified", "session_id": "sid", "file": "b.py"},
                    {"event": "file_modified", "session_id": "other", "file": "c.py"},
                    {"event": "nudge_shown", "session_id": "sid"},
                )
            )
            + "\n",
            encoding="utf-8",
        )

        assert unpinned_session_changed_files(trw_dir, "sid") == 2

    def test_unscoped_reading_counts_every_recent_unpinned_record(self, tmp_path: Path) -> None:
        """P1-2 (release-verify 2026-09-17): a client with no shared session id reads
        the stream unscoped, so records keyed on the host's own id still count."""
        from datetime import datetime, timedelta, timezone

        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {}})
        events = trw_dir / "context" / "session-events.jsonl"
        now = datetime.now(timezone.utc)
        stamp = lambda dt: dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        events.write_text(
            "\n".join(
                json.dumps(rec)
                for rec in (
                    {"ts": stamp(now), "event": "file_modified", "session_id": "host-a", "file": "a.py"},
                    {
                        "ts": stamp(now + timedelta(seconds=1)),
                        "event": "file_modified",
                        "session_id": "host-b",
                        "file": "b.py",
                    },
                    {
                        "ts": stamp(now - timedelta(minutes=5)),
                        "event": "file_modified",
                        "session_id": "host-a",
                        "file": "old.py",
                    },
                    {"event": "file_modified", "session_id": "host-a", "file": "unstamped.py"},
                )
            )
            + "\n",
            encoding="utf-8",
        )

        assert unpinned_session_changed_files(trw_dir, "process-uuid") == 0
        # a.py, b.py and the unstamped record (unknown time counts, fail closed); old.py is excluded
        assert unpinned_session_changed_files(trw_dir, "process-uuid", unscoped_since=now - timedelta(seconds=1)) == 3

    def test_the_unscoped_window_opens_at_process_boot_not_at_first_deliver(self) -> None:
        """F1 (release-verify 2026-09-17): the stamp must predate the lazy gate import."""
        import trw_mcp
        from trw_mcp.tools import _delivery_event_checks

        assert _delivery_event_checks.PROCESS_STARTED_AT is trw_mcp.PROCESS_STARTED_AT
        init_source = (_REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "__init__.py").read_text(encoding="utf-8")
        assert "PROCESS_STARTED_AT = _datetime.now(_timezone.utc)" in init_source

    def test_a_non_claude_code_client_is_blocked_end_to_end(self, tmp_path: Path) -> None:
        """The hook keyed its record on the host session id; the server's key is its
        process UUID. The gate must still see the edit (P1-2)."""
        from datetime import datetime, timezone

        from trw_mcp.state._paths import get_session_id
        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {}})
        events = trw_dir / "context" / "session-events.jsonl"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        events.write_text(
            json.dumps({"ts": stamp, "event": "file_modified", "session_id": "opencode-host-7", "file": "x.py"}) + "\n",
            encoding="utf-8",
        )
        results: dict[str, Any] = {}

        with patch("trw_mcp.state._paths.resolve_pin_key", return_value=get_session_id()):
            blocked = evaluate_build_authority(cast("Any", results), [], None, trw_dir, False, "")

        assert blocked is True
        assert "unscoped" in str(results["delivery_blocked"])

    def test_an_unpinned_session_with_a_passing_build_is_allowed(self, tmp_path: Path) -> None:
        from datetime import datetime, timezone

        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        trw_dir = self._state(
            tmp_path,
            {
                "session_started": True,
                "session_build_results": {"sid": "passed"},
                "session_build_results_at": {"sid": datetime.now(timezone.utc).isoformat()},
                "files_modified_since_checkpoint": 9,
            },
        )

        with patch("trw_mcp.state._paths.resolve_pin_key", return_value="sid"):
            assert evaluate_build_authority(cast("Any", {}), [], None, trw_dir, False, "") is False

    def test_a_pass_recorded_before_the_last_edit_is_stale(self, tmp_path: Path) -> None:
        """P1-1 (release-verify 2026-09-17 run 4): a passing build is evidence for the
        tree as it stood then; an edit recorded after it re-opens the gate."""
        from datetime import datetime, timedelta, timezone

        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        passed_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        trw_dir = self._state(
            tmp_path,
            {
                "session_started": True,
                "session_build_results": {"sid": "passed"},
                "session_build_results_at": {"sid": passed_at.isoformat()},
            },
        )
        events = trw_dir / "context" / "session-events.jsonl"
        later = (passed_at + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        events.write_text(
            json.dumps({"ts": later, "event": "file_modified", "session_id": "sid", "file": "a.py"}) + "\n",
            encoding="utf-8",
        )
        assert unpinned_build_passed(trw_dir, "sid") is False
        results: dict[str, Any] = {}
        with patch("trw_mcp.state._paths.resolve_pin_key", return_value="sid"):
            assert evaluate_build_authority(cast("Any", results), [], None, trw_dir, False, "") is True
        assert "1 file(s)" in str(results["delivery_blocked"])

    def test_a_pass_recorded_after_the_last_edit_stands(self, tmp_path: Path) -> None:
        from datetime import datetime, timedelta, timezone

        passed_at = datetime.now(timezone.utc)
        trw_dir = self._state(
            tmp_path,
            {
                "session_started": True,
                "session_build_results": {"sid": "passed"},
                "session_build_results_at": {"sid": passed_at.isoformat()},
            },
        )
        events = trw_dir / "context" / "session-events.jsonl"
        earlier = (passed_at - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        events.write_text(
            json.dumps({"ts": earlier, "event": "file_modified", "session_id": "sid", "file": "a.py"}) + "\n",
            encoding="utf-8",
        )
        assert unpinned_build_passed(trw_dir, "sid") is True

    def test_an_undecodable_events_log_makes_a_pass_stale(self, tmp_path: Path) -> None:
        """A byte the reader cannot decode is not evidence of no edits (fail closed)."""
        from datetime import datetime, timezone

        trw_dir = self._state(
            tmp_path,
            {
                "session_started": True,
                "session_build_results": {"sid": "passed"},
                "session_build_results_at": {"sid": datetime.now(timezone.utc).isoformat()},
            },
        )
        (trw_dir / "context" / "session-events.jsonl").write_bytes(b'{"event":"file_modified"\xff\n')
        assert unpinned_build_passed(trw_dir, "sid") is False

    def test_a_pass_with_no_recorded_time_cannot_be_shown_current(self, tmp_path: Path) -> None:
        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {"sid": "passed"}})
        assert unpinned_build_passed(trw_dir, "sid") is False

    def test_an_unpinned_coding_session_without_a_build_is_blocked_end_to_end(self, tmp_path: Path) -> None:
        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        trw_dir = self._state(
            tmp_path,
            {"session_started": True, "session_build_results": {}, "files_modified_since_checkpoint": 6},
        )
        results: dict[str, Any] = {}
        errors: list[str] = []

        with patch("trw_mcp.state._paths.resolve_pin_key", return_value="sid"):
            blocked = evaluate_build_authority(cast("Any", results), errors, None, trw_dir, False, "")

        assert blocked is True
        assert "6 file(s)" in str(results["delivery_blocked"])
        assert results["missing_gate"] == "build_check"

    def test_an_unpinned_docs_session_is_not_blocked_end_to_end(self, tmp_path: Path) -> None:
        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {}})

        with patch("trw_mcp.state._paths.resolve_pin_key", return_value="sid"):
            assert evaluate_build_authority(cast("Any", {}), [], None, trw_dir, False, "") is False

    def test_the_gate_reads_the_key_the_writers_write(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Writer/reader agreement — the P1 that made FR04 inert when hooks are wired.

        ``trw_build_check`` persists its receipt under ``resolve_pin_key(ctx=ctx)``
        and the PostToolUse hook writes its unpinned change record under
        ``lib-trw.sh::trw_pin_key``. Both are the TRW_SESSION_ID-first ladder, so
        the gate must resolve the same way; reading the bare process UUID instead
        would miss every record precisely when hooks ARE wired.
        """
        from trw_mcp.state._paths import resolve_pin_key

        monkeypatch.setenv("TRW_SESSION_ID", "env-session-9")

        assert resolve_pin_key(None) == "env-session-9"

        gate_source = (
            _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "tools" / "_deliver_gate_selfcomputed.py"
        ).read_text(encoding="utf-8")
        assert "resolve_pin_key(None)" in gate_source
        # get_session_id() may appear only as the "is my key unshared?" comparison
        # (P1-2): the evidence readers themselves are keyed on resolve_pin_key.
        assert "session_id == get_session_id()" in gate_source
        assert "unpinned_build_failure_recorded(trw_dir, session_id)" in gate_source
        assert gate_source.count("get_session_id()") == 1

    def test_unreadable_session_change_evidence_fails_closed(self, tmp_path: Path) -> None:
        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {}})
        events = trw_dir / "context" / "session-events.jsonl"
        events.mkdir()  # a directory where a file is expected: present but unreadable

        assert unpinned_session_changed_files(trw_dir, "sid") is None
        assert resolve_unpinned_gate_decision(None)[0] is True

    def test_a_pending_result_is_not_a_failure(self, tmp_path: Path) -> None:
        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {"sid": "pending"}})

        assert unpinned_build_failure_recorded(trw_dir, "sid") is False

    def test_another_sessions_failure_is_not_this_sessions_evidence(self, tmp_path: Path) -> None:
        """The cross-session read is exactly what made the project-global file wrong."""
        trw_dir = self._state(
            tmp_path,
            {
                "session_started": True,
                "build_check_result": "failed",
                "session_build_results": {"other-session": "failed"},
            },
        )

        assert unpinned_build_failure_recorded(trw_dir, "sid") is False

    def test_unreadable_ceremony_state_fails_closed(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "context" / "ceremony-state.json").write_text("{not json", encoding="utf-8")

        assert unpinned_build_failure_recorded(trw_dir, "sid") is None

    def test_the_block_is_structured_and_overridable(self, tmp_path: Path) -> None:
        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        trw_dir = self._state(tmp_path, {"session_started": True, "session_build_results": {"sid": "failed"}})
        results: dict[str, Any] = {}
        record = json.dumps(
            {
                "failed_command": "pytest tests -q",
                "residual_risk": "known flake",
                "owner": "tyler",
                "expiry_iso": "2099-01-01",
            }
        )

        with patch("trw_mcp.state._paths.resolve_pin_key", return_value="sid"):
            # Non-vacuity: the same call WITHOUT a record must block, otherwise the
            # override assertion below would pass on a gate that never fired.
            assert evaluate_build_authority(cast("Any", {}), [], None, trw_dir, False, "") is True
            blocked = evaluate_build_authority(cast("Any", results), [], None, trw_dir, True, record)

        assert blocked is False, "a valid acceptable-failure record must release the block"


class TestLatestBuildFailureBlocks:
    """FR05 — the most recent build result decides, not the best one."""

    @staticmethod
    def _event(passed: bool) -> dict[str, object]:
        return {"event": "build_check_complete", "data": {"tests_passed": passed, "test_count": 3, "scope": "unit"}}

    def test_a_pass_followed_by_a_failure_blocks(self) -> None:
        assert latest_build_check_failed([self._event(True), self._event(False)]) is True

    def test_a_failure_followed_by_a_pass_does_not_block(self) -> None:
        assert latest_build_check_failed([self._event(False), self._event(True)]) is False

    def test_no_build_events_is_not_a_failure(self) -> None:
        assert latest_build_check_failed([{"event": "file_modified"}]) is False

    def test_an_unreadable_log_fails_closed(self) -> None:
        assert latest_build_check_failed(None) is None

    def test_the_gate_blocks_a_run_whose_latest_check_failed(self, tmp_path: Path) -> None:
        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        run_path = tmp_path / "runs" / "task" / "run-1"
        (run_path / "meta").mkdir(parents=True)
        (run_path / "meta" / "events.jsonl").write_text(
            "\n".join(json.dumps(e) for e in (self._event(True), self._event(False))) + "\n",
            encoding="utf-8",
        )
        results: dict[str, Any] = {}
        errors: list[str] = []

        blocked = evaluate_build_authority(cast("Any", results), errors, run_path, tmp_path / ".trw", False, "")

        assert blocked is True
        assert "most recent trw_build_check" in str(results["delivery_blocked"])
        assert results["missing_gate"] == "build_check"

    def test_the_gate_allows_a_run_whose_latest_check_passed(self, tmp_path: Path) -> None:
        from trw_mcp.tools._deliver_gate_selfcomputed import evaluate_build_authority

        run_path = tmp_path / "runs" / "task" / "run-2"
        (run_path / "meta").mkdir(parents=True)
        (run_path / "meta" / "events.jsonl").write_text(json.dumps(self._event(True)) + "\n", encoding="utf-8")
        results: dict[str, Any] = {}

        assert evaluate_build_authority(cast("Any", results), [], run_path, tmp_path / ".trw", False, "") is False
