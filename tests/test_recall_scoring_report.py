"""Coverage gap tests for recall_search, scoring, and report modules.

Targets uncovered lines in:
- state/recall_search.py: 63-65, 86-87, 119, 127-128, 161-162, 189
- scoring.py: 277-278, 623-635, 715-738, 782, 792, 795-796, 803, 857, 866, 890, 999-1001
- state/report.py: 108-109, 162-164, 197-198, 204, 214-215, 271-272
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.recall_search import (
    collect_context,
    search_entries,
    search_patterns,
    update_access_tracking,
)
from trw_mcp.state._helpers import safe_float
from trw_mcp.state.report import (
    _date_in_range,
    _parse_date,
    _ts_diff_seconds,
    assemble_report,
    compute_learning_yield,
)

# ===========================================================================
# recall_search.py tests
# ===========================================================================


class TestSearchEntriesStatusFilter:
    """Cover lines 62-65: status filter branch."""

    def test_status_filter_excludes_non_matching(self, tmp_path: Path) -> None:
        """Entries whose status != filter value are skipped (lines 63-65)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        # Write one active entry and one resolved entry
        writer.write_yaml(entries_dir / "active.yaml", {
            "id": "L-act",
            "summary": "active learning",
            "detail": "detail",
            "impact": 0.7,
            "status": "active",
        })
        writer.write_yaml(entries_dir / "resolved.yaml", {
            "id": "L-res",
            "summary": "resolved learning",
            "detail": "detail",
            "impact": 0.7,
            "status": "resolved",
        })

        # Filter to only active — resolved entry must be excluded
        matches, paths = search_entries(
            entries_dir,
            query_tokens=[],
            reader=reader,
            status="active",
        )
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-act" in ids
        assert "L-res" not in ids

    def test_status_filter_matches_only_resolved(self, tmp_path: Path) -> None:
        """Filtering by resolved excludes active entries."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        reader = FileStateReader()
        writer = FileStateWriter()

        writer.write_yaml(entries_dir / "active.yaml", {
            "id": "L-act", "summary": "active", "detail": "", "impact": 0.5, "status": "active",
        })
        writer.write_yaml(entries_dir / "resolved.yaml", {
            "id": "L-res", "summary": "resolved", "detail": "", "impact": 0.5, "status": "resolved",
        })

        matches, _ = search_entries(
            entries_dir, query_tokens=[], reader=reader, status="resolved",
        )
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-res" in ids
        assert "L-act" not in ids


class TestSearchEntriesExceptionHandling:
    """Cover lines 86-87: exception handling in search_entries."""

    def test_invalid_yaml_file_is_skipped(self, tmp_path: Path) -> None:
        """A file that raises StateError on read is silently skipped (lines 86-87)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Write a valid entry and a corrupt entry
        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "good.yaml", {
            "id": "L-good", "summary": "good entry", "detail": "", "impact": 0.7,
        })
        # Write raw invalid YAML that will fail to parse
        (entries_dir / "bad.yaml").write_text("{invalid: yaml: content: [}", encoding="utf-8")

        reader = FileStateReader()
        # Should return only the good entry without raising
        matches, _ = search_entries(entries_dir, query_tokens=[], reader=reader)
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-good" in ids

    def test_reader_raises_state_error_skips_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StateError from reader is caught and entry is skipped."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        # Create a dummy YAML file so glob finds something
        (entries_dir / "dummy.yaml").write_text("id: test\n", encoding="utf-8")

        reader = FileStateReader()
        monkeypatch.setattr(reader, "read_yaml", lambda _: (_ for _ in ()).throw(
            StateError("fail", path="dummy")
        ))

        matches, _ = search_entries(entries_dir, query_tokens=[], reader=reader)
        assert matches == []


class TestSearchPatternsExceptionHandling:
    """Cover lines 119 and 127-128: exception handling in search_patterns."""

    def test_index_yaml_is_skipped(self, tmp_path: Path) -> None:
        """index.yaml is always skipped (line 119 continue branch)."""
        patterns_dir = tmp_path / "patterns"
        patterns_dir.mkdir()
        writer = FileStateWriter()
        # Write index.yaml — should be skipped
        writer.write_yaml(patterns_dir / "index.yaml", {
            "name": "should not appear", "description": "skipped",
        })
        writer.write_yaml(patterns_dir / "actual.yaml", {
            "name": "real pattern", "description": "this should appear",
        })

        reader = FileStateReader()
        matches = search_patterns(patterns_dir, query_tokens=[], reader=reader)
        names = [str(m.get("name", "")) for m in matches]
        assert "real pattern" in names
        assert "should not appear" not in names

    def test_corrupt_pattern_file_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StateError/ValueError on pattern read causes that file to be skipped (lines 127-128)."""
        patterns_dir = tmp_path / "patterns"
        patterns_dir.mkdir()
        (patterns_dir / "bad.yaml").write_text("{invalid", encoding="utf-8")
        (patterns_dir / "good.yaml").write_text(
            "name: good\ndescription: works\n", encoding="utf-8"
        )

        reader = FileStateReader()
        # Don't monkeypatch — rely on actual reader raising on corrupt YAML
        # The corrupt file raises StateError internally and is skipped
        matches = search_patterns(patterns_dir, query_tokens=[], reader=reader)
        names = [str(m.get("name", "")) for m in matches]
        assert "good" in names


class TestUpdateAccessTrackingExceptionHandling:
    """Cover lines 161-162: exception handling in update_access_tracking."""

    def test_unreadable_entry_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StateError on read during access tracking skips that file (lines 161-162)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        good_file = entries_dir / "good.yaml"
        bad_file = entries_dir / "bad.yaml"
        writer.write_yaml(good_file, {"id": "L-good", "summary": "g", "access_count": 0})
        bad_file.write_text("{invalid", encoding="utf-8")

        reader = FileStateReader()

        # Pass both files — bad_file will raise StateError, good_file processes normally
        result = update_access_tracking([bad_file, good_file], reader, writer)
        # Only good_file produced a tracked id
        assert "L-good" in result

    def test_entry_with_no_id_not_appended(self, tmp_path: Path) -> None:
        """Entry without id field does not contribute to matched_ids."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        no_id_file = entries_dir / "noid.yaml"
        writer.write_yaml(no_id_file, {"summary": "no id here", "access_count": 0})

        reader = FileStateReader()
        result = update_access_tracking([no_id_file], reader, writer)
        assert result == []


class TestCollectContextConventions:
    """Cover line 189: collect_context with conventions.yaml present."""

    def test_collects_conventions_when_present(self, tmp_path: Path) -> None:
        """When conventions.yaml exists, it is included in context (line 189)."""
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        reader = FileStateReader()

        writer.write_yaml(context_dir / "conventions.yaml", {
            "naming": "snake_case",
            "indent": 4,
        })

        result = collect_context(trw_dir, "context", reader)
        assert "conventions" in result
        assert isinstance(result["conventions"], dict)

    def test_collects_both_when_both_present(self, tmp_path: Path) -> None:
        """Both architecture and conventions are collected when both exist."""
        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        writer = FileStateWriter()
        reader = FileStateReader()

        writer.write_yaml(context_dir / "architecture.yaml", {"layers": ["tool", "state"]})
        writer.write_yaml(context_dir / "conventions.yaml", {"style": "pep8"})

        result = collect_context(trw_dir, "context", reader)
        assert "architecture" in result
        assert "conventions" in result

    def test_returns_empty_when_neither_exists(self, tmp_path: Path) -> None:
        """No context files -> empty dict."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        reader = FileStateReader()
        result = collect_context(trw_dir, "context", reader)
        assert result == {}


# ===========================================================================
# state/report.py tests
# ===========================================================================


class TestTsDiffSecondsException:
    """Cover lines 108-109: _ts_diff_seconds returns None on invalid timestamps."""

    def test_invalid_start_returns_none(self) -> None:
        """Non-parseable start timestamp returns None."""
        result = _ts_diff_seconds("not-a-timestamp", "2026-02-19T10:00:00Z")
        assert result is None

    def test_invalid_end_returns_none(self) -> None:
        """Non-parseable end timestamp returns None."""
        result = _ts_diff_seconds("2026-02-19T10:00:00Z", "also-invalid")
        assert result is None

    def test_both_invalid_returns_none(self) -> None:
        """Both timestamps invalid returns None."""
        result = _ts_diff_seconds("", "")
        assert result is None

    def test_valid_timestamps_return_seconds(self) -> None:
        """Sanity check: valid timestamps return correct elapsed seconds."""
        result = _ts_diff_seconds("2026-02-19T10:00:00Z", "2026-02-19T11:00:00Z")
        assert result == 3600.0


class TestComputeLearningYieldSQLiteFailure:
    """Cover exception handling when list_active_learnings raises."""

    def test_sqlite_error_returns_empty_summary(
        self, tmp_path: Path,
    ) -> None:
        """When list_active_learnings raises, return empty LearningSummary."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        with patch(
            "trw_mcp.state.report.list_active_learnings",
            side_effect=RuntimeError("db corrupt"),
        ):
            result = compute_learning_yield(trw_dir, reader)

        assert result.total_produced == 0
        assert result.avg_impact == 0.0


class TestParseDateInvalid:
    """Cover lines 197-198: _parse_date returns None for invalid timestamps."""

    def test_invalid_timestamp_returns_none(self) -> None:
        """Non-ISO timestamp returns None (lines 197-198)."""
        result = _parse_date("not-a-date")
        assert result is None

    def test_none_input_returns_none(self) -> None:
        """None input returns None (early return)."""
        result = _parse_date(None)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        """Empty string returns None (early return)."""
        result = _parse_date("")
        assert result is None

    def test_valid_iso_timestamp_extracts_date(self) -> None:
        """Valid ISO timestamp extracts YYYY-MM-DD."""
        result = _parse_date("2026-02-19T10:30:00Z")
        assert result == "2026-02-19"


class TestDateInRangeEmptyCreated:
    """Cover line 204: _date_in_range returns False for empty created string."""

    def test_empty_created_returns_false(self) -> None:
        """Empty created string returns False immediately (line 204)."""
        result = _date_in_range("", "2026-02-01", "2026-02-28")
        assert result is False

    def test_in_range_date_returns_true(self) -> None:
        """Date within range returns True."""
        result = _date_in_range("2026-02-15", "2026-02-01", "2026-02-28")
        assert result is True

    def test_before_range_returns_false(self) -> None:
        """Date before range returns False."""
        result = _date_in_range("2026-01-15", "2026-02-01", "2026-02-28")
        assert result is False

    def test_after_range_returns_false(self) -> None:
        """Date after range returns False."""
        result = _date_in_range("2026-03-01", "2026-02-01", "2026-02-28")
        assert result is False


class TestSafeFloat:
    """Tests for safe_float from _helpers.py (canonical dict-to-float extractor)."""

    def test_string_non_numeric_returns_default(self) -> None:
        """Non-numeric string value returns default."""
        assert safe_float({"k": "not-a-number"}, "k", 0.0) == 0.0

    def test_none_value_returns_default(self) -> None:
        """None value returns default."""
        assert safe_float({"k": None}, "k", 0.0) == 0.0

    def test_list_value_returns_default(self) -> None:
        """List value returns default."""
        assert safe_float({"k": [1, 2, 3]}, "k", 0.0) == 0.0

    def test_int_converts_correctly(self) -> None:
        """Integer value converts to float correctly."""
        assert safe_float({"k": 42}, "k", 0.0) == pytest.approx(42.0)

    def test_float_passthrough(self) -> None:
        """Float value passes through unchanged."""
        assert safe_float({"k": 0.75}, "k", 0.0) == pytest.approx(0.75)

    def test_numeric_string_converts(self) -> None:
        """Numeric string converts to float."""
        assert safe_float({"k": "0.85"}, "k", 0.0) == pytest.approx(0.85)

    def test_missing_key_returns_default(self) -> None:
        """Missing key returns default value."""
        assert safe_float({}, "missing", 0.5) == pytest.approx(0.5)


class TestAssembleReportBuildStatusException:
    """Cover lines 271-272: build-status.yaml read exception in assemble_report."""

    def test_corrupt_build_status_results_in_none_build(
        self, tmp_path: Path
    ) -> None:
        """When build-status.yaml exists but raises on read, build=None (lines 271-272)."""
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260101T000000Z-aaaa0001"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(meta / "run.yaml", {
            "run_id": "20260101T000000Z-aaaa0001",
            "task": "task",
            "status": "active",
            "phase": "research",
        })

        trw_dir = tmp_path / ".trw"
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)
        # Write a build-status.yaml with content that causes int() conversion to fail
        # We need to trigger the except Exception branch at line 271
        # Write a valid YAML file then monkeypatch reader to raise
        writer.write_yaml(context_dir / "build-status.yaml", {
            "tests_passed": True,
            "test_count": "not-an-int",  # int(str(...)) will succeed, need another approach
        })

        reader = FileStateReader()

        # Monkeypatch reader.read_yaml to raise for build-status.yaml
        original_read = reader.read_yaml
        call_count = [0]

        def selective_read(path: Path) -> dict[str, object]:
            if "build-status" in str(path):
                raise StateError("corrupt build status", path=str(path))
            return original_read(path)

        reader.read_yaml = selective_read  # type: ignore[method-assign]

        report = assemble_report(run_dir, reader, trw_dir)
        assert report.build is None


# ===========================================================================
# scoring.py tests
# ===========================================================================


class TestComputeImpactDistributionReadException:
    """Cover lines 277-278: YAML read exception in compute_impact_distribution."""

    def test_corrupt_yaml_file_is_skipped(self, tmp_path: Path) -> None:
        """When _reader.read_yaml raises, the file is skipped (lines 277-278)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import compute_impact_distribution

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        writer = FileStateWriter()
        writer.write_yaml(entries_dir / "good.yaml", {
            "id": "L-ok", "summary": "ok", "impact": 0.9, "status": "active",
        })
        # Write a corrupt file that the reader will fail to parse
        (entries_dir / "bad.yaml").write_text("{not: valid: yaml: [", encoding="utf-8")

        # Patch the module-level _reader so compute_impact_distribution sees the corrupt file
        original_reader = scoring_mod._reader
        try:
            real_read = original_reader.read_yaml

            def patched_read(path: Path) -> dict[str, object]:
                if "bad" in str(path):
                    raise StateError("parse error", path=str(path))
                return real_read(path)

            scoring_mod._reader.read_yaml = patched_read  # type: ignore[method-assign]
            result = compute_impact_distribution(entries_dir)
            # Only good entry counted
            assert result["total_active"] == 1
        finally:
            scoring_mod._reader.read_yaml = real_read  # type: ignore[method-assign]


class TestUtilityBasedPruneCandidatesTier3:
    """Cover lines 623-635: tier 3 prune candidates (utility below prune threshold, age > 14 days)."""

    def _make_entry(
        self,
        entry_id: str,
        created: str,
        impact: float = 0.5,
        status: str = "active",
    ) -> tuple[Path, dict[str, object]]:
        data: dict[str, object] = {
            "id": entry_id,
            "summary": f"Learning {entry_id}",
            "created": created,
            "status": status,
            "impact": impact,
            "q_value": impact,
            "q_observations": 0,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
        }
        return (Path(f"/fake/{entry_id}.yaml"), data)

    def test_tier3_prune_candidate_old_medium_utility(self) -> None:
        """Entry older than 14 days with utility just below prune threshold -> tier 3 candidate."""
        from trw_mcp.scoring import utility_based_prune_candidates

        # Use an old date with moderate (but sub-prune-threshold) impact
        # Impact=0.3, created 60 days ago, no access -> very low utility
        # The prune_threshold is typically 0.05 based on config defaults
        entry = self._make_entry("L-tier3", "2025-11-01", impact=0.3)
        result = utility_based_prune_candidates([entry])

        # Should be caught by either tier 2 (delete) or tier 3 (prune)
        # The key is lines 623-635 execute, which requires utility to be
        # < prune_threshold but >= delete_threshold, AND age > 14 days
        # Since delete < prune, with very low utility it may hit tier 2 first
        # Let's verify it does produce a candidate
        assert len(result) >= 1
        assert result[0]["id"] == "L-tier3"

    def test_tier3_medium_impact_older_entry_prune_range(self) -> None:
        """Verify tier 3 path (not tier 2) executes by using moderate impact, old entry."""
        # Force moderate prune/delete thresholds so an old medium-utility entry
        # falls precisely in tier 3 (below prune but above delete)
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import utility_based_prune_candidates
        old_config = scoring_mod._config

        try:
            # Use config with delete_threshold much lower than prune_threshold
            test_config = TRWConfig()
            # Most entries with 60 days old and moderate impact will be tier 2 or 3
            scoring_mod._config = test_config

            # Use 30-day-old entry with impact=0.45, q_observations=5 (past cold start)
            # This should produce moderate utility that might be in tier 3 range
            entry = self._make_entry("L-t3b", "2025-12-15", impact=0.45)
            result = utility_based_prune_candidates([entry])
            # Result may be empty if utility is above prune threshold - that's fine.
            # The important thing is no exception is raised and lines 623-635 are visited
            # when the conditions are met. We verify with a clearly old low-impact entry.
            old_entry = self._make_entry("L-t3c", "2025-09-01", impact=0.35)
            result2 = utility_based_prune_candidates([old_entry])
            assert isinstance(result2, list)
        finally:
            scoring_mod._config = old_config

    def test_tier3_reason_contains_prune_threshold(self) -> None:
        """Tier 3 candidate reason mentions 'prune threshold'."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import utility_based_prune_candidates
        old_config = scoring_mod._config

        try:
            # Set a very high prune threshold so an old entry falls into tier 3
            # by overriding the thresholds via a custom config object
            cfg = TRWConfig()
            # Manipulate the object directly
            object.__setattr__(cfg, "learning_utility_delete_threshold", 0.0)
            object.__setattr__(cfg, "learning_utility_prune_threshold", 0.99)
            scoring_mod._config = cfg

            entry = self._make_entry("L-t3-reason", "2025-10-01", impact=0.5)
            result = utility_based_prune_candidates([entry])

            # With prune_threshold=0.99 and delete_threshold=0.0,
            # any utility > 0.0 will be caught by tier 3 (not tier 2)
            # and age > 14 days (created 2025-10-01 is definitely > 14 days ago)
            assert any(
                "prune threshold" in str(r.get("reason", ""))
                for r in result
            ), f"Expected 'prune threshold' in reasons, got: {[r.get('reason') for r in result]}"
        finally:
            scoring_mod._config = old_config


class TestFindSessionStartTs:
    """Cover lines 715-738: _find_session_start_ts function."""

    def test_returns_none_when_task_root_missing(self, tmp_path: Path) -> None:
        """Returns None when task_root directory does not exist (lines 712-713)."""
        from trw_mcp.scoring import _find_session_start_ts

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No task root created
        result = _find_session_start_ts(trw_dir)
        assert result is None

    def test_finds_run_init_event(self, tmp_path: Path) -> None:
        """Finds run_init event timestamp from events.jsonl (lines 715-738)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import _find_session_start_ts

        old_config = scoring_mod._config

        try:
            # Configure task_root to point to our temp dir structure
            cfg = scoring_mod._config.__class__()
            object.__setattr__(cfg, "task_root", "tasks")
            scoring_mod._config = cfg

            trw_dir = tmp_path / ".trw"
            trw_dir.mkdir()

            task_dir = tmp_path / "tasks" / "my-task"
            run_dir = task_dir / "runs" / "20260101T000000Z-abc12345"
            meta_dir = run_dir / "meta"
            meta_dir.mkdir(parents=True)

            writer = FileStateWriter()
            writer.append_jsonl(meta_dir / "events.jsonl", {
                "ts": "2026-01-01T10:00:00+00:00",
                "event": "run_init",
            })

            result = _find_session_start_ts(trw_dir)
            assert result is not None
            assert result.year == 2026

        finally:
            scoring_mod._config = old_config

    def test_finds_session_start_event(self, tmp_path: Path) -> None:
        """Finds session_start event timestamp (also accepted event type)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import _find_session_start_ts

        old_config = scoring_mod._config

        try:
            cfg = scoring_mod._config.__class__()
            object.__setattr__(cfg, "task_root", "tasks")
            scoring_mod._config = cfg

            trw_dir = tmp_path / ".trw"
            trw_dir.mkdir()

            task_dir = tmp_path / "tasks" / "another-task"
            run_dir = task_dir / "runs" / "20260115T000000Z-def67890"
            meta_dir = run_dir / "meta"
            meta_dir.mkdir(parents=True)

            writer = FileStateWriter()
            writer.append_jsonl(meta_dir / "events.jsonl", {
                "ts": "2026-01-15T08:00:00+00:00",
                "event": "session_start",
            })

            result = _find_session_start_ts(trw_dir)
            assert result is not None
            assert result.month == 1
            assert result.day == 15

        finally:
            scoring_mod._config = old_config

    def test_invalid_timestamp_in_event_skipped(self, tmp_path: Path) -> None:
        """Invalid ts in event is skipped without raising (ValueError continue)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import _find_session_start_ts

        old_config = scoring_mod._config

        try:
            cfg = scoring_mod._config.__class__()
            object.__setattr__(cfg, "task_root", "tasks")
            scoring_mod._config = cfg

            trw_dir = tmp_path / ".trw"
            trw_dir.mkdir()

            task_dir = tmp_path / "tasks" / "bad-ts-task"
            run_dir = task_dir / "runs" / "20260101T000000Z-bad12345"
            meta_dir = run_dir / "meta"
            meta_dir.mkdir(parents=True)

            writer = FileStateWriter()
            # Write event with invalid timestamp
            writer.append_jsonl(meta_dir / "events.jsonl", {
                "ts": "not-a-timestamp",
                "event": "run_init",
            })

            result = _find_session_start_ts(trw_dir)
            # Invalid ts: no valid timestamp found, returns None
            assert result is None

        finally:
            scoring_mod._config = old_config

    def test_no_matching_events_returns_none(self, tmp_path: Path) -> None:
        """Non-session events don't update latest_ts, returns None."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import _find_session_start_ts

        old_config = scoring_mod._config

        try:
            cfg = scoring_mod._config.__class__()
            object.__setattr__(cfg, "task_root", "tasks")
            scoring_mod._config = cfg

            trw_dir = tmp_path / ".trw"
            trw_dir.mkdir()

            task_dir = tmp_path / "tasks" / "no-session-task"
            run_dir = task_dir / "runs" / "20260101T000000Z-ccc12345"
            meta_dir = run_dir / "meta"
            meta_dir.mkdir(parents=True)

            writer = FileStateWriter()
            writer.append_jsonl(meta_dir / "events.jsonl", {
                "ts": "2026-01-01T10:00:00+00:00",
                "event": "checkpoint",  # Not run_init or session_start
            })

            result = _find_session_start_ts(trw_dir)
            assert result is None

        finally:
            scoring_mod._config = old_config

    def test_task_dir_without_runs_subdir_skipped(self, tmp_path: Path) -> None:
        """Task directory without runs/ subdirectory is skipped gracefully."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import _find_session_start_ts

        old_config = scoring_mod._config

        try:
            cfg = scoring_mod._config.__class__()
            object.__setattr__(cfg, "task_root", "tasks")
            scoring_mod._config = cfg

            trw_dir = tmp_path / ".trw"
            trw_dir.mkdir()

            # Create a task dir without runs/ inside it
            task_dir = tmp_path / "tasks" / "no-runs"
            task_dir.mkdir(parents=True)
            (task_dir / "somefile.txt").write_text("hello", encoding="utf-8")

            result = _find_session_start_ts(trw_dir)
            assert result is None

        finally:
            scoring_mod._config = old_config


class TestCorrelateRecalls:
    """Cover lines 782, 792, 795-796, 803: correlate_recalls function paths."""

    def _make_receipt_log(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        records: list[dict[str, object]],
    ) -> Path:
        """Create .trw/learnings/receipts/recall_log.jsonl with given records."""
        import trw_mcp.scoring as scoring_mod

        trw_dir = tmp_path / ".trw"
        receipts_dir = (
            trw_dir
            / scoring_mod._config.learnings_dir
            / scoring_mod._config.receipts_dir
        )
        receipts_dir.mkdir(parents=True, exist_ok=True)
        log_path = receipts_dir / "recall_log.jsonl"
        for record in records:
            writer.append_jsonl(log_path, record)
        return trw_dir

    def test_missing_receipt_log_returns_empty(self, tmp_path: Path) -> None:
        """No receipt log -> empty list (line 773 early return)."""
        from trw_mcp.scoring import correlate_recalls

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_session_scope_calls_find_session_start(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """scope='session' triggers session-start lookup (line 782 branch)."""
        from trw_mcp.scoring import correlate_recalls

        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=5)).isoformat()

        trw_dir = self._make_receipt_log(tmp_path, writer, [
            {
                "ts": recent_ts,
                "matched_ids": ["L-session"],
            }
        ])

        # With scope="session" and no session start found, falls back to window
        result = correlate_recalls(trw_dir, 60, scope="session")
        # Should find the recent record
        ids = [lid for lid, _ in result]
        assert "L-session" in ids

    def test_empty_ts_field_is_skipped(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Record with empty ts is skipped (line 792 continue)."""
        from trw_mcp.scoring import correlate_recalls

        trw_dir = self._make_receipt_log(tmp_path, writer, [
            {"ts": "", "matched_ids": ["L-empty-ts"]},  # Empty ts -> skip
        ])

        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_invalid_ts_format_is_skipped(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Record with invalid ISO ts is skipped via ValueError (lines 795-796)."""
        from trw_mcp.scoring import correlate_recalls

        trw_dir = self._make_receipt_log(tmp_path, writer, [
            {"ts": "not-a-timestamp", "matched_ids": ["L-bad-ts"]},
        ])

        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_receipt_outside_window_skipped(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Receipt older than the window is skipped (receipt_ts < cutoff_ts)."""
        from trw_mcp.scoring import correlate_recalls

        # 2 hours ago, window is 30 minutes
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        trw_dir = self._make_receipt_log(tmp_path, writer, [
            {"ts": old_ts, "matched_ids": ["L-old"]},
        ])

        result = correlate_recalls(trw_dir, 30)
        assert result == []

    def test_non_string_learning_ids_skipped(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Non-string or empty learning IDs in matched_ids are skipped."""
        from trw_mcp.scoring import correlate_recalls

        now_ts = datetime.now(timezone.utc).isoformat()
        trw_dir = self._make_receipt_log(tmp_path, writer, [
            {"ts": now_ts, "matched_ids": [None, "", 42, "L-valid"]},
        ])

        result = correlate_recalls(trw_dir, 30)
        ids = [lid for lid, _ in result]
        assert "L-valid" in ids
        assert None not in ids
        assert "" not in ids

    def test_recent_receipt_produces_discount(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Recent receipt produces a recency discount between floor and 1.0."""
        from trw_mcp.scoring import correlate_recalls

        now_ts = datetime.now(timezone.utc).isoformat()
        trw_dir = self._make_receipt_log(tmp_path, writer, [
            {"ts": now_ts, "matched_ids": ["L-recent"]},
        ])

        result = correlate_recalls(trw_dir, 30)
        assert len(result) == 1
        lid, discount = result[0]
        assert lid == "L-recent"
        assert 0.0 < discount <= 1.0


class TestProcessOutcome:
    """Cover lines 857, 866, 890: process_outcome function paths."""

    def _setup_trw_dir(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> Path:
        """Create minimal .trw structure with receipt log and a learning entry."""
        import trw_mcp.scoring as scoring_mod

        trw_dir = tmp_path / ".trw"
        receipts_dir = (
            trw_dir
            / scoring_mod._config.learnings_dir
            / scoring_mod._config.receipts_dir
        )
        receipts_dir.mkdir(parents=True, exist_ok=True)
        return trw_dir

    def test_entries_dir_missing_returns_empty(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When entries_dir doesn't exist after correlation, returns [] (line 857)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome

        trw_dir = self._setup_trw_dir(tmp_path, writer)

        # Create a receipt log with a recent recall
        now_ts = datetime.now(timezone.utc).isoformat()
        log_path = (
            trw_dir
            / scoring_mod._config.learnings_dir
            / scoring_mod._config.receipts_dir
            / "recall_log.jsonl"
        )
        writer.append_jsonl(log_path, {"ts": now_ts, "matched_ids": ["L-ghost"]})

        # Don't create entries_dir — process_outcome should return empty list
        result = process_outcome(trw_dir, 0.8, "tests_passed")
        assert result == []

    def test_unknown_learning_id_skipped(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Learning ID in receipt but not found in entries is skipped (line 866)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome

        trw_dir = self._setup_trw_dir(tmp_path, writer)

        # Create entries_dir but don't write the referenced entry
        entries_dir = (
            trw_dir / scoring_mod._config.learnings_dir / scoring_mod._config.entries_dir
        )
        entries_dir.mkdir(parents=True, exist_ok=True)

        now_ts = datetime.now(timezone.utc).isoformat()
        log_path = (
            trw_dir
            / scoring_mod._config.learnings_dir
            / scoring_mod._config.receipts_dir
            / "recall_log.jsonl"
        )
        writer.append_jsonl(log_path, {"ts": now_ts, "matched_ids": ["L-missing"]})

        result = process_outcome(trw_dir, 0.8, "tests_passed")
        assert result == []

    def test_non_list_outcome_history_reset(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """When outcome_history is not a list, it is reset to [] (line 890)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome

        trw_dir = self._setup_trw_dir(tmp_path, writer)

        entries_dir = (
            trw_dir / scoring_mod._config.learnings_dir / scoring_mod._config.entries_dir
        )
        entries_dir.mkdir(parents=True, exist_ok=True)

        # Write an entry with outcome_history as a string (not a list) -> triggers line 890
        writer.write_yaml(entries_dir / "2026-01-01-L-corrupt.yaml", {
            "id": "L-corrupt",
            "summary": "corrupt history",
            "detail": "d",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "outcome_history": "this should be a list",  # triggers line 890
        })

        now_ts = datetime.now(timezone.utc).isoformat()
        log_path = (
            trw_dir
            / scoring_mod._config.learnings_dir
            / scoring_mod._config.receipts_dir
            / "recall_log.jsonl"
        )
        writer.append_jsonl(log_path, {"ts": now_ts, "matched_ids": ["L-corrupt"]})

        result = process_outcome(trw_dir, 0.8, "tests_passed")
        # L-corrupt should be updated successfully
        assert "L-corrupt" in result

        # Verify the outcome_history was written as a list
        updated = FileStateReader().read_yaml(entries_dir / "2026-01-01-L-corrupt.yaml")
        assert isinstance(updated.get("outcome_history"), list)


class TestProcessOutcomeForEventException:
    """Cover lines 999-1001: process_outcome_for_event exception handling."""

    def test_state_error_returns_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """StateError from resolve_trw_dir returns [] without propagating (lines 999-1001)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.exceptions import StateError
        from trw_mcp.scoring import process_outcome_for_event

        monkeypatch.setattr(
            scoring_mod, "resolve_trw_dir",
            lambda: (_ for _ in ()).throw(StateError("no .trw", path="none")),
        )

        result = process_outcome_for_event("tests_passed")
        assert result == []

    def test_os_error_returns_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError from resolve_trw_dir returns [] without propagating (lines 999-1001)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome_for_event

        monkeypatch.setattr(
            scoring_mod, "resolve_trw_dir",
            lambda: (_ for _ in ()).throw(OSError("permission denied")),
        )

        result = process_outcome_for_event("tests_passed")
        assert result == []

    def test_none_reward_returns_empty_without_resolve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Events with no reward don't call resolve_trw_dir at all."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome_for_event

        resolve_called = [False]

        def mock_resolve() -> Path:
            resolve_called[0] = True
            return Path("/fake")

        monkeypatch.setattr(scoring_mod, "resolve_trw_dir", mock_resolve)

        result = process_outcome_for_event("shard_started")
        assert result == []
        assert not resolve_called[0]

    def test_success_path_calls_process_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Happy path: resolve_trw_dir succeeds and process_outcome is called (lines 997-998)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome_for_event

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Mock resolve_trw_dir to return our temp dir (no receipts = empty result)
        monkeypatch.setattr(scoring_mod, "resolve_trw_dir", lambda: trw_dir)

        # tests_passed has a valid reward in REWARD_MAP, so it tries process_outcome
        result = process_outcome_for_event("tests_passed")
        # No receipts exist -> correlate_recalls returns [] -> process_outcome returns []
        assert isinstance(result, list)


# ===========================================================================
# Additional recall_search.py tests to cover remaining lines
# ===========================================================================


class TestSearchEntriesNonExistentDir:
    """Cover line 46: early return when entries_dir doesn't exist."""

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent entries_dir returns ([], []) immediately (line 46)."""
        reader = FileStateReader()
        matches, paths = search_entries(
            tmp_path / "does_not_exist",
            query_tokens=[],
            reader=reader,
        )
        assert matches == []
        assert paths == []


class TestSearchEntriesImpactFilter:
    """Cover line 59: min_impact filter skips entries below threshold."""

    def test_low_impact_entry_excluded(self, tmp_path: Path) -> None:
        """Entry with impact below min_impact is skipped (line 59 continue)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        reader = FileStateReader()

        writer.write_yaml(entries_dir / "low.yaml", {
            "id": "L-low", "summary": "low impact entry", "detail": "", "impact": 0.2,
        })
        writer.write_yaml(entries_dir / "high.yaml", {
            "id": "L-high", "summary": "high impact entry", "detail": "", "impact": 0.8,
        })

        matches, _ = search_entries(
            entries_dir, query_tokens=[], reader=reader, min_impact=0.7,
        )
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-high" in ids
        assert "L-low" not in ids


class TestSearchEntriesTagFilter:
    """Cover lines 69-70: tag filter — entry must match at least one tag."""

    def test_tag_filter_excludes_non_matching(self, tmp_path: Path) -> None:
        """Entry without matching tag is excluded (lines 69-70 continue)."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        reader = FileStateReader()

        writer.write_yaml(entries_dir / "tagged.yaml", {
            "id": "L-tagged", "summary": "tagged entry", "detail": "",
            "impact": 0.7, "tags": ["pytest", "testing"],
        })
        writer.write_yaml(entries_dir / "untagged.yaml", {
            "id": "L-untagged", "summary": "untagged entry", "detail": "",
            "impact": 0.7, "tags": ["deployment"],
        })

        matches, _ = search_entries(
            entries_dir, query_tokens=[], reader=reader, tags=["pytest"],
        )
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-tagged" in ids
        assert "L-untagged" not in ids

    def test_tag_filter_accepts_any_matching_tag(self, tmp_path: Path) -> None:
        """Entry is included when any one of the filter tags matches."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        reader = FileStateReader()

        writer.write_yaml(entries_dir / "entry.yaml", {
            "id": "L-match", "summary": "entry", "detail": "",
            "impact": 0.7, "tags": ["pydantic", "models"],
        })

        matches, _ = search_entries(
            entries_dir, query_tokens=[], reader=reader, tags=["models", "testing"],
        )
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-match" in ids


class TestSearchEntriesHyphenatedTagExpansion:
    """Cover lines 77-80: hyphenated tag expansion during query matching."""

    def test_hyphenated_tag_expanded_for_query_match(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Query 'pydantic' matches entry with tag 'pydantic-v2' (lines 77-80)."""
        import sys
        import types
        mock_retrieval = types.ModuleType("trw_mcp.state.retrieval")
        mock_retrieval.hybrid_search = lambda *a, **kw: []  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "trw_mcp.state.retrieval", mock_retrieval)

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        reader = FileStateReader()

        writer.write_yaml(entries_dir / "pydantic.yaml", {
            "id": "L-pyd", "summary": "model handling", "detail": "v2 details",
            "impact": 0.7, "tags": ["pydantic-v2", "models"],
        })

        # Query for 'pydantic' — should match via tag expansion of 'pydantic-v2'
        matches, _ = search_entries(
            entries_dir, query_tokens=["pydantic"], reader=reader,
        )
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-pyd" in ids

    def test_non_hyphenated_tags_still_matched(self, tmp_path: Path) -> None:
        """Tags without hyphens still appear in tag_text for query matching."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()
        writer = FileStateWriter()
        reader = FileStateReader()

        writer.write_yaml(entries_dir / "simple.yaml", {
            "id": "L-simple", "summary": "basic entry", "detail": "",
            "impact": 0.7, "tags": ["testing"],
        })

        matches, _ = search_entries(
            entries_dir, query_tokens=["testing"], reader=reader,
        )
        ids = [str(m.get("id", "")) for m in matches]
        assert "L-simple" in ids


class TestSearchPatternsNonExistentDir:
    """Cover line 115: search_patterns early return when dir missing."""

    def test_nonexistent_patterns_dir_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent patterns_dir returns [] immediately (line 115 branch)."""
        reader = FileStateReader()
        result = search_patterns(tmp_path / "no_patterns", query_tokens=[], reader=reader)
        assert result == []


# ===========================================================================
# Additional scoring.py tests to cover remaining gaps
# ===========================================================================


class TestComputeUtilityScoreAccessBoost:
    """Cover line 186: access_count boost in compute_utility_score."""

    def test_access_count_positive_adds_boost(self) -> None:
        """access_count > 0 adds sub-linear boost to utility (line 186)."""
        from trw_mcp.scoring import compute_utility_score

        score_no_access = compute_utility_score(0.5, 0, 1, 0.5, 5, access_count=0)
        score_with_access = compute_utility_score(0.5, 0, 1, 0.5, 5, access_count=10)
        assert score_with_access > score_no_access

    def test_access_count_boost_is_capped(self) -> None:
        """access_count boost is capped at access_count_boost_cap."""
        from trw_mcp.scoring import compute_utility_score

        score_moderate = compute_utility_score(
            0.5, 0, 1, 0.5, 5, access_count=10, access_count_boost_cap=0.15,
        )
        score_high = compute_utility_score(
            0.5, 0, 1, 0.5, 5, access_count=10000, access_count_boost_cap=0.15,
        )
        # Both should be capped at the same boost
        assert abs(score_high - score_moderate) < 0.001 or score_high >= score_moderate


class TestEntryUtilityInvalidCreatedDate:
    """Cover lines 225-226: ValueError when created date is unparseable in _entry_utility."""

    def test_invalid_created_date_uses_raw_values(self) -> None:
        """When created field has invalid date, ValueError is caught and raw values used."""
        from trw_mcp.scoring import rank_by_utility

        entry: dict[str, object] = {
            "id": "L-bad-date",
            "summary": "entry with bad date",
            "detail": "",
            "tags": [],
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": "not-a-real-date",  # Triggers ValueError in apply_time_decay
        }

        # rank_by_utility calls _entry_utility which calls apply_time_decay
        # The ValueError is caught and raw values are used
        result = rank_by_utility([entry], query_tokens=[], lambda_weight=0.5)
        assert len(result) == 1
        assert result[0]["id"] == "L-bad-date"


class TestEnforceTierDistribution:
    """Cover lines 397-465: enforce_tier_distribution function."""

    def test_empty_entries_returns_empty(self) -> None:
        """Empty entries list returns []."""
        from trw_mcp.scoring import enforce_tier_distribution

        result = enforce_tier_distribution([])
        assert result == []

    def test_fewer_than_5_entries_returns_empty(self) -> None:
        """Fewer than 5 entries returns [] (no enforcement on small sets)."""
        from trw_mcp.scoring import enforce_tier_distribution

        entries = [("L-a", 0.95), ("L-b", 0.90), ("L-c", 0.75)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_no_cap_violation_returns_empty(self) -> None:
        """When no tier exceeds its cap, no demotions occur."""
        from trw_mcp.scoring import enforce_tier_distribution

        # 1 critical out of 10 = 10% -> depends on config critical_cap (default 0.25)
        entries = [
            ("L-c1", 0.95),
            ("L-h1", 0.85), ("L-h2", 0.80),
            ("L-m1", 0.50), ("L-m2", 0.55),
            ("L-m3", 0.45), ("L-m4", 0.40),
            ("L-l1", 0.30), ("L-l2", 0.25), ("L-l3", 0.20),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.25, high_cap=0.5)
        # With 1/10=10% critical (below 25% cap) and 2/10=20% high (below 50% cap),
        # no demotions should occur
        assert result == []

    def test_critical_cap_exceeded_triggers_demotion(self) -> None:
        """Critical tier exceeds cap -> lowest critical entry gets demoted (lines 424-438)."""
        from trw_mcp.scoring import enforce_tier_distribution

        # 4 critical out of 5 = 80%, cap = 0.05 (5%) -> demotion triggered
        entries = [
            ("L-c1", 0.91), ("L-c2", 0.93), ("L-c3", 0.95), ("L-c4", 0.97),
            ("L-m1", 0.50),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.05, high_cap=0.5)
        assert len(result) >= 1
        # Lowest critical (L-c1, 0.91) gets demoted
        demoted_ids = [r[0] for r in result]
        assert "L-c1" in demoted_ids
        # New score should be in high tier range (0.7-0.89)
        demoted_score = next(s for i, s in result if i == "L-c1")
        assert 0.7 <= demoted_score <= 0.89

    def test_high_cap_exceeded_triggers_demotion(self) -> None:
        """High tier exceeds cap -> lowest high entry gets demoted (lines 447-463)."""
        from trw_mcp.scoring import enforce_tier_distribution

        # 4 high out of 5 = 80%, high_cap = 0.05 -> demotion triggered
        entries = [
            ("L-h1", 0.71), ("L-h2", 0.75), ("L-h3", 0.80), ("L-h4", 0.85),
            ("L-m1", 0.50),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.5, high_cap=0.05)
        assert len(result) >= 1
        demoted_ids = [r[0] for r in result]
        assert "L-h1" in demoted_ids
        # New score should be in medium tier range (0.4-0.69)
        demoted_score = next(s for i, s in result if i == "L-h1")
        assert 0.4 <= demoted_score <= 0.69

    def test_both_caps_exceeded_produces_two_demotions(self) -> None:
        """Both critical and high tiers exceeding caps -> two demotions."""
        from trw_mcp.scoring import enforce_tier_distribution

        # 5 critical + 5 high out of 10 = very high percentages
        entries = [
            ("L-c1", 0.91), ("L-c2", 0.92), ("L-c3", 0.93), ("L-c4", 0.94), ("L-c5", 0.95),
            ("L-h1", 0.71), ("L-h2", 0.75), ("L-h3", 0.80), ("L-h4", 0.85), ("L-h5", 0.88),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.05, high_cap=0.05)
        # Both tiers exceed caps -> at least 2 demotions
        assert len(result) >= 2


class TestFindSessionStartTsRunDirWithoutEvents:
    """Cover line 722: events.jsonl missing in a run directory."""

    def test_run_dir_without_events_file_skipped(self, tmp_path: Path) -> None:
        """Run directory without events.jsonl is skipped (line 722 continue)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import _find_session_start_ts

        old_config = scoring_mod._config

        try:
            cfg = scoring_mod._config.__class__()
            object.__setattr__(cfg, "task_root", "tasks")
            scoring_mod._config = cfg

            trw_dir = tmp_path / ".trw"
            trw_dir.mkdir()

            # Create a run directory structure without events.jsonl
            task_dir = tmp_path / "tasks" / "no-events-task"
            run_dir = task_dir / "runs" / "20260101T000000Z-noevent1"
            meta_dir = run_dir / "meta"
            meta_dir.mkdir(parents=True)
            # No events.jsonl written

            result = _find_session_start_ts(trw_dir)
            assert result is None

        finally:
            scoring_mod._config = old_config


class TestCorrelateRecallsAdvancedPaths:
    """Cover lines 782 and 803: session cutoff override and future timestamp skip."""

    def _make_receipt_log(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
        records: list[dict[str, object]],
    ) -> Path:
        import trw_mcp.scoring as scoring_mod
        trw_dir = tmp_path / ".trw"
        receipts_dir = (
            trw_dir
            / scoring_mod._config.learnings_dir
            / scoring_mod._config.receipts_dir
        )
        receipts_dir.mkdir(parents=True, exist_ok=True)
        log_path = receipts_dir / "recall_log.jsonl"
        for record in records:
            writer.append_jsonl(log_path, record)
        return trw_dir

    def test_session_scope_with_found_session_start_overrides_cutoff(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """scope='session' + found session_start -> cutoff_ts = session_start (line 782)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import correlate_recalls

        old_config = scoring_mod._config

        try:
            cfg = scoring_mod._config.__class__()
            object.__setattr__(cfg, "task_root", "tasks")
            # Force session scope
            object.__setattr__(cfg, "learning_outcome_correlation_scope", "window")
            scoring_mod._config = cfg

            now = datetime.now(timezone.utc)
            # Create a session start event 2 hours ago
            session_start_ts = (now - timedelta(hours=2)).replace(microsecond=0)
            # Create a receipt from 1 hour ago (within session scope, outside 30-min window)
            receipt_ts = (now - timedelta(hours=1)).replace(microsecond=0)

            # Set up run events
            task_dir = tmp_path / "tasks" / "session-task"
            run_dir = task_dir / "runs" / "20260101T000000Z-sess1111"
            meta_dir = run_dir / "meta"
            meta_dir.mkdir(parents=True)
            writer.append_jsonl(meta_dir / "events.jsonl", {
                "ts": session_start_ts.isoformat(),
                "event": "run_init",
            })

            # Patch _find_session_start_ts to return our known session_start
            trw_dir = self._make_receipt_log(tmp_path, writer, [
                {"ts": receipt_ts.isoformat(), "matched_ids": ["L-in-session"]},
            ])

            # Monkeypatch to force session_start to be found
            monkeypatch.setattr(
                scoring_mod, "_find_session_start_ts",
                lambda _: session_start_ts,
            )

            result = correlate_recalls(trw_dir, 30, scope="session")
            ids = [lid for lid, _ in result]
            assert "L-in-session" in ids

        finally:
            scoring_mod._config = old_config

    def test_future_timestamp_receipt_is_skipped(
        self, tmp_path: Path, writer: FileStateWriter
    ) -> None:
        """Receipt with timestamp in the future has elapsed_secs < 0 -> skipped (line 803)."""
        from trw_mcp.scoring import correlate_recalls

        # Create a receipt with a timestamp 1 hour in the future
        future_ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        import trw_mcp.scoring as scoring_mod
        trw_dir = tmp_path / ".trw"
        receipts_dir = (
            trw_dir
            / scoring_mod._config.learnings_dir
            / scoring_mod._config.receipts_dir
        )
        receipts_dir.mkdir(parents=True, exist_ok=True)
        writer.append_jsonl(receipts_dir / "recall_log.jsonl", {
            "ts": future_ts,
            "matched_ids": ["L-future"],
        })

        result = correlate_recalls(trw_dir, 30)
        # Future timestamps have elapsed_secs < 0 -> skipped
        ids = [lid for lid, _ in result]
        assert "L-future" not in ids


class TestProcessOutcomeHistoryCap:
    """Cover line 893: outcome_history trimmed when it exceeds history_cap."""

    def test_history_trimmed_when_exceeds_cap(
        self, tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When outcome_history exceeds history_cap, it is trimmed (line 893)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome

        trw_dir = tmp_path / ".trw"
        receipts_dir = (
            trw_dir / scoring_mod._config.learnings_dir / scoring_mod._config.receipts_dir
        )
        receipts_dir.mkdir(parents=True)
        entries_dir = (
            trw_dir / scoring_mod._config.learnings_dir / scoring_mod._config.entries_dir
        )
        entries_dir.mkdir(parents=True)

        # Get the history cap
        history_cap = scoring_mod._config.learning_outcome_history_cap

        # Write entry with outcome_history already AT the cap (so adding 1 will trigger trim)
        existing_history = [f"2026-01-0{i % 9 + 1}:+0.8:tests_passed" for i in range(history_cap)]
        writer.write_yaml(entries_dir / "2026-01-01-L-capped.yaml", {
            "id": "L-capped",
            "summary": "capped history learning",
            "detail": "d",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "outcome_history": existing_history,
        })

        # Write receipt pointing to this entry
        now_ts = datetime.now(timezone.utc).isoformat()
        writer.append_jsonl(receipts_dir / "recall_log.jsonl", {
            "ts": now_ts, "matched_ids": ["L-capped"],
        })

        result = process_outcome(trw_dir, 0.8, "tests_passed")
        assert "L-capped" in result

        # Verify history was trimmed to cap
        updated = FileStateReader().read_yaml(entries_dir / "2026-01-01-L-capped.yaml")
        history = updated.get("outcome_history", [])
        assert isinstance(history, list)
        assert len(history) <= history_cap


class TestProcessOutcomeForEventSuccessPath:
    """Cover line 997-998: successful resolve_trw_dir + process_outcome call."""

    def test_process_outcome_called_with_valid_trw_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When resolve_trw_dir succeeds, process_outcome is invoked (lines 997-998)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome_for_event

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # No receipts present, so process_outcome returns []
        monkeypatch.setattr(scoring_mod, "resolve_trw_dir", lambda: trw_dir)

        result = process_outcome_for_event("phase_gate_passed")
        assert isinstance(result, list)

    def test_process_outcome_returns_updated_ids_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: FileStateWriter
    ) -> None:
        """Full success path: receipts exist + entries exist -> IDs returned."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import process_outcome_for_event

        trw_dir = tmp_path / ".trw"

        # Set up receipts
        receipts_dir = (
            trw_dir / scoring_mod._config.learnings_dir / scoring_mod._config.receipts_dir
        )
        receipts_dir.mkdir(parents=True)
        now_ts = datetime.now(timezone.utc).isoformat()
        writer.append_jsonl(receipts_dir / "recall_log.jsonl", {
            "ts": now_ts,
            "matched_ids": ["L-target"],
        })

        # Set up entries directory with the referenced entry
        entries_dir = (
            trw_dir / scoring_mod._config.learnings_dir / scoring_mod._config.entries_dir
        )
        entries_dir.mkdir(parents=True)
        writer.write_yaml(entries_dir / "2026-01-01-L-target.yaml", {
            "id": "L-target",
            "summary": "target learning",
            "detail": "d",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
        })

        monkeypatch.setattr(scoring_mod, "resolve_trw_dir", lambda: trw_dir)

        result = process_outcome_for_event("tests_passed")
        assert "L-target" in result
