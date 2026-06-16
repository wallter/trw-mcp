"""Targeted analytics entry iteration and lookup branch tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._analytics_branches_support import _write_entry
from trw_mcp.state.analytics import (
    _iter_entry_files,
    detect_tool_sequences,
    find_entry_by_id,
    has_existing_mechanical_learning,
    has_existing_success_learning,
    surface_validated_learnings,
)

from ._analytics_branches_support import trw_dir  # noqa: F401


class TestIterEntryFilesIndexYamlSkipped:
    """Line 66: index.yaml is skipped by _iter_entry_files."""

    def test_index_yaml_is_skipped(self, trw_dir: Path) -> None:
        """index.yaml file is silently skipped — line 66 (continue)."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "valid_entry", summary="real learning")
        (entries_dir / "index.yaml").write_text("entries: []\ntotal_count: 0\n", encoding="utf-8")

        results = list(_iter_entry_files(entries_dir))
        filenames = [p.name for p, _ in results]
        assert "index.yaml" not in filenames
        assert "valid_entry.yaml" in filenames

    def test_index_yaml_skipped_sorted_order(self, trw_dir: Path) -> None:
        """index.yaml skipped even in sorted_order=True mode — line 66."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "aaa_entry", summary="first learning")
        (entries_dir / "index.yaml").write_text("entries: []\ntotal_count: 0\n", encoding="utf-8")

        results = list(_iter_entry_files(entries_dir, sorted_order=True))
        filenames = [p.name for p, _ in results]
        assert "index.yaml" not in filenames
        assert "aaa_entry.yaml" in filenames


class TestIterEntryFilesExceptionHandling:
    """Line 71: corrupt files are skipped with continue."""

    def test_corrupt_yaml_is_skipped(self, trw_dir: Path) -> None:
        """Unparseable YAML file is silently skipped — line 71 (continue)."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "good_entry", summary="valid entry")
        (entries_dir / "bad_entry.yaml").write_bytes(b"\xff\xfe\x00INVALID\x00")

        results = list(_iter_entry_files(entries_dir))
        filenames = [p.name for p, _ in results]
        assert "good_entry.yaml" in filenames
        assert "bad_entry.yaml" not in filenames

    def test_corrupt_yaml_skipped_sorted(self, trw_dir: Path) -> None:
        """Corrupt file skipped in sorted_order=True path — line 71."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "valid_entry", summary="ok")
        (entries_dir / "zzz_corrupt.yaml").write_bytes(b"\x00\x01INVALID\xff")

        results = list(_iter_entry_files(entries_dir, sorted_order=True))
        filenames = [p.name for p, _ in results]
        assert "valid_entry.yaml" in filenames
        assert "zzz_corrupt.yaml" not in filenames


class TestFindEntryByIdExceptionHandling:
    """Lines 144-145: exception handling in find_entry_by_id."""

    def test_corrupt_entry_skipped_returns_none(self, trw_dir: Path) -> None:
        """Corrupt YAML during ID scan is skipped — lines 144-145."""
        entries_dir = trw_dir / "learnings" / "entries"
        (entries_dir / "corrupt.yaml").write_bytes(b"\xff\xfe INVALID \x00")

        result = find_entry_by_id(entries_dir, "L-nonexistent")
        assert result is None

    def test_corrupt_entry_skipped_valid_found(self, trw_dir: Path) -> None:
        """Corrupt file skipped; subsequent valid file with matching ID found."""
        entries_dir = trw_dir / "learnings" / "entries"
        (entries_dir / "aaa_corrupt.yaml").write_bytes(b"\xff\xfe INVALID \x00")
        _write_entry(entries_dir, "zzz_valid", summary="target", learning_id="L-target")

        result = find_entry_by_id(entries_dir, "L-target")
        assert result is not None
        path, data = result
        assert data["id"] == "L-target"
        assert path.suffix == ".yaml"
        assert "summary" in data


class TestDetectToolSequences:
    """Lines 257-266, 272-274: detect_tool_sequences."""

    def test_empty_events_returns_empty(self) -> None:
        """Less than 2 events returns empty list."""
        assert detect_tool_sequences([]) == []
        assert detect_tool_sequences([{"event": "session_start"}]) == []

    def test_no_success_events_returns_empty(self) -> None:
        """Events with no success anchors returns empty list."""
        events = [
            {"event": "run_init"},
            {"event": "phase_transition"},
            {"event": "tool_call"},
        ]
        result = detect_tool_sequences(events)
        assert result == []

    def test_single_success_event_no_repeats(self) -> None:
        """Single success event with min_occurrences=3 returns empty."""
        events = [
            {"event": "run_init"},
            {"event": "task_complete"},
        ]
        result = detect_tool_sequences(events, min_occurrences=3)
        assert result == []

    def test_repeated_sequence_detected(self) -> None:
        """Repeated sequences meeting min_occurrences threshold are returned — lines 257-266."""
        events: list[dict[str, object]] = []
        for _ in range(3):
            events.append({"event": "checkpoint"})
            events.append({"event": "task_complete"})

        result = detect_tool_sequences(events, lookback=1, min_occurrences=3)
        assert len(result) > 0
        entry = result[0]
        assert "sequence" in entry
        assert "count" in entry
        assert entry["count"] == 3
        assert "/" in str(entry["success_rate"])

    def test_sequence_rate_format(self) -> None:
        """success_rate is formatted as 'count/total_anchors' — line 273."""
        events: list[dict[str, object]] = []
        for _ in range(3):
            events.append({"event": "tool_used"})
            events.append({"event": "task_done"})

        result = detect_tool_sequences(events, lookback=1, min_occurrences=3)
        assert len(result) >= 1
        rate = str(result[0]["success_rate"])
        parts = rate.split("/")
        assert len(parts) == 2
        assert parts[0].isdigit() and parts[1].isdigit()

    def test_empty_event_type_becomes_unknown_in_sequence(self) -> None:
        """Events with no 'event' key become 'unknown' in sequence — line 260, 263."""
        events: list[dict[str, object]] = []
        for _ in range(3):
            events.append({})
            events.append({"event": "task_complete"})

        result = detect_tool_sequences(events, lookback=1, min_occurrences=3)
        if result:
            seq = result[0]["sequence"]
            assert "unknown" in seq

    @pytest.mark.parametrize("min_occ", [1, 2, 4])
    def test_min_occurrences_threshold(self, min_occ: int) -> None:
        """Sequences below min_occurrences are filtered out."""
        events: list[dict[str, object]] = []
        for _ in range(3):
            events.append({"event": "step_a"})
            events.append({"event": "task_success"})

        result = detect_tool_sequences(events, lookback=1, min_occurrences=min_occ)
        if min_occ <= 3:
            assert len(result) > 0
        else:
            assert len(result) == 0


class TestSurfaceValidatedLearnings:
    """Lines 308, 312-319: surface_validated_learnings."""

    def test_nonexistent_entries_dir_returns_empty(self, tmp_path: Path) -> None:
        """Returns [] when entries_dir doesn't exist — line 308."""
        fake_trw = tmp_path / ".trw_nonexistent"
        result = surface_validated_learnings(fake_trw)
        assert result == []

    def test_non_active_entries_skipped(self, trw_dir: Path) -> None:
        """Non-active entries are skipped — line 312-313."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "resolved_entry",
            status="resolved",
            q_value=0.9,
            q_observations=5,
        )
        result = surface_validated_learnings(trw_dir)
        assert result == []

    def test_low_q_entries_excluded(self, trw_dir: Path) -> None:
        """Entries below q_threshold are excluded — line 318."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "low_q",
            status="active",
            q_value=0.3,
            q_observations=5,
        )
        result = surface_validated_learnings(trw_dir, q_threshold=0.6)
        assert result == []

    def test_low_observations_excluded(self, trw_dir: Path) -> None:
        """Entries below cold_start_threshold are excluded — line 318."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "few_obs",
            status="active",
            q_value=0.9,
            q_observations=1,
        )
        result = surface_validated_learnings(trw_dir, cold_start_threshold=3)
        assert result == []

    def test_qualified_entries_returned_sorted(self, trw_dir: Path) -> None:
        """Qualified entries returned sorted by q_value descending — lines 312-319."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "high_q",
            summary="high q learning",
            status="active",
            q_value=0.95,
            q_observations=5,
        )
        _write_entry(
            entries_dir,
            "mid_q",
            summary="mid q learning",
            status="active",
            q_value=0.75,
            q_observations=4,
        )
        _write_entry(
            entries_dir,
            "too_low",
            summary="too low",
            status="active",
            q_value=0.4,
            q_observations=4,
        )
        result = surface_validated_learnings(trw_dir, q_threshold=0.6, cold_start_threshold=3)
        assert len(result) == 2
        assert result[0]["q_value"] >= result[1]["q_value"]
        assert result[0]["q_value"] == 0.95

    def test_result_fields_present(self, trw_dir: Path) -> None:
        """Result dicts have required keys — lines 319-325."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "qualified",
            summary="important learning",
            status="active",
            q_value=0.8,
            q_observations=3,
            tags=["testing"],
        )
        result = surface_validated_learnings(trw_dir)
        assert len(result) == 1
        entry = result[0]
        assert "learning_id" in entry
        assert "summary" in entry
        assert "q_value" in entry
        assert "q_observations" in entry
        assert "tags" in entry


class TestHasExistingSuccessLearning:
    """Lines 347-355: has_existing_success_learning."""

    def test_nonexistent_entries_dir_returns_false(self, tmp_path: Path) -> None:
        """Returns False when entries_dir doesn't exist."""
        fake_trw = tmp_path / ".trw_nonexistent"
        result = has_existing_success_learning(fake_trw, "Success: some event")
        assert result is False

    def test_finds_matching_prefix(self, trw_dir: Path) -> None:
        """Returns True when a matching summary prefix exists — lines 352-354."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "success_entry",
            summary="Success: task_complete pattern discovered",
        )
        result = has_existing_success_learning(trw_dir, "Success: task_complete pattern discovered")
        assert result is True

    def test_no_match_returns_false(self, trw_dir: Path) -> None:
        """Returns False when no matching prefix — line 355."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "other_entry", summary="Different summary entirely")
        result = has_existing_success_learning(trw_dir, "Success: something else")
        assert result is False

    def test_prefix_truncated_to_50_chars(self, trw_dir: Path) -> None:
        """Comparison uses only first 50 chars of summary — line 351."""
        entries_dir = trw_dir / "learnings" / "entries"
        long_summary = "A" * 60 + " suffix that should be ignored"
        _write_entry(entries_dir, "long_entry", summary=long_summary)
        result = has_existing_success_learning(trw_dir, "A" * 60 + " different suffix")
        assert result is True

    def test_case_insensitive_match(self, trw_dir: Path) -> None:
        """Prefix matching is case-insensitive — line 353."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "upper_entry", summary="SUCCESS: Build passed cleanly")
        result = has_existing_success_learning(trw_dir, "success: build passed cleanly")
        assert result is True


class TestHasExistingMechanicalLearning:
    """Lines 376, 381: has_existing_mechanical_learning."""

    def test_nonexistent_entries_dir_returns_false(self, tmp_path: Path) -> None:
        """Returns False when entries_dir doesn't exist — line 376."""
        fake_trw = tmp_path / ".trw_nonexistent"
        result = has_existing_mechanical_learning(fake_trw, "Repeated operation: build")
        assert result is False

    def test_no_match_returns_false(self, trw_dir: Path) -> None:
        """Returns False when no matching prefix exists — final return False (line 385)."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "other", summary="Error pattern: timeout in api")
        result = has_existing_mechanical_learning(trw_dir, "Repeated operation: build")
        assert result is False

    def test_finds_matching_active_entry(self, trw_dir: Path) -> None:
        """Returns True when active entry with matching prefix found — line 381."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "repeated_op",
            summary="repeated operation: file_modified (12x)",
            status="active",
        )
        result = has_existing_mechanical_learning(trw_dir, "Repeated operation: file_modified")
        assert result is True

    def test_non_active_entry_ignored(self, trw_dir: Path) -> None:
        """Non-active entries are ignored even if prefix matches — lines 379-380."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir,
            "resolved_op",
            summary="repeated operation: build_step (8x)",
            status="resolved",
        )
        result = has_existing_mechanical_learning(trw_dir, "Repeated operation: build_step")
        assert result is False
