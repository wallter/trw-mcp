"""Tests for ceremony delivery gates and finalize helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools._ceremony_helpers import check_delivery_gates, finalize_run
from ._ceremony_helpers_support import run_dir, trw_dir  # noqa: F401

from ._ceremony_helpers_support import run_dir, trw_dir  # noqa: F401

from ._ceremony_helpers_support import run_dir, trw_dir  # noqa: F401

from ._ceremony_helpers_support import run_dir, trw_dir  # noqa: F401

pytest_plugins = ("tests._ceremony_helpers_support",)


class TestCheckDeliveryGates:
    """Review/build gates and premature delivery guard."""

    def test_returns_empty_when_no_run(self, reader: FileStateReader) -> None:
        result = check_delivery_gates(None, reader)
        assert result == {}

    def test_review_advisory_when_no_review_file(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        result = check_delivery_gates(run_dir, reader)
        assert "review_advisory" in result
        assert "No trw_review" in str(result["review_advisory"])

    def test_review_warning_on_critical_findings(
        self,
        run_dir: Path,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        review_path = run_dir / "meta" / "review.yaml"
        writer.write_yaml(
            review_path,
            {
                "verdict": "block",
                "critical_count": 3,
            },
        )

        result = check_delivery_gates(run_dir, reader)
        assert "review_warning" in result
        assert "3 critical" in str(result["review_warning"])

    def test_no_review_warning_on_pass_verdict(
        self,
        run_dir: Path,
        reader: FileStateReader,
        writer: FileStateWriter,
    ) -> None:
        review_path = run_dir / "meta" / "review.yaml"
        writer.write_yaml(
            review_path,
            {
                "verdict": "pass",
                "critical_count": 0,
            },
        )

        result = check_delivery_gates(run_dir, reader)
        assert "review_warning" not in result

    def test_build_gate_warning_when_no_build_check(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps({"event": "run_init", "data": {}}) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "build_gate_warning" in result

    def test_build_gate_warning_on_empty_events_jsonl(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """Empty/truncated events.jsonl = no build evidence; the gate must warn
        (A-P1-07 symmetry with 'events present but no passing build'), not silently
        pass as it did pre-fix when _check_build_and_work_events returned (None, None)."""
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text("", encoding="utf-8")

        result = check_delivery_gates(run_dir, reader)
        assert "build_gate_warning" in result
        assert "No events found" in str(result["build_gate_warning"])

    def test_no_build_gate_warning_when_build_passed(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps(
                {
                    "event": "build_check_complete",
                    "data": {"tests_passed": True},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "build_gate_warning" not in result

    def test_no_build_gate_warning_when_flat_build_event_passed(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """Flat FileEventLogger build_check_complete events satisfy delivery."""
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps(
                {
                    "event": "build_check_complete",
                    "tests_passed": True,
                    "static_checks_clean": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "build_gate_warning" not in result

    def test_build_gate_warning_when_static_checks_failed(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """tests_passed=True is insufficient when static checks are reported failed."""
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps(
                {
                    "event": "build_check_complete",
                    "data": {"tests_passed": True, "static_checks_clean": False},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "build_gate_warning" in result

    def test_premature_delivery_warning_on_ceremony_only_events(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"event": "run_init", "data": {}},
            {"event": "checkpoint", "data": {}},
        ]
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "warning" in result
        assert "Premature delivery" in str(result["warning"])

    def test_no_premature_warning_with_work_events(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"event": "run_init", "data": {}},
            {"event": "phase_enter", "data": {"phase": "implement"}},
            {"event": "shard_complete", "data": {}},
        ]
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "warning" not in result

    def test_premature_delivery_warning_with_session_start_event(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """session_start is a ceremony bootstrap event, not work.

        A run that logs only run_init + session_start before deliver has done no
        real work and MUST trip the premature-delivery guard. Regression for the
        _CEREMONY_ONLY_EVENTS gap that let the (always-emitted) session_start
        event count as work and silently defeat the guard for every real run.
        """
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"event": "run_init", "data": {}},
            {"event": "session_start", "data": {"run_detected": True}},
        ]
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "warning" in result
        assert "Premature delivery" in str(result["warning"])

    def test_build_gate_failopen_on_read_error(
        self,
        run_dir: Path,
    ) -> None:
        """Build gate check should not raise on read errors."""
        mock_reader = MagicMock(spec=FileStateReader)
        mock_reader.exists.return_value = True
        mock_reader.read_jsonl.side_effect = Exception("read error")
        mock_reader.read_yaml.side_effect = Exception("read error")

        result = check_delivery_gates(run_dir, mock_reader)
        assert isinstance(result, dict)

    def test_review_yaml_read_failopen_on_exception(
        self,
        run_dir: Path,
    ) -> None:
        """Lines 253-254: Corrupt review.yaml fails open without raising."""
        review_path = run_dir / "meta" / "review.yaml"
        review_path.write_text("{{invalid yaml: [", encoding="utf-8")

        mock_reader = MagicMock(spec=FileStateReader)
        mock_reader.read_yaml.side_effect = Exception("corrupt yaml")
        mock_reader.exists.return_value = True
        mock_reader.read_jsonl.return_value = []

        result = check_delivery_gates(run_dir, mock_reader)
        assert "review_warning" not in result

    def test_untracked_warning_when_git_reports_files(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """Untracked src/test files produce a warning."""
        git_output = "src/trw_mcp/new_module.py\ntests/test_new.py\nREADME.md\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=git_output,
            )
            result = check_delivery_gates(run_dir, reader)
        assert "untracked_warning" in result
        assert "2 untracked" in str(result["untracked_warning"])

    def test_no_untracked_warning_when_clean(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """No warning when git reports no untracked source files."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="README.md\n")
            result = check_delivery_gates(run_dir, reader)
        assert "untracked_warning" not in result

    def test_untracked_check_failopen_on_git_error(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """Git failure doesn't block delivery."""
        with patch("subprocess.run", side_effect=Exception("git not found")):
            result = check_delivery_gates(run_dir, reader)
        assert "untracked_warning" not in result

    def test_build_passed_false_when_data_not_dict(
        self,
        run_dir: Path,
        reader: FileStateReader,
    ) -> None:
        """Line 273: _build_passed returns False when event data is not a dict."""
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps(
                {
                    "event": "build_check_complete",
                    "data": "not-a-dict",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "build_gate_warning" in result


class TestFinalizeRun:
    """Finalize run helper (currently no-op placeholder)."""

    def test_returns_empty_dict(
        self,
        run_dir: Path,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
        writer: FileStateWriter,
        event_logger: FileEventLogger,
    ) -> None:
        result = finalize_run(run_dir, trw_dir, config, reader, writer, event_logger)
        assert result == {}

    def test_returns_empty_dict_with_no_run(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
        writer: FileStateWriter,
        event_logger: FileEventLogger,
    ) -> None:
        result = finalize_run(None, trw_dir, config, reader, writer, event_logger)
        assert result == {}
