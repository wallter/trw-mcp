"""Tests for PRD-FIX-043 — error logging and fail-open patterns.

Covers missing FR tests identified in Sprint 64 audit:
- FR03: flush failure preserves queue (TelemetryClient)
- FR07: _mark_run_complete logs warning on write failure (ceremony.py)
- FR02: maintenance except blocks use unique event names (_ceremony_helpers.py)
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

from structlog.testing import capture_logs

from trw_mcp.telemetry.client import TelemetryClient
from trw_mcp.telemetry.models import TelemetryEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INSTALL_ID = "abcd1234abcd1234"
_FW_VERSION = "v21.0_TRW"


def _base_event(**kwargs: object) -> TelemetryEvent:
    return TelemetryEvent(
        installation_id=_INSTALL_ID,
        framework_version=_FW_VERSION,
        event_type="test_event",
        **kwargs,  # type: ignore[arg-type]
    )


# ===========================================================================
# FR03 — flush failure preserves queue for retry
# ===========================================================================


class TestFlushFailurePreservesQueue:
    """PRD-FIX-043 FR03: When flush fails to write an event, that event
    remains in the queue for retry on the next flush() call."""

    def test_flush_failure_preserves_queue(self, tmp_path: Path) -> None:
        """When all writes fail, every event stays in the queue."""
        output = tmp_path / "telemetry.jsonl"
        mock_writer = MagicMock()
        mock_writer.append_jsonl.side_effect = OSError("disk full")

        client = TelemetryClient(
            enabled=True,
            output_path=output,
            writer=mock_writer,
        )
        # Enqueue 3 events
        for _ in range(3):
            client.record_event(_base_event())

        assert client.queue_size() == 3

        # First flush — all writes fail
        written = client.flush()
        assert written == 0
        # Events must remain in queue for retry
        assert client.queue_size() == 3

        # Second flush with working writer — events should succeed
        mock_writer.append_jsonl.side_effect = None
        written2 = client.flush()
        assert written2 == 3
        assert client.queue_size() == 0

    def test_partial_flush_failure_preserves_only_failed(self, tmp_path: Path) -> None:
        """When some writes fail, only failed events stay in the queue."""
        output = tmp_path / "logs" / "partial.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        mock_writer = MagicMock()
        call_count = {"n": 0}

        def _side_effect(path: Path, record: dict[str, object]) -> None:
            call_count["n"] += 1
            # Fail on 2nd event only
            if call_count["n"] == 2:
                raise OSError("transient error")

        mock_writer.append_jsonl.side_effect = _side_effect

        client = TelemetryClient(
            enabled=True,
            output_path=output,
            writer=mock_writer,
        )
        for _ in range(3):
            client.record_event(_base_event())

        written = client.flush()
        assert written == 2  # events 1 and 3 succeeded
        assert client.queue_size() == 1  # event 2 failed, kept for retry


# ===========================================================================
# FR07 — _mark_run_complete logs warning on write failure
# ===========================================================================


class TestMarkRunCompleteFailureLogsWarning:
    """PRD-FIX-043 FR07: _mark_run_complete logs at warning level when
    the write to run.yaml fails."""

    def test_mark_run_complete_failure_logs_warning(self, tmp_path: Path) -> None:
        """When write_yaml raises, a warning log with 'mark_run_complete_failed'
        is emitted and the function does not raise."""
        from trw_mcp.tools.ceremony import _mark_run_complete

        # Create the run directory with a valid run.yaml
        meta_dir = tmp_path / "run-001" / "meta"
        meta_dir.mkdir(parents=True)
        run_yaml = meta_dir / "run.yaml"
        run_yaml.write_text("status: active\n", encoding="utf-8")

        # Patch the writer to raise on write_yaml
        with (
            patch(
                "trw_mcp.tools.ceremony.FileStateWriter.write_yaml",
                side_effect=OSError("permission denied"),
            ),
            capture_logs() as cap_logs,
        ):
            # Should NOT raise
            _mark_run_complete(tmp_path / "run-001")

        # Verify warning was logged with the correct event name
        warning_events = [
            log
            for log in cap_logs
            if log.get("log_level") == "warning" and "mark_run_complete_failed" in str(log.get("event", ""))
        ]
        assert len(warning_events) >= 1, f"Expected a warning log with 'mark_run_complete_failed', got: {cap_logs}"


# ===========================================================================
# FR02 — maintenance except blocks use unique event names
# ===========================================================================


class TestMaintenanceStepsHaveUniqueEventNames:
    """PRD-FIX-043 FR02: All ceremony helper maintenance except blocks
    use unique event names so log filtering can distinguish failures."""

    def test_maintenance_steps_have_unique_event_names(self) -> None:
        """Parse _ceremony_helpers.py AST and extract all string literals
        passed as the first arg to logger.warning() inside except blocks.
        Assert they are all unique."""
        import trw_mcp.tools._ceremony_helpers as mod

        source = inspect.getsource(mod)
        tree = ast.parse(source)

        event_names: list[str] = []

        for node in ast.walk(tree):
            # Look for except handlers
            if not isinstance(node, ast.ExceptHandler):
                continue
            # Walk the except body for logger.warning(...) calls
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                func = child.func
                # Match logger.warning(...)
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "warning"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "logger"
                    and child.args
                    and isinstance(child.args[0], ast.Constant)
                    and isinstance(child.args[0].value, str)
                ):
                    event_names.append(child.args[0].value)

        # Must have found some event names (sanity check)
        assert len(event_names) >= 3, f"Expected at least 3 unique event names in except blocks, found: {event_names}"

        # All must be unique
        seen: set[str] = set()
        duplicates: list[str] = []
        for name in event_names:
            if name in seen:
                duplicates.append(name)
            seen.add(name)

        assert not duplicates, f"Duplicate event names in except blocks: {duplicates}. All event names: {event_names}"
