"""Targeted coverage tests for analytics.py and analytics_report.py.

Covers the missing lines identified in the coverage gap analysis:
- analytics.py: lines 66, 71, 144-145, 257-266, 272-274, 308, 312-319,
  347-355, 376, 381, 444-445, 607, 626, 634, 840, 873-878, 929-930, 1030-1052
- analytics_report.py: lines 146-147, 180-181, 193, 209-210, 215-216
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import trw_mcp.state.analytics.report as analytics_mod
from trw_mcp.models.learning import LearningEntry, LearningStatus
from trw_mcp.state import analytics as analytics_state
from trw_mcp.state.analytics import core as analytics_core_mod
from trw_mcp.state.analytics import (
    _iter_entry_files,
    apply_status_update,
    auto_prune_excess_entries,
    backfill_source_attribution,
    compute_reflection_quality,
    detect_tool_sequences,
    find_entry_by_id,
    has_existing_mechanical_learning,
    has_existing_success_learning,
    mark_promoted,
    surface_validated_learnings,
    update_learning_index,
)
from trw_mcp.state.analytics.report import (
    _analyze_single_run,
    compute_ceremony_score,
    scan_all_runs,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

_reader = FileStateReader()
_writer = FileStateWriter()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw/ structure."""
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "reflections").mkdir()
    (d / "context").mkdir()
    return d


def _write_entry(
    entries_dir: Path,
    name: str,
    *,
    summary: str = "",
    impact: float = 0.5,
    status: str = "active",
    q_observations: int = 0,
    q_value: float = 0.5,
    access_count: int = 0,
    source_type: str = "agent",
    tags: list[str] | None = None,
    learning_id: str | None = None,
) -> None:
    """Write a learning entry YAML file.

    Summaries are always double-quoted to handle special chars like ':' safely.
    """
    lid = learning_id or f"L-{name}"
    if not summary:
        summary = f"Test learning {name}"
    # Escape any double-quotes in the summary value for YAML safety
    escaped_summary = summary.replace('"', '\\"')
    tag_str = ", ".join(f'"{t}"' for t in (tags or []))
    (entries_dir / f"{name}.yaml").write_text(
        f'id: {lid}\nsummary: "{escaped_summary}"\ndetail: Detail\n'
        f"status: {status}\nimpact: {impact}\n"
        f"q_observations: {q_observations}\nq_value: {q_value}\n"
        f"access_count: {access_count}\nsource_type: {source_type}\n"
        f"source_identity: ''\ntags: [{tag_str}]\n"
        f"created: '2026-02-01'\n",
        encoding="utf-8",
    )


def _write_run(
    base: Path,
    task: str,
    run_id: str,
    events: list[dict[str, object]] | None = None,
    run_yaml_content: dict[str, object] | None = None,
) -> Path:
    """Create a run directory with run.yaml and optional events.jsonl."""
    run_dir = base / ".trw" / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)

    yaml_data: dict[str, object] = run_yaml_content or {
        "run_id": run_id,
        "task": task,
        "status": "active",
        "phase": "implement",
    }
    _writer.write_yaml(meta / "run.yaml", yaml_data)

    if events:
        events_path = meta / "events.jsonl"
        for evt in events:
            _writer.append_jsonl(events_path, evt)

    return run_dir


# ===========================================================================
# analytics.py coverage
# ===========================================================================


class TestIterEntryFilesIndexYamlSkipped:
    """Line 66: index.yaml is skipped by _iter_entry_files."""

    def test_index_yaml_is_skipped(self, trw_dir: Path) -> None:
        """index.yaml file is silently skipped — line 66 (continue)."""
        entries_dir = trw_dir / "learnings" / "entries"
        # Write a valid entry and an index.yaml
        _write_entry(entries_dir, "valid_entry", summary="real learning")
        (entries_dir / "index.yaml").write_text(
            "entries: []\ntotal_count: 0\n", encoding="utf-8"
        )

        results = list(_iter_entry_files(entries_dir))
        # Only the valid entry, not index.yaml
        filenames = [p.name for p, _ in results]
        assert "index.yaml" not in filenames
        assert "valid_entry.yaml" in filenames

    def test_index_yaml_skipped_sorted_order(self, trw_dir: Path) -> None:
        """index.yaml skipped even in sorted_order=True mode — line 66."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "aaa_entry", summary="first learning")
        (entries_dir / "index.yaml").write_text(
            "entries: []\ntotal_count: 0\n", encoding="utf-8"
        )

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
        # Write a corrupt (binary) YAML file
        (entries_dir / "bad_entry.yaml").write_bytes(b"\xff\xfe\x00INVALID\x00")

        results = list(_iter_entry_files(entries_dir))
        filenames = [p.name for p, _ in results]
        # Good entry is returned, corrupt one is skipped
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
        # Only a corrupt file — should return None
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
        # Build a pattern that repeats 3 times: [checkpoint, complete] x3
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
        # success_rate should be count/total_anchors — lines 272-274
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
            events.append({})  # no 'event' key
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
            entries_dir, "resolved_entry",
            status="resolved", q_value=0.9, q_observations=5,
        )
        result = surface_validated_learnings(trw_dir)
        assert result == []

    def test_low_q_entries_excluded(self, trw_dir: Path) -> None:
        """Entries below q_threshold are excluded — line 318."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir, "low_q",
            status="active", q_value=0.3, q_observations=5,
        )
        result = surface_validated_learnings(trw_dir, q_threshold=0.6)
        assert result == []

    def test_low_observations_excluded(self, trw_dir: Path) -> None:
        """Entries below cold_start_threshold are excluded — line 318."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir, "few_obs",
            status="active", q_value=0.9, q_observations=1,
        )
        result = surface_validated_learnings(trw_dir, cold_start_threshold=3)
        assert result == []

    def test_qualified_entries_returned_sorted(self, trw_dir: Path) -> None:
        """Qualified entries returned sorted by q_value descending — lines 312-319."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir, "high_q",
            summary="high q learning", status="active",
            q_value=0.95, q_observations=5,
        )
        _write_entry(
            entries_dir, "mid_q",
            summary="mid q learning", status="active",
            q_value=0.75, q_observations=4,
        )
        _write_entry(
            entries_dir, "too_low",
            summary="too low", status="active",
            q_value=0.4, q_observations=4,
        )
        result = surface_validated_learnings(trw_dir, q_threshold=0.6, cold_start_threshold=3)
        assert len(result) == 2
        # Sorted descending by q_value
        assert result[0]["q_value"] >= result[1]["q_value"]
        assert result[0]["q_value"] == 0.95

    def test_result_fields_present(self, trw_dir: Path) -> None:
        """Result dicts have required keys — lines 319-325."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir, "qualified",
            summary="important learning", status="active",
            q_value=0.8, q_observations=3, tags=["testing"],
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
            entries_dir, "success_entry",
            summary="Success: task_complete pattern discovered",
        )
        result = has_existing_success_learning(
            trw_dir, "Success: task_complete pattern discovered"
        )
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
        # Match only by first 50 chars
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
            entries_dir, "repeated_op",
            summary="repeated operation: file_modified (12x)",
            status="active",
        )
        result = has_existing_mechanical_learning(
            trw_dir, "Repeated operation: file_modified"
        )
        assert result is True

    def test_non_active_entry_ignored(self, trw_dir: Path) -> None:
        """Non-active entries are ignored even if prefix matches — lines 379-380."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(
            entries_dir, "resolved_op",
            summary="repeated operation: build_step (8x)",
            status="resolved",
        )
        result = has_existing_mechanical_learning(trw_dir, "Repeated operation: build_step")
        assert result is False


class TestUpdateLearningIndexOverflow:
    """Lines 444-445: update_learning_index overflow pruning."""

    def test_overflow_prunes_lowest_impact(self, trw_dir: Path) -> None:
        """When entries exceed learning_max_entries, lowest impact entries are pruned — lines 444-445."""
        from trw_mcp.models.config import TRWConfig, _reset_config
        _reset_config()

        # Use a low max_entries to trigger the pruning branch
        config_with_low_max = TRWConfig(
            trw_dir=str(trw_dir),
            learning_max_entries=2,
        )

        # Patch get_config in analytics_entries where it's called
        with patch("trw_mcp.state.analytics.entries.get_config", return_value=config_with_low_max):
            # Create entries via LearningEntry objects
            from datetime import date
            entries = [
                LearningEntry(
                    id=f"L-{i:04d}",
                    summary=f"Learning number {i}",
                    detail="detail",
                    tags=["test"],
                    impact=float(i) / 10.0,
                    source_type="agent",
                    source_identity="test",
                    created=date(2026, 1, i + 1),
                )
                for i in range(3)  # 3 entries, max_entries=2 → triggers pruning
            ]
            for entry in entries:
                update_learning_index(trw_dir, entry)

        index_path = trw_dir / "learnings" / "index.yaml"
        assert index_path.exists()
        data = _reader.read_yaml(index_path)
        # After pruning, only 2 entries should remain
        assert len(data["entries"]) <= 2


class TestMarkPromotedNoEntriesDir:
    """Line 607: mark_promoted returns early when entries_dir doesn't exist."""

    def test_nonexistent_entries_dir_returns_silently(self, tmp_path: Path) -> None:
        """mark_promoted returns without error when entries_dir missing — line 607."""
        fake_trw = tmp_path / ".trw_no_entries"
        # Should not raise — early return when entries dir doesn't exist
        mark_promoted(fake_trw, "L-nonexistent")
        # Confirm no entries dir was created as a side effect
        assert not (fake_trw / "learnings" / "entries").exists()

    def test_mark_promoted_sets_flag(self, trw_dir: Path) -> None:
        """mark_promoted writes promoted_to_claude_md=True to entry file."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "promote_me", learning_id="L-promote-me")

        mark_promoted(trw_dir, "L-promote-me")

        data = _reader.read_yaml(entries_dir / "promote_me.yaml")
        assert data["promoted_to_claude_md"] is True

    def test_mark_promoted_missing_id_no_error(self, trw_dir: Path) -> None:
        """mark_promoted with non-existent ID does nothing silently."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "some_entry", learning_id="L-some-entry")
        # Should not raise for missing ID
        mark_promoted(trw_dir, "L-nonexistent-id")
        # Existing entry should remain unchanged (no promoted_to_claude_md added)
        data = _reader.read_yaml(entries_dir / "some_entry.yaml")
        assert data.get("promoted_to_claude_md") is not True


class TestApplyStatusUpdateEdgeCases:
    """Lines 626, 634: apply_status_update edge cases."""

    def test_nonexistent_entries_dir_returns_silently(self, tmp_path: Path) -> None:
        """apply_status_update returns early when entries_dir missing — line 626."""
        fake_trw = tmp_path / ".trw_no_entries"
        # Should not raise — early return when entries dir doesn't exist
        apply_status_update(fake_trw, "L-nonexistent", "resolved")
        # Confirm no entries dir was created as a side effect
        assert not (fake_trw / "learnings" / "entries").exists()

    def test_resolved_status_adds_resolved_at(self, trw_dir: Path) -> None:
        """Resolved status adds resolved_at field — line 634."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "resolve_me", learning_id="L-resolve-me")

        apply_status_update(trw_dir, "L-resolve-me", LearningStatus.RESOLVED.value)

        data = _reader.read_yaml(entries_dir / "resolve_me.yaml")
        assert data["status"] == "resolved"
        assert "resolved_at" in data
        assert data["resolved_at"] is not None

    def test_obsolete_status_no_resolved_at(self, trw_dir: Path) -> None:
        """Obsolete status does not add resolved_at — confirms line 634 branch not taken."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "obsolete_me", learning_id="L-obsolete-me")

        apply_status_update(trw_dir, "L-obsolete-me", "obsolete")

        data = _reader.read_yaml(entries_dir / "obsolete_me.yaml")
        assert data["status"] == "obsolete"
        assert "resolved_at" not in data


class TestAutoPruneNonexistentDir:
    """Line 840: auto_prune_excess_entries when entries_dir doesn't exist."""

    def test_nonexistent_entries_dir_returns_empty(self, tmp_path: Path) -> None:
        """Returns empty result when entries_dir doesn't exist — line 840."""
        fake_trw = tmp_path / ".trw_no_entries"
        result = auto_prune_excess_entries(fake_trw, max_entries=100)
        assert result["actions_taken"] == 0
        assert result["dedup_candidates"] == []
        assert result["utility_candidates"] == []


class TestAutoPruneUtilityCandidates:
    """Lines 873-878: utility candidate pruning in auto_prune_excess_entries."""

    def test_utility_candidates_with_suggested_status_applied(self, trw_dir: Path) -> None:
        """Utility candidates with suggested_status are applied — lines 873-878."""
        entries_dir = trw_dir / "learnings" / "entries"
        # Create enough active entries to exceed threshold
        for i in range(6):
            _write_entry(
                entries_dir,
                f"entry_{i:02d}",
                summary=f"Unique learning topic {i} about subject {i}",
                status="active",
                impact=0.1 + i * 0.05,
                q_observations=0,
                q_value=0.1,
                learning_id=f"L-entry_{i:02d}",
            )

        # utility_based_prune_candidates is imported locally inside auto_prune_excess_entries
        # from trw_mcp.scoring — patch at the source module
        fake_candidates = [
            {"id": "L-entry_00", "suggested_status": "obsolete"},
            {"id": "L-entry_01", "suggested_status": "resolved"},
            {"id": "", "suggested_status": "obsolete"},  # empty id — skipped
        ]

        with patch(
            "trw_mcp.scoring.utility_based_prune_candidates",
            return_value=fake_candidates,
        ):
            result = auto_prune_excess_entries(
                trw_dir, max_entries=3, dry_run=False
            )

        # Actions taken includes utility pruning
        assert result["actions_taken"] > 0

    def test_utility_candidate_invalid_status_skipped(self, trw_dir: Path) -> None:
        """Utility candidates with invalid suggested_status are skipped — line 876."""
        entries_dir = trw_dir / "learnings" / "entries"
        for i in range(5):
            _write_entry(
                entries_dir,
                f"e_{i:02d}",
                summary=f"Topic {i} about something entirely different",
                status="active",
                learning_id=f"L-e_{i:02d}",
            )

        fake_candidates = [
            {"id": "L-e_00", "suggested_status": "invalid_status"},  # should be skipped
            {"id": "L-e_01", "suggested_status": ""},  # empty — skipped
        ]

        with patch(
            "trw_mcp.scoring.utility_based_prune_candidates",
            return_value=fake_candidates,
        ):
            result = auto_prune_excess_entries(
                trw_dir, max_entries=3, dry_run=False
            )

        # No utility actions from these invalid-status candidates
        assert result is not None
        assert isinstance(result, dict)
        assert "actions_taken" in result
        assert result["actions_taken"] == 0


class TestComputeReflectionQualityExceptionHandling:
    """Lines 929-930: exception handling in compute_reflection_quality."""

    def test_corrupt_reflection_file_skipped(self, trw_dir: Path) -> None:
        """Corrupt reflection YAML is skipped with continue — lines 929-930."""
        reflections_dir = trw_dir / "reflections"
        # Write a valid reflection
        (reflections_dir / "valid_reflection.yaml").write_text(
            "id: R-valid\nscope: session\nnew_learnings: [L-a, L-b]\n"
            "timestamp: '2026-01-01T00:00:00Z'\nevents_analyzed: 3\n"
            "what_worked: []\nwhat_failed: []\nrepeated_patterns: []\n",
            encoding="utf-8",
        )
        # Write a corrupt reflection file
        (reflections_dir / "corrupt_reflection.yaml").write_bytes(
            b"\xff\xfe INVALID YAML \x00"
        )

        result = compute_reflection_quality(trw_dir)
        # Should not raise; valid reflection counted
        assert result["diagnostics"]["reflection_count"] == 1
        assert result["score"] >= 0.0

    def test_all_corrupt_reflections_returns_zero_score(self, trw_dir: Path) -> None:
        """All corrupt reflections produce zero reflection components."""
        reflections_dir = trw_dir / "reflections"
        (reflections_dir / "bad1.yaml").write_bytes(b"\xff\xfe\x00\x01")
        (reflections_dir / "bad2.yaml").write_bytes(b"\xff\xfe\x00\x02")

        result = compute_reflection_quality(trw_dir)
        assert result["components"]["reflection_frequency"] == 0.0
        assert result["components"]["productivity"] == 0.0


class TestBackfillSourceAttribution:
    """Lines 1030-1052: backfill_source_attribution function."""

    def test_nonexistent_entries_dir_returns_zeros(self, tmp_path: Path) -> None:
        """Returns zero counts when entries_dir doesn't exist — line 1031-1032."""
        fake_trw = tmp_path / ".trw_no_entries"
        result = backfill_source_attribution(fake_trw)
        assert result["updated_count"] == 0
        assert result["skipped_count"] == 0
        assert result["total_scanned"] == 0

    def test_entries_with_valid_source_type_skipped(self, trw_dir: Path) -> None:
        """Entries with valid source_type ('human' or 'agent') are skipped — lines 1042-1044."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "agent_entry", source_type="agent")
        _write_entry(entries_dir, "human_entry", source_type="human")

        result = backfill_source_attribution(trw_dir)
        assert result["updated_count"] == 0
        assert result["skipped_count"] == 2
        assert result["total_scanned"] == 2

    def test_entries_missing_source_type_are_updated(self, trw_dir: Path) -> None:
        """Entries with missing/invalid source_type get backfilled — lines 1046-1050."""
        entries_dir = trw_dir / "learnings" / "entries"
        # Write entry without source_type
        (entries_dir / "no_source.yaml").write_text(
            "id: L-no-source\nsummary: Old entry without source\ndetail: detail\n"
            "status: active\nimpact: 0.5\ntags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )
        # Write entry with invalid source_type
        (entries_dir / "bad_source.yaml").write_text(
            "id: L-bad-source\nsummary: Old entry with bad source\ndetail: detail\n"
            "status: active\nimpact: 0.5\nsource_type: unknown_type\n"
            "tags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )

        result = backfill_source_attribution(trw_dir, dry_run=False)
        assert result["updated_count"] == 2
        assert result["skipped_count"] == 0
        assert result["total_scanned"] == 2
        assert result["dry_run"] is False

        # Verify files were actually updated
        data1 = _reader.read_yaml(entries_dir / "no_source.yaml")
        assert data1["source_type"] == "agent"
        assert data1["source_identity"] == ""
        assert "updated" in data1

        data2 = _reader.read_yaml(entries_dir / "bad_source.yaml")
        assert data2["source_type"] == "agent"

    def test_dry_run_does_not_modify_files(self, trw_dir: Path) -> None:
        """dry_run=True reports count without modifying files — line 1045."""
        entries_dir = trw_dir / "learnings" / "entries"
        (entries_dir / "no_source.yaml").write_text(
            "id: L-no-source\nsummary: Old entry\ndetail: detail\n"
            "status: active\nimpact: 0.5\ntags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )
        original_content = (entries_dir / "no_source.yaml").read_text()

        result = backfill_source_attribution(trw_dir, dry_run=True)
        assert result["updated_count"] == 1
        assert result["dry_run"] is True

        # File should NOT be modified
        assert (entries_dir / "no_source.yaml").read_text() == original_content

    def test_mixed_entries_counted_correctly(self, trw_dir: Path) -> None:
        """Mix of valid and invalid source_types counted correctly."""
        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "valid_agent", source_type="agent")
        _write_entry(entries_dir, "valid_human", source_type="human")
        (entries_dir / "no_src.yaml").write_text(
            "id: L-no-src\nsummary: Entry without source\ndetail: d\n"
            "status: active\nimpact: 0.5\ntags: []\ncreated: '2026-01-01'\n",
            encoding="utf-8",
        )

        result = backfill_source_attribution(trw_dir, dry_run=False)
        assert result["total_scanned"] == 3
        assert result["skipped_count"] == 2
        assert result["updated_count"] == 1


# ===========================================================================
# analytics_report.py coverage
# ===========================================================================


class TestAnalyzeRunExceptionHandling:
    """Lines 193, 209-210, 215-216: _analyze_single_run exception paths."""

    def test_unreadable_run_yaml_returns_none(self, tmp_path: Path) -> None:
        """Unreadable run.yaml returns None from _analyze_single_run — line 193."""
        run_dir = tmp_path / "meta_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        # Write binary garbage that fails YAML parse
        (meta / "run.yaml").write_bytes(b"\xff\xfe\x00\x01 INVALID \x00")

        result = _analyze_single_run(run_dir)
        assert result is None

    def test_missing_run_yaml_returns_none(self, tmp_path: Path) -> None:
        """Missing run.yaml returns None — line 193 via line 192 exists() check."""
        run_dir = tmp_path / "empty_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        # No run.yaml written

        result = _analyze_single_run(run_dir)
        assert result is None

    def test_corrupt_events_jsonl_run_still_scanned(self, tmp_path: Path) -> None:
        """Corrupt events.jsonl is skipped; run still analyzed — lines 209-210."""
        run_dir = tmp_path / "corrupt_events_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        _writer.write_yaml(meta / "run.yaml", {
            "run_id": "20260101T000000Z-corrpt00",
            "task": "test",
            "status": "active",
            "phase": "implement",
        })
        # Write corrupt (non-JSON) events.jsonl
        (meta / "events.jsonl").write_bytes(b"\xff\xfe INVALID JSON CONTENT \x00")

        # Monkeypatch the reader to raise on read_jsonl
        original_read_jsonl = _reader.read_jsonl

        def raise_on_read(path: Path) -> list[dict[str, object]]:
            if "corrupt_events" in str(path):
                raise ValueError("simulated parse error")
            return original_read_jsonl(path)

        with patch.object(
            analytics_mod._reader, "read_jsonl", side_effect=raise_on_read
        ):
            result = _analyze_single_run(run_dir)

        # Run should still be analyzed with score 0
        assert result is not None
        assert isinstance(result, dict)
        assert result["score"] == 0
        assert "run_id" in result

    def test_ceremony_score_exception_returns_null_score(self, tmp_path: Path) -> None:
        """compute_ceremony_score exception results in null score — lines 215-216."""
        run_dir = tmp_path / "bad_ceremony_run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        _writer.write_yaml(meta / "run.yaml", {
            "run_id": "20260101T000000Z-ceremon0",
            "task": "test",
            "status": "active",
            "phase": "implement",
        })

        with patch(
            "trw_mcp.state.analytics.report.compute_ceremony_score",
            side_effect=RuntimeError("scoring exploded"),
        ):
            result = _analyze_single_run(run_dir)

        assert result is not None
        assert isinstance(result, dict)
        # score should be None (from the except-clause fallback)
        assert result["score"] is None
        assert result["session_start"] is False
        assert result["deliver"] is False
        assert "run_id" in result


class TestScanAllRunsExceptionPaths:
    """Lines 146-147, 180-181: scan_all_runs exception handling."""

    def test_run_dir_analysis_exception_added_to_parse_errors(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception in _analyze_single_run is caught and added to parse_errors — lines 146-147."""
        from trw_mcp.models.config import TRWConfig
        mock_cfg = TRWConfig(task_root="docs")
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod, "get_config", lambda: mock_cfg)
        monkeypatch.setattr(
            analytics_mod, "resolve_trw_dir", lambda: tmp_path / ".trw"
        )

        # Create a run directory with a valid run.yaml structure
        run_dir = tmp_path / ".trw" / "runs" / "task-exc" / "20260101T000000Z-exc00000"
        (run_dir / "meta").mkdir(parents=True)
        _writer.write_yaml(run_dir / "meta" / "run.yaml", {
            "run_id": "20260101T000000Z-exc00000",
            "task": "task-exc",
            "status": "active",
            "phase": "implement",
        })

        # Make _analyze_single_run raise for this specific run
        original_analyze = analytics_mod._analyze_single_run

        def raising_analyze(run_dir_arg: Path) -> dict[str, object] | None:
            if "exc00000" in run_dir_arg.name:
                raise RuntimeError("forced analysis error")
            return original_analyze(run_dir_arg)

        monkeypatch.setattr(analytics_mod, "_analyze_single_run", raising_analyze)

        result = scan_all_runs()
        assert any("exc00000" in str(e) for e in result["parse_errors"])
        assert result["runs_scanned"] == 0

    def test_cache_write_exception_does_not_propagate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cache write exception is swallowed — lines 180-181 (except Exception: pass).

        Must create at least one valid run so the function reaches the cache write
        section rather than returning early via _empty_report.
        """
        from trw_mcp.models.config import TRWConfig
        mock_cfg = TRWConfig(task_root="docs")
        monkeypatch.setattr(analytics_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(analytics_mod, "get_config", lambda: mock_cfg)

        # Create a valid run so task_root.exists() is True and we reach the cache section
        _write_run(
            tmp_path,
            "cache-exc-task",
            "20260101T000000Z-cacheexc0",
            events=[{"event": "session_start"}],
        )

        # Make resolve_trw_dir succeed for the scan loop (call #1, passing trw_dir
        # to _analyze_single_run) but raise for the cache write section (call #2).
        # This ensures the run is analyzed successfully before the cache write fails.
        _call_count = [0]

        def _call_counted_resolve_trw_dir() -> Path:
            _call_count[0] += 1
            if _call_count[0] == 1:
                return tmp_path / ".trw"
            raise RuntimeError("no trw dir available")

        monkeypatch.setattr(analytics_mod, "resolve_trw_dir", _call_counted_resolve_trw_dir)

        # Should not raise even though cache write fails
        result = scan_all_runs()
        assert "runs" in result
        assert "aggregate" in result
        assert result["runs_scanned"] == 1


class TestCeremonyScoreToolInvocationPaths:
    """Additional compute_ceremony_score paths for tool_invocation events."""

    def test_tool_invocation_session_start(self) -> None:
        """tool_invocation event with tool_name=trw_session_start counts as session_start."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_session_start"},
        ]
        result = compute_ceremony_score(events)
        assert result["session_start"] is True
        assert result["score"] == 25

    def test_tool_invocation_deliver(self) -> None:
        """tool_invocation with tool_name=trw_deliver counts as deliver."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_deliver"},
        ]
        result = compute_ceremony_score(events)
        assert result["deliver"] is True
        assert result["score"] == 25

    def test_tool_invocation_reflect(self) -> None:
        """tool_invocation with tool_name=trw_reflect counts as deliver."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_reflect"},
        ]
        result = compute_ceremony_score(events)
        assert result["deliver"] is True

    def test_tool_invocation_checkpoint(self) -> None:
        """tool_invocation with tool_name=trw_checkpoint counts as checkpoint."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
        ]
        result = compute_ceremony_score(events)
        assert result["checkpoint_count"] == 1
        assert result["score"] == 15

    def test_tool_invocation_learn(self) -> None:
        """tool_invocation with tool_name=trw_learn counts as learn."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_learn"},
        ]
        result = compute_ceremony_score(events)
        assert result["learn_count"] == 1
        assert result["score"] == 10

    def test_tool_invocation_build_check(self) -> None:
        """tool_invocation with tool_name=trw_build_check counts as build_check."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_build_check", "tests_passed": "true"},
        ]
        result = compute_ceremony_score(events)
        assert result["build_check"] is True
        assert result["score"] == 10

    def test_trw_deliver_complete_event(self) -> None:
        """trw_deliver_complete event counts as deliver."""
        events: list[dict[str, object]] = [
            {"event": "trw_deliver_complete"},
        ]
        result = compute_ceremony_score(events)
        assert result["deliver"] is True
        assert result["score"] == 25


# ===========================================================================
# PRD-FIX-033: Deliver Performance — SQLite Migration Tests
# ===========================================================================


class TestAutoPruneUsesSQLite:
    """PRD-FIX-033-FR02: auto_prune_excess_entries uses SQLite when available."""

    def test_auto_prune_uses_sqlite(self, trw_dir: Path) -> None:
        """auto_prune calls list_entries_by_status instead of _iter_entry_files."""
        entries_dir = trw_dir / "learnings" / "entries"
        # Create YAML entries so entries_dir.is_dir() passes
        for i in range(5):
            _write_entry(
                entries_dir, f"sq_{i:02d}",
                summary=f"Unique topic {i} about subject {i * 10}",
                status="active", impact=0.5,
                learning_id=f"L-sq_{i:02d}",
            )

        # Mock list_entries_by_status to return pre-built list
        fake_entries: list[dict[str, object]] = [
            {
                "id": f"L-sq_{i:02d}",
                "summary": f"Unique topic {i} about subject {i * 10}",
                "status": "active",
                "impact": 0.5,
                "tags": [],
                "detail": "detail",
                "created": "2026-02-01",
                "last_accessed_at": "2026-02-01",
                "q_value": 0.5,
                "q_observations": 0,
                "recurrence": 1,
                "access_count": 0,
                "source_type": "agent",
            }
            for i in range(5)
        ]

        # Patch at the source module since it's imported locally
        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            return_value=fake_entries,
        ) as mock_sqlite, patch(
            "trw_mcp.scoring.utility_based_prune_candidates",
            return_value=[],
        ):
            result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=True)

        mock_sqlite.assert_called_once()
        assert result["active_count"] == 5

    def test_auto_prune_fallback_to_yaml(self, trw_dir: Path) -> None:
        """Falls back to YAML when SQLite raises, with warning logged."""
        entries_dir = trw_dir / "learnings" / "entries"
        for i in range(5):
            _write_entry(
                entries_dir, f"fb_{i:02d}",
                summary=f"Fallback topic {i} about thing {i * 100}",
                status="active", impact=0.5,
                learning_id=f"L-fb_{i:02d}",
            )

        # Patch at the source module since it's imported locally
        with patch(
            "trw_mcp.state.memory_adapter.list_entries_by_status",
            side_effect=RuntimeError("SQLite unavailable"),
        ):
            result = auto_prune_excess_entries(trw_dir, max_entries=3, dry_run=True)

        # YAML fallback should still work
        assert result["active_count"] == 5


class TestFindDuplicateLearningsEntriesParam:
    """PRD-FIX-033-FR03: find_duplicate_learnings accepts pre-loaded entries."""

    def test_with_entries_param_skips_yaml(self, trw_dir: Path) -> None:
        """When entries param provided, YAML files are not read."""
        from trw_mcp.state.analytics import find_duplicate_learnings

        entries_dir = trw_dir / "learnings" / "entries"
        # Pre-loaded entries with duplicates
        pre_loaded: list[dict[str, object]] = [
            {"id": "L-a1", "summary": "python testing gotcha mock", "status": "active"},
            {"id": "L-a2", "summary": "python testing gotcha mock pattern", "status": "active"},
            {"id": "L-a3", "summary": "completely different topic rust", "status": "active"},
        ]

        results = find_duplicate_learnings(entries_dir, threshold=0.6, entries=pre_loaded)
        # L-a1 and L-a2 should be flagged as duplicates
        assert len(results) >= 1
        pair_ids = {(r[0], r[1]) for r in results}
        assert ("L-a1", "L-a2") in pair_ids

    def test_backward_compat_without_entries_param(self, trw_dir: Path) -> None:
        """Without entries param, existing YAML scan path works."""
        from trw_mcp.state.analytics import find_duplicate_learnings

        entries_dir = trw_dir / "learnings" / "entries"
        _write_entry(entries_dir, "dup1", summary="exact same words here", learning_id="L-dup1")
        _write_entry(entries_dir, "dup2", summary="exact same words here", learning_id="L-dup2")

        results = find_duplicate_learnings(entries_dir, threshold=0.9)
        assert len(results) >= 1

    def test_entries_param_filters_active_only(self, trw_dir: Path) -> None:
        """Pre-loaded entries with non-active status are filtered out."""
        from trw_mcp.state.analytics import find_duplicate_learnings

        entries_dir = trw_dir / "learnings" / "entries"
        pre_loaded: list[dict[str, object]] = [
            {"id": "L-x1", "summary": "same words here", "status": "active"},
            {"id": "L-x2", "summary": "same words here", "status": "obsolete"},
        ]

        results = find_duplicate_learnings(entries_dir, threshold=0.9, entries=pre_loaded)
        # L-x2 is obsolete so should not be compared
        assert len(results) == 0
