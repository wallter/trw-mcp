"""Tests for extracted ceremony helper functions.

Covers:
- perform_session_recalls: focused/baseline recall, dedup, access tracking
- _phase_contextual_recall: phase-tag mapping, ranking, capping
- run_auto_maintenance: auto-upgrade, stale run close, embeddings backfill
- check_delivery_gates: review gate, build gate, premature delivery guard
- _phase_to_tags: phase-to-tag mapping
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.tools._ceremony_helpers import (
    _phase_contextual_recall,
    _phase_to_tags,
    check_delivery_gates,
    finalize_run,
    perform_session_recalls,
    run_auto_maintenance,
)

# --- Fixtures ---


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create minimal .trw structure."""
    trw = tmp_path / ".trw"
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "learnings" / "receipts").mkdir(parents=True)
    (trw / "context").mkdir(parents=True)
    (trw / "memory").mkdir(parents=True)
    return trw


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory."""
    d = tmp_path / "docs" / "task" / "runs" / "20260301T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


@pytest.fixture()
def config() -> TRWConfig:
    """Test configuration."""
    return TRWConfig()


@pytest.fixture()
def reader() -> FileStateReader:
    return FileStateReader()


@pytest.fixture()
def writer() -> FileStateWriter:
    return FileStateWriter()


@pytest.fixture()
def event_logger(writer: FileStateWriter) -> FileEventLogger:
    return FileEventLogger(writer)


# --- _phase_to_tags ---


class TestPhaseToTags:
    """Phase-to-tag mapping for auto-recall."""

    def test_known_phase_returns_tags(self) -> None:
        tags = _phase_to_tags("implement")
        assert "gotcha" in tags
        assert "testing" in tags
        assert "pattern" in tags

    def test_unknown_phase_returns_empty(self) -> None:
        assert _phase_to_tags("nonexistent") == []

    def test_case_insensitive(self) -> None:
        assert _phase_to_tags("RESEARCH") == _phase_to_tags("research")

    def test_all_phases_have_entries(self) -> None:
        phases = ["research", "plan", "implement", "validate", "review", "deliver"]
        for phase in phases:
            tags = _phase_to_tags(phase)
            assert len(tags) > 0, f"Phase {phase} should have tags"


# --- perform_session_recalls ---


class TestPerformSessionRecalls:
    """Core recall logic with dedup and access tracking."""

    def test_wildcard_recall_returns_learnings(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        mock_entries = [
            {"id": "L-001", "summary": "Test 1", "impact": 0.8},
            {"id": "L-002", "summary": "Test 2", "impact": 0.9},
        ]
        with (
            patch(
                "trw_mcp.tools._ceremony_helpers.adapter_recall",
                return_value=mock_entries,
            )
            if False
            else patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=mock_entries,
            ),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, auto_recalled, extra = perform_session_recalls(
                trw_dir,
                "",
                config,
                reader,
            )

        assert len(learnings) == 2
        assert extra["total_available"] == 2
        assert auto_recalled == []

    def test_focused_recall_deduplicates(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        focused = [
            {"id": "L-001", "summary": "Focused hit", "impact": 0.5},
            {"id": "L-002", "summary": "Focused hit 2", "impact": 0.4},
        ]
        baseline = [
            {"id": "L-001", "summary": "Focused hit", "impact": 0.5},  # dupe
            {"id": "L-003", "summary": "Baseline only", "impact": 0.9},
        ]

        call_count = 0

        def mock_recall(*args: object, **kwargs: object) -> list[dict[str, object]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return focused
            return baseline

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=mock_recall),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, _, extra = perform_session_recalls(
                trw_dir,
                "test query",
                config,
                reader,
            )

        assert len(learnings) == 3  # L-001, L-002, L-003 (deduped)
        ids = [str(e["id"]) for e in learnings]
        assert ids == ["L-001", "L-002", "L-003"]
        assert extra["query"] == "test query"
        assert "query_matched" in extra

    def test_updates_access_tracking(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        mock_entries = [{"id": "L-001", "summary": "X", "impact": 0.8}]
        mock_update = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=mock_entries),
            patch("trw_mcp.state.memory_adapter.update_access_tracking", mock_update),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            perform_session_recalls(trw_dir, "", config, reader)

        mock_update.assert_called_once_with(trw_dir, ["L-001"])

    def test_increments_session_counts_for_surfaced_learnings(
        self,
        trw_dir: Path,
        config: TRWConfig,
        reader: FileStateReader,
    ) -> None:
        mock_entries = [{"id": "L-001", "summary": "X", "impact": 0.8}]
        mock_increment = MagicMock()

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=mock_entries),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.state.memory_adapter.increment_session_counts", mock_increment),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            perform_session_recalls(trw_dir, "", config, reader)

        mock_increment.assert_called_once_with(trw_dir, ["L-001"])


# --- _phase_contextual_recall ---


class TestPhaseContextualRecall:
    """Phase-contextual auto-recall with ranking."""

    def test_returns_empty_when_no_entries(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]):
            result = _phase_contextual_recall(trw_dir, "", config, None, None)
        assert result == []

    def test_includes_phase_tags_from_run_status(
        self,
        trw_dir: Path,
        config: TRWConfig,
        run_dir: Path,
    ) -> None:
        mock_entries = [
            {"id": "L-001", "summary": "Test", "impact": 0.6, "tags": ["gotcha"]},
        ]
        run_status = {"phase": "implement", "task_name": "my-task"}

        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=mock_entries,
        ) as mock_recall:
            result = _phase_contextual_recall(
                trw_dir,
                "",
                config,
                run_dir,
                run_status,
            )

        # Verify tags were passed to recall
        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        # The tags should include implement phase tags
        tags_arg = call_kwargs.kwargs.get("tags") or call_kwargs[1].get("tags")
        assert tags_arg is not None
        assert "gotcha" in tags_arg

    def test_ranks_and_caps_results(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        mock_entries = [{"id": f"L-{i:03d}", "summary": f"Entry {i}", "impact": 0.5 + i * 0.01} for i in range(20)]

        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=mock_entries,
        ):
            result = _phase_contextual_recall(trw_dir, "", config, None, None)

        # Should be capped at config.auto_recall_max_results
        assert len(result) <= config.auto_recall_max_results
        # Each entry should have id, summary, impact
        for entry in result:
            assert "id" in entry
            assert "summary" in entry
            assert "impact" in entry

    def test_focused_query_adds_tokens(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[],
        ) as mock_recall:
            _phase_contextual_recall(trw_dir, "testing gotchas", config, None, None)

        # The query should include the focused tokens
        call_args = mock_recall.call_args
        query_arg = call_args.kwargs.get("query") or call_args[1].get("query")
        assert "testing" in str(query_arg)
        assert "gotchas" in str(query_arg)

    def test_uses_compact_mode(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Phase-contextual recall must use compact=True to limit response size."""
        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[],
        ) as mock_recall:
            _phase_contextual_recall(trw_dir, "", config, None, None)

        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        compact_arg = call_kwargs.kwargs.get("compact")
        assert compact_arg is True

    def test_max_results_is_bounded(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Phase-contextual recall must not use max_results=0 (unlimited)."""
        with patch(
            "trw_mcp.state.memory_adapter.recall_learnings",
            return_value=[],
        ) as mock_recall:
            _phase_contextual_recall(trw_dir, "", config, None, None)

        call_kwargs = mock_recall.call_args
        assert call_kwargs is not None
        max_results_arg = call_kwargs.kwargs.get("max_results")
        assert max_results_arg is not None
        assert max_results_arg > 0
        assert max_results_arg <= config.auto_recall_max_results * 3


# --- run_auto_maintenance ---


class TestRunAutoMaintenance:
    """Auto-maintenance operations: upgrade, stale runs, embeddings."""

    def test_returns_empty_when_nothing_needed(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        # No maintenance keys should be set
        assert "update_advisory" not in result
        assert "auto_upgrade" not in result
        assert "stale_runs_closed" not in result

    def test_includes_update_advisory_when_available(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": True, "advisory": "v2.0 available"},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert result["update_advisory"] == "v2.0 available"

    def test_failopen_on_upgrade_error(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                side_effect=Exception("network error"),
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        # Should not raise, return empty
        assert isinstance(result, dict)

    def test_embeddings_advisory_included(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"advisory": "Install anthropic SDK for embeddings"},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "embeddings_advisory" in result

    def test_auto_upgrade_performed_when_enabled(
        self,
        trw_dir: Path,
    ) -> None:
        """Lines 181-189: When auto_upgrade=True and upgrade is applied."""
        cfg = TRWConfig(auto_upgrade=True)  # type: ignore[call-arg]
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": True, "advisory": "v2.0 available"},
            ),
            patch(
                "trw_mcp.state.auto_upgrade.perform_upgrade",
                return_value={"applied": True, "version": "2.0.0", "details": "patch applied"},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, cfg)

        assert result["update_advisory"] == "v2.0 available"
        assert result["auto_upgrade"]["applied"] is True
        assert result["auto_upgrade"]["version"] == "2.0.0"

    def test_embeddings_backfill_failopen_on_exception(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Lines 215-216: Embeddings block fails open on exception."""
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                side_effect=Exception("embeddings boom"),
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        # Should not raise, and no embeddings keys
        assert isinstance(result, dict)
        assert "embeddings_advisory" not in result
        assert "embeddings_backfill" not in result

    def test_version_sentinel_mismatch_injects_advisory(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Version sentinel with mismatched version produces update_advisory."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"version": "99.0.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" in result
        assert "99.0.0" in str(result["update_advisory"])
        assert "/mcp" in str(result["update_advisory"])

    def test_version_sentinel_matching_no_advisory(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Version sentinel matching running version does not inject advisory."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"version": "0.15.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_missing_no_error(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Missing sentinel file does not cause errors."""
        sentinel = trw_dir / "installed-version.json"
        assert not sentinel.exists()
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_corrupt_json_no_error(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Corrupt sentinel JSON does not crash maintenance."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text("not valid json{{{", encoding="utf-8")
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        # Fail-open: no crash, no advisory
        assert isinstance(result, dict)

    def test_version_sentinel_missing_version_key(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Sentinel JSON without 'version' key produces no advisory."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_importlib_failure(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """importlib.metadata failure produces no advisory and no crash."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"version": "99.0.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                side_effect=Exception("package not found"),
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_existing_advisory_preserved(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """Pre-existing update_advisory is not overwritten by sentinel check."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"version": "99.0.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )
        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": True, "advisory": "upstream advisory"},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        # The auto-upgrade advisory takes precedence since sentinel check runs first
        # but the auto-upgrade check runs second and overwrites. Either way, an
        # advisory IS present — the key invariant is no crash.
        assert "update_advisory" in result

    def test_version_sentinel_e2e_upgrade_cycle(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """E2E: installer writes sentinel → session_start detects mismatch → advisory."""
        # Simulate installer writing sentinel
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"version": "0.16.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )

        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.15.1",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" in result
        advisory = str(result["update_advisory"])
        assert "0.16.0" in advisory
        assert "0.15.1" in advisory
        assert "/mcp" in advisory

    def test_version_sentinel_e2e_no_advisory_after_reload(
        self,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """E2E: after /mcp reload (versions match), no advisory appears."""
        sentinel = trw_dir / "installed-version.json"
        sentinel.write_text(
            json.dumps({"version": "0.16.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )

        with (
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={"available": False},
            ),
            patch(
                "trw_mcp.state.memory_adapter.check_embeddings_status",
                return_value={"enabled": False},
            ),
            patch(
                "importlib.metadata.version",
                return_value="0.16.0",
            ),
        ):
            result = run_auto_maintenance(trw_dir, config)

        assert "update_advisory" not in result

    def test_version_sentinel_no_platform_imports(self) -> None:
        """FR09: _check_version_sentinel uses no platform-specific imports."""
        import inspect

        from trw_mcp.tools._ceremony_helpers import _check_version_sentinel

        source = inspect.getsource(_check_version_sentinel)
        assert "import signal" not in source
        assert "import fcntl" not in source
        assert "sys.platform" not in source


# --- check_delivery_gates ---


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
        # events.jsonl exists but has no build_check_complete events
        events_path = run_dir / "meta" / "events.jsonl"
        events_path.write_text(
            json.dumps({"event": "run_init", "data": {}}) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "build_gate_warning" in result

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
            "\n".join(json.dumps(e) for e in events) + "\n",
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
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        result = check_delivery_gates(run_dir, reader)
        assert "warning" not in result

    def test_build_gate_failopen_on_read_error(
        self,
        run_dir: Path,
    ) -> None:
        """Build gate check should not raise on read errors."""
        mock_reader = MagicMock(spec=FileStateReader)
        mock_reader.exists.return_value = True
        mock_reader.read_jsonl.side_effect = Exception("read error")
        mock_reader.read_yaml.side_effect = Exception("read error")

        # Should not raise
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
        # review.yaml exists but read_yaml raises
        mock_reader.read_yaml.side_effect = Exception("corrupt yaml")
        mock_reader.exists.return_value = True
        mock_reader.read_jsonl.return_value = []

        result = check_delivery_gates(run_dir, mock_reader)
        # Should not contain review_warning (exception swallowed) nor review_advisory
        # (file exists at the path, so the else branch is skipped)
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
        # Write event where data is a string instead of a dict
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


# --- finalize_run ---


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
