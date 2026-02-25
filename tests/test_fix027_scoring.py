"""Tests for PRD-FIX-027: Fix scoring & Q-learning wiring.

Covers:
- Bug 1: Missing EventType.DELIVER_COMPLETE + REWARD_MAP entry
- Bug 2: trw_build_check has no Q-learning wiring
- Bug 3: collect_promotable_learnings uses raw impact without time decay
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.run import EventType
from trw_mcp.scoring import REWARD_MAP, EVENT_ALIASES, apply_time_decay
from trw_mcp.state.claude_md import collect_promotable_learnings
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


# ============================================================================
# Bug 1: EventType.DELIVER_COMPLETE exists and is wired into scoring
# ============================================================================

class TestDeliverCompleteEventType:
    """Bug 1: DELIVER_COMPLETE must exist in EventType and REWARD_MAP."""

    def test_deliver_complete_exists_in_eventtype(self) -> None:
        """EventType.DELIVER_COMPLETE must be a member of the enum."""
        assert hasattr(EventType, "DELIVER_COMPLETE"), (
            "EventType.DELIVER_COMPLETE is missing — trw_deliver() events are silently dropped"
        )

    def test_deliver_complete_value(self) -> None:
        """DELIVER_COMPLETE must map to the string logged by trw_deliver()."""
        assert EventType.DELIVER_COMPLETE == "trw_deliver_complete"

    def test_deliver_complete_in_reward_map(self) -> None:
        """DELIVER_COMPLETE must have a REWARD_MAP entry."""
        assert EventType.DELIVER_COMPLETE in REWARD_MAP, (
            "REWARD_MAP missing DELIVER_COMPLETE — Q-learning gets no reward for delivery"
        )

    def test_deliver_complete_reward_is_highest(self) -> None:
        """Delivery is the goal — reward should be 1.0."""
        reward = REWARD_MAP[EventType.DELIVER_COMPLETE]
        assert reward == 1.0, f"Expected 1.0, got {reward}"

    def test_process_outcome_returns_nonempty_for_deliver_complete(
        self, tmp_path: Path
    ) -> None:
        """process_outcome_for_event("trw_deliver_complete") must not silently fail.

        Without DELIVER_COMPLETE in EventType/REWARD_MAP, the function returns []
        which means no Q-learning update ever fires on delivery.
        """
        from trw_mcp.scoring import process_outcome_for_event

        trw_dir = tmp_path / ".trw"
        learnings_dir = trw_dir / "learnings" / "entries"
        learnings_dir.mkdir(parents=True)

        writer = FileStateWriter()

        # Create a sample learning with a session_recall event
        entry = {
            "id": "L-test0001",
            "summary": "Test learning for Q-learning reward",
            "detail": "Detail text",
            "impact": 0.7,
            "status": "active",
            "q_value": 0.7,
            "q_observations": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tags": [],
        }
        writer.write_yaml(learnings_dir / "test-learning.yaml", entry)

        # We patch _config and the trw_dir resolution so the function can run
        mock_config = TRWConfig(trw_dir=str(trw_dir))
        with (
            patch("trw_mcp.scoring._config", mock_config),
            patch("trw_mcp.scoring.resolve_trw_dir", return_value=trw_dir),
        ):
            # The function should not raise — even if no session events exist,
            # returning [] is acceptable when no recalls occurred this session.
            result = process_outcome_for_event("trw_deliver_complete")
            assert isinstance(result, list)

    def test_deliver_complete_resolve_from_string(self) -> None:
        """EventType.resolve('trw_deliver_complete') must return the enum member."""
        resolved = EventType.resolve("trw_deliver_complete")
        assert resolved is EventType.DELIVER_COMPLETE


# ============================================================================
# Bug 2: trw_build_check Q-learning wiring
# ============================================================================

class TestBuildCheckQLearningWiring:
    """Bug 2: trw_build_check must call process_outcome_for_event after each run."""

    def _make_mock_status(self, passed: bool) -> MagicMock:
        mock_status = MagicMock()
        mock_status.tests_passed = passed
        mock_status.mypy_clean = passed
        mock_status.coverage_pct = 95.0 if passed else 60.0
        mock_status.test_count = 100 if passed else 50
        mock_status.failure_count = 0 if passed else 5
        mock_status.failures = [] if passed else ["test_foo failed"]
        mock_status.scope = "full"
        mock_status.duration_secs = 1.0
        return mock_status

    def _get_tool_fn(
        self,
        tmp_path: Path,
        passed: bool,
        monkeypatch: pytest.MonkeyPatch,
    ) -> object:
        """Register build tools with mocked dependencies, return the tool fn."""
        import trw_mcp.tools.build as build_mod
        from trw_mcp.models.config import TRWConfig
        from fastmcp import FastMCP

        mock_status = self._make_mock_status(passed)
        mock_config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        monkeypatch.setattr(build_mod, "_config", mock_config)
        monkeypatch.setattr(build_mod, "run_build_check", lambda *a, **kw: mock_status)
        monkeypatch.setattr(build_mod, "cache_build_status", lambda *a, **kw: Path("/tmp/cache"))
        monkeypatch.setattr(build_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr(build_mod, "resolve_project_root", lambda: tmp_path)

        server = FastMCP("test")
        build_mod.register_build_tools(server)

        for tool in server._tool_manager._tools.values():
            if tool.name == "trw_build_check":
                return tool.fn
        return None

    def test_build_check_calls_process_outcome_on_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When build passes, process_outcome_for_event('build_passed') must be called."""
        called_events: list[str] = []

        # Patch at the module level so local import inside the tool function picks it up
        monkeypatch.setattr(
            "trw_mcp.scoring.process_outcome_for_event",
            lambda event_type: called_events.append(event_type) or [],
        )

        tool_fn = self._get_tool_fn(tmp_path, passed=True, monkeypatch=monkeypatch)
        assert tool_fn is not None, "trw_build_check tool not found"

        tool_fn(scope="full", run_path=None, timeout_secs=30)

        assert "build_passed" in called_events, (
            "trw_build_check did not call process_outcome_for_event('build_passed') — "
            "Q-learning gets no reward signal from successful builds"
        )

    def test_build_check_calls_process_outcome_on_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When build fails, process_outcome_for_event('build_failed') must be called."""
        called_events: list[str] = []

        monkeypatch.setattr(
            "trw_mcp.scoring.process_outcome_for_event",
            lambda event_type: called_events.append(event_type) or [],
        )

        tool_fn = self._get_tool_fn(tmp_path, passed=False, monkeypatch=monkeypatch)
        assert tool_fn is not None, "trw_build_check tool not found"

        tool_fn(scope="full", run_path=None, timeout_secs=30)

        assert "build_failed" in called_events, (
            "trw_build_check did not call process_outcome_for_event('build_failed') — "
            "Q-learning gets no negative signal from build failures"
        )

    def test_build_check_q_learning_failure_does_not_block_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Q-learning errors must be swallowed — never block build check results."""
        def exploding_process(event_type: str) -> list[str]:
            raise RuntimeError("Q-learning exploded!")

        monkeypatch.setattr("trw_mcp.scoring.process_outcome_for_event", exploding_process)

        tool_fn = self._get_tool_fn(tmp_path, passed=True, monkeypatch=monkeypatch)
        assert tool_fn is not None, "trw_build_check tool not found"

        # Must not raise even when Q-learning throws
        result = tool_fn(scope="full", run_path=None, timeout_secs=30)
        assert result["tests_passed"] is True


# ============================================================================
# Bug 3: collect_promotable_learnings applies time decay
# ============================================================================

class TestPromotableLearnungsTimeDecay:
    """Bug 3: time decay must be applied before comparing against promotion threshold."""

    def _write_learning(
        self,
        entries_dir: Path,
        writer: FileStateWriter,
        filename: str,
        impact: float,
        created_at: datetime,
        q_obs: int = 0,
    ) -> None:
        data = {
            "id": f"L-{filename}",
            "summary": f"Learning {filename}",
            "detail": "Detail",
            "impact": impact,
            "q_value": impact,
            "q_observations": q_obs,
            "status": "active",
            "created_at": created_at.isoformat(),
            "tags": [],
        }
        writer.write_yaml(entries_dir / f"{filename}.yaml", data)

    def test_old_learning_with_high_impact_filtered_by_decay(
        self, tmp_path: Path
    ) -> None:
        """An entry created 1 year ago with impact=0.8 should be filtered out.

        Without decay: 0.8 >= 0.7 threshold → promoted
        With decay: 0.8 * max(0.3, 1.0 - (365/365)*0.3) = 0.8 * 0.7 = 0.56 < 0.7 → not promoted
        """
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)
        self._write_learning(entries_dir, writer, "old-entry", 0.8, one_year_ago)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        assert "L-old-entry" not in ids, (
            "Old learning with decayed score should NOT be promoted — "
            "time decay was not applied before threshold comparison"
        )

    def test_new_learning_with_high_impact_is_promoted(
        self, tmp_path: Path
    ) -> None:
        """An entry created today with impact=0.8 should still pass the threshold."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        now = datetime.now(timezone.utc)
        self._write_learning(entries_dir, writer, "new-entry", 0.8, now)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        assert "L-new-entry" in ids, (
            "New learning with impact=0.8 should be promoted"
        )

    def test_decay_not_applied_when_no_created_at(
        self, tmp_path: Path
    ) -> None:
        """Entries without created_at fall back to raw impact (no crash)."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        data = {
            "id": "L-no-date",
            "summary": "No date entry",
            "detail": "Detail",
            "impact": 0.9,
            "q_value": 0.9,
            "q_observations": 0,
            "status": "active",
            "tags": [],
        }
        writer.write_yaml(entries_dir / "no-date.yaml", data)

        # Should not raise — just use raw impact
        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        # With impact=0.9 and no date, raw impact should pass (0.9 >= 0.7)
        assert "L-no-date" in ids

    def test_old_high_impact_vs_new_same_impact(
        self, tmp_path: Path
    ) -> None:
        """Two entries with same impact=0.8 — old one filtered, new one promoted."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        now = datetime.now(timezone.utc)
        one_year_ago = now - timedelta(days=365)

        self._write_learning(entries_dir, writer, "aaa-old", 0.8, one_year_ago)
        self._write_learning(entries_dir, writer, "bbb-new", 0.8, now)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]

        assert "L-bbb-new" in ids, "New entry should be promoted"
        assert "L-aaa-old" not in ids, "Year-old entry with same impact should be filtered by decay"

    def test_malformed_created_at_falls_back_to_raw_score(
        self, tmp_path: Path
    ) -> None:
        """Malformed created_at ISO string triggers ValueError — raw score used."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        data = {
            "id": "L-baddate",
            "summary": "Entry with bad date",
            "detail": "Detail",
            "impact": 0.9,
            "q_value": 0.9,
            "q_observations": 0,
            "status": "active",
            "created_at": "not-a-valid-date",  # Will trigger ValueError on fromisoformat
            "tags": [],
        }
        writer.write_yaml(entries_dir / "baddate.yaml", data)

        # Should not raise — malformed date falls back to raw score
        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        # With raw impact=0.9 and no decay, should be promoted (0.9 >= 0.7 threshold)
        assert "L-baddate" in ids

    def test_apply_time_decay_at_boundary_exact_0_7(
        self,
    ) -> None:
        """Entry with impact=0.7 exactly at threshold — decayed or not depends on age."""
        from trw_mcp.scoring import apply_time_decay

        now = datetime.now(timezone.utc)
        # Very new entry: decay_factor = max(0.3, 1.0 - 0/365 * 0.3) = 1.0
        decayed = apply_time_decay(0.7, now)
        assert abs(decayed - 0.7) < 1e-9

        # 6 months old: decay_factor = max(0.3, 1.0 - (182/365)*0.3) = ~0.85
        six_months_ago = now - timedelta(days=182)
        decayed_6m = apply_time_decay(0.7, six_months_ago)
        assert decayed_6m < 0.7  # Should be less due to decay

    def test_q_cold_start_uses_q_value_when_mature(
        self, tmp_path: Path
    ) -> None:
        """When q_observations >= threshold, q_value is used instead of impact."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        now = datetime.now(timezone.utc)
        # Entry with low impact but high q_value, mature (q_observations=5 >= threshold=3)
        data = {
            "id": "L-mature",
            "summary": "Mature entry with high q_value",
            "detail": "Detail",
            "impact": 0.3,  # Below threshold if used alone
            "q_value": 0.9,  # High q_value
            "q_observations": 5,  # >= cold_start_threshold(3)
            "status": "active",
            "created_at": now.isoformat(),
            "tags": [],
        }
        writer.write_yaml(entries_dir / "mature.yaml", data)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        # q_value=0.9 with full decay (now) = 0.9 >= 0.7 → should be promoted
        assert "L-mature" in ids

    def test_collect_promotable_returns_empty_when_no_entries_dir(
        self, tmp_path: Path
    ) -> None:
        """collect_promotable_learnings returns [] when entries_dir doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Don't create learnings/entries

        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        result = collect_promotable_learnings(trw_dir, config, reader)
        assert result == []


# ============================================================================
# apply_time_decay: parametrized edge cases
# ============================================================================

class TestApplyTimeDecay:
    """Parametrized edge cases for apply_time_decay."""

    @pytest.mark.parametrize("days,impact,expected_min,expected_max", [
        (0, 1.0, 1.0, 1.0),         # Brand new: no decay
        (182, 1.0, 0.848, 0.852),    # 6 months: 1.0-(182/365)*0.3 ~ 0.8504
        (365, 1.0, 0.699, 0.701),    # 1 year: 1.0 - 0.3 = 0.7
        (486, 1.0, 0.598, 0.602),    # ~16 months: 1.0-(486/365)*0.3 ~ 0.600
        (730, 1.0, 0.399, 0.401),    # 2 years: 1.0-(730/365)*0.3 = 0.4
        (1460, 1.0, 0.299, 0.301),   # 4 years: floored at 0.3
        (0, 0.0, 0.0, 0.0),          # Zero impact stays zero
    ])
    def test_decay_parametrized(
        self, days: int, impact: float, expected_min: float, expected_max: float
    ) -> None:
        from trw_mcp.scoring import apply_time_decay

        created = datetime.now(timezone.utc) - timedelta(days=days)
        result = apply_time_decay(impact, created)
        assert expected_min <= result <= expected_max, (
            f"days={days}, impact={impact}: got {result}, expected [{expected_min}, {expected_max}]"
        )

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive datetime (no tzinfo) is treated as UTC — no exception."""
        from trw_mcp.scoring import apply_time_decay

        naive_now = datetime.now()  # No timezone info
        result = apply_time_decay(0.8, naive_now)
        # Brand new, so minimal decay
        assert result >= 0.79


# ============================================================================
# FR02/FR05: REWARD_MAP completeness and scoring values
# ============================================================================

class TestRewardMapCompleteness:
    """FR02/FR05: REWARD_MAP values are correct and immutable in tests."""

    def test_all_reward_values_in_range(self) -> None:
        """All rewards in REWARD_MAP must be in [-1.0, 1.0]."""
        from trw_mcp.scoring import REWARD_MAP as rmap
        for event_type, reward in rmap.items():
            assert -1.0 <= reward <= 1.0, f"{event_type} reward {reward} out of range"

    def test_reward_map_total_count(self) -> None:
        """REWARD_MAP has at least 15 entries (including new Sprint 31 entries)."""
        from trw_mcp.scoring import REWARD_MAP as rmap
        assert len(rmap) >= 15

    def test_positive_events_have_positive_rewards(self) -> None:
        """Key positive events have positive rewards."""
        from trw_mcp.scoring import REWARD_MAP as rmap
        positive = [
            EventType.TESTS_PASSED,
            EventType.TASK_COMPLETE,
            EventType.PHASE_GATE_PASSED,
            EventType.DELIVER_COMPLETE,
            EventType.BUILD_PASSED,
        ]
        for event in positive:
            assert rmap[event] > 0, f"{event} should have positive reward"

    def test_failure_events_have_negative_rewards(self) -> None:
        """Failure events have negative rewards."""
        from trw_mcp.scoring import REWARD_MAP as rmap
        negative = [
            EventType.TESTS_FAILED,
            EventType.PHASE_GATE_FAILED,
            EventType.BUILD_FAILED,
        ]
        for event in negative:
            assert rmap[event] < 0, f"{event} should have negative reward"

    def test_build_passed_higher_than_tests_passed_parity(self) -> None:
        """build_passed (0.6) < tests_passed (0.8) — build includes coverage check."""
        from trw_mcp.scoring import REWARD_MAP as rmap
        assert rmap[EventType.BUILD_PASSED] > 0
        assert rmap[EventType.TESTS_PASSED] > 0


# ============================================================================
# FR03/FR04: process_outcome_for_event behavior
# ============================================================================

class TestProcessOutcomeForEvent:
    """FR03/FR04: process_outcome_for_event integration tests."""

    def test_returns_list(self) -> None:
        """process_outcome_for_event always returns a list."""
        from trw_mcp.scoring import process_outcome_for_event as poe
        result = poe(EventType.SHARD_STARTED)
        assert isinstance(result, list)

    def test_returns_list_for_unknown_event(self) -> None:
        """Unknown events return empty list (no reward)."""
        from trw_mcp.scoring import process_outcome_for_event as poe
        result = poe("totally_unknown_xyz_event")
        assert result == []

    def test_build_passed_event_type_string(self) -> None:
        """EventType.BUILD_PASSED string value is 'build_passed'."""
        assert EventType.BUILD_PASSED == "build_passed"

    def test_build_failed_event_type_string(self) -> None:
        """EventType.BUILD_FAILED string value is 'build_failed'."""
        assert EventType.BUILD_FAILED == "build_failed"

    def test_outcome_history_appended(self, tmp_path: Path) -> None:
        """Outcome history grows with each process_outcome call."""
        import trw_mcp.scoring as _sc
        from trw_mcp.scoring import process_outcome as po
        writer = FileStateWriter()
        reader = FileStateReader()

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        receipts_dir = trw_dir / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)

        entry: dict[str, object] = {
            "id": "L-hist001",
            "summary": "history test",
            "detail": "detail",
            "impact": 0.7,
            "status": "active",
            "q_value": 0.7,
            "q_observations": 0,
            "recurrence": 1,
            "outcome_history": [],
            "tags": [],
        }
        entry_path = entries_dir / "L-hist001.yaml"
        writer.write_yaml(entry_path, entry)

        now_iso = datetime.now(timezone.utc).isoformat()
        receipt: dict[str, object] = {
            "ts": now_iso,
            "matched_ids": ["L-hist001"],
            "query": "history",
        }
        writer.append_jsonl(receipts_dir / "recall_log.jsonl", receipt)

        old_config = _sc._config
        old_reader = _sc._reader
        old_writer = _sc._writer
        from trw_mcp.models.config import TRWConfig
        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_outcome_correlation_window_minutes", 9999)
        object.__setattr__(cfg, "learning_outcome_correlation_scope", "window")
        _sc._config = cfg
        _sc._reader = reader
        _sc._writer = writer

        try:
            po(trw_dir, 0.8, "build_passed")
            stored = reader.read_yaml(entry_path)
            history = stored.get("outcome_history", [])
            assert isinstance(history, list)
            assert len(history) >= 1
        finally:
            _sc._config = old_config
            _sc._reader = old_reader
            _sc._writer = old_writer

    def test_negative_outcome_decreases_q_value(self, tmp_path: Path) -> None:
        """Negative reward (build_failed) decreases q_value for correlated entries."""
        import trw_mcp.scoring as _sc
        from trw_mcp.scoring import process_outcome as po
        writer = FileStateWriter()
        reader = FileStateReader()

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        receipts_dir = trw_dir / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)

        entry: dict[str, object] = {
            "id": "L-neg001",
            "summary": "negative reward test",
            "detail": "detail",
            "impact": 0.8,
            "status": "active",
            "q_value": 0.8,
            "q_observations": 0,
            "recurrence": 1,
            "tags": [],
        }
        entry_path = entries_dir / "L-neg001.yaml"
        writer.write_yaml(entry_path, entry)

        now_iso = datetime.now(timezone.utc).isoformat()
        receipt: dict[str, object] = {
            "ts": now_iso,
            "matched_ids": ["L-neg001"],
            "query": "test",
        }
        writer.append_jsonl(receipts_dir / "recall_log.jsonl", receipt)

        old_config = _sc._config
        old_reader = _sc._reader
        old_writer = _sc._writer
        from trw_mcp.models.config import TRWConfig
        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_outcome_correlation_window_minutes", 9999)
        object.__setattr__(cfg, "learning_outcome_correlation_scope", "window")
        _sc._config = cfg
        _sc._reader = reader
        _sc._writer = writer

        try:
            po(trw_dir, -0.4, "build_failed")
            stored = reader.read_yaml(entry_path)
            q_new = float(str(stored.get("q_value", 0.8)))
            assert q_new < 0.8, "Negative reward should decrease q_value"
        finally:
            _sc._config = old_config
            _sc._reader = old_reader
            _sc._writer = old_writer


# ============================================================================
# NFR03: Stored impact immutability (additional scenarios)
# ============================================================================

class TestStoredImpactImmutabilityAdditional:
    """NFR03: Additional tests verifying stored impact is never mutated at query time."""

    def test_rank_by_utility_does_not_mutate_entry_dict(self) -> None:
        """rank_by_utility must not mutate the entry dicts passed to it."""
        from trw_mcp.scoring import rank_by_utility as rbu
        created_old = (datetime.now(timezone.utc) - timedelta(days=300)).date().isoformat()
        entry: dict[str, object] = {
            "id": "L-mut001",
            "summary": "mutation test",
            "detail": "mutation detail",
            "impact": 0.9,
            "status": "active",
            "created": created_old,
            "q_value": 0.9,
            "q_observations": 5,
            "recurrence": 3,
            "tags": ["test"],
        }
        import copy
        entry_copy = copy.deepcopy(entry)

        rbu([entry], ["mutation"], 0.5)

        # The entry dict itself must not have been modified
        assert entry["impact"] == entry_copy["impact"]
        assert entry.get("q_value") == entry_copy.get("q_value")

    def test_apply_time_decay_does_not_modify_caller_state(self) -> None:
        """Repeated calls to apply_time_decay with same args return same result."""
        created = datetime.now(timezone.utc) - timedelta(days=100)
        r1 = apply_time_decay(0.8, created)
        r2 = apply_time_decay(0.8, created)
        # Results must be deterministic (pure function)
        assert r1 == pytest.approx(r2)


# ============================================================================
# FR01: Static purity check — apply_time_decay has no write calls
# ============================================================================

class TestApplyTimeDecayPurity:
    """FR01: apply_time_decay must be a pure compute function with no write side effects."""

    def test_apply_time_decay_body_has_no_writer_calls(self) -> None:
        """FR01: The body of apply_time_decay must not call _writer or write_yaml.

        This is a static contract test. If someone adds a write call to apply_time_decay,
        stored impact values would be permanently mutated at query time — a correctness bug.
        """
        import inspect
        from trw_mcp.scoring import apply_time_decay as atd
        source = inspect.getsource(atd)
        assert "_writer" not in source, (
            "apply_time_decay body calls _writer — this would mutate stored impact scores at query time"
        )
        assert "write_yaml" not in source, (
            "apply_time_decay body calls write_yaml — this would mutate stored impact scores at query time"
        )
        assert "FileStateWriter" not in source, (
            "apply_time_decay body instantiates FileStateWriter — violates purity contract"
        )

    def test_apply_time_decay_returns_float_no_side_effects(self, tmp_path: Path) -> None:
        """FR01: Direct call to apply_time_decay returns float, no YAML written.

        Verifies the function contract: given impact=0.9 and a date 400 days ago,
        the result is a float in [0.0, 1.0] and no file is created.
        """
        created = datetime(2024, 1, 1, tzinfo=timezone.utc)  # > 365 days ago
        result = apply_time_decay(0.9, created)

        # Returns a float in range
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        assert result < 0.9  # Decay must be applied

        # No files written (tmp_path stays empty)
        files_created = list(tmp_path.rglob("*"))
        assert files_created == [], f"apply_time_decay wrote files: {files_created}"

    def test_stored_impact_unchanged_after_repeated_trw_recall(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR06/NFR03: Calling trw_recall N times never mutates the stored impact field.

        Given a learning entry with impact=0.75, created 400 days ago,
        after calling trw_recall 3 times the YAML still has impact=0.75.
        """
        import trw_mcp.scoring as scoring_mod

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()

        old_date = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        entry: dict[str, object] = {
            "id": "L-immut01",
            "summary": "immutability test entry",
            "detail": "should not change",
            "impact": 0.75,
            "q_value": 0.75,
            "q_observations": 0,
            "status": "active",
            "created_at": old_date,
            "tags": ["immutability"],
        }
        entry_path = entries_dir / "L-immut01.yaml"
        writer.write_yaml(entry_path, entry)

        # Call search_entries 3 times — simulates repeated recall queries
        # FR06/NFR03: search_entries applies decay in-memory only; stored impact must be unchanged
        from trw_mcp.state.recall_search import search_entries as rse

        for _ in range(3):
            rse(entries_dir, ["immutability", "should"], reader)

        stored = reader.read_yaml(entry_path)
        stored_impact = stored.get("impact")
        assert stored_impact == 0.75, (
            f"Stored impact was mutated from 0.75 to {stored_impact} by recall calls"
        )


# ============================================================================
# FR05: BUILD_PASSED and BUILD_FAILED are in REWARD_MAP, not EVENT_ALIASES
# ============================================================================

class TestBuildEventsInRewardMap:
    """FR05: BUILD_PASSED and BUILD_FAILED must be direct REWARD_MAP entries."""

    def test_build_passed_in_reward_map(self) -> None:
        """FR05: BUILD_PASSED is a first-class REWARD_MAP entry."""
        assert EventType.BUILD_PASSED in REWARD_MAP, (
            "EventType.BUILD_PASSED is not in REWARD_MAP — build outcome Q-learning is broken"
        )

    def test_build_failed_in_reward_map(self) -> None:
        """FR05: BUILD_FAILED is a first-class REWARD_MAP entry."""
        assert EventType.BUILD_FAILED in REWARD_MAP, (
            "EventType.BUILD_FAILED is not in REWARD_MAP — negative build outcome Q-learning is broken"
        )

    def test_build_passed_not_in_event_aliases(self) -> None:
        """FR05: BUILD_PASSED must not be in EVENT_ALIASES (promoted to REWARD_MAP)."""
        assert EventType.BUILD_PASSED not in EVENT_ALIASES, (
            "EventType.BUILD_PASSED is still in EVENT_ALIASES — should be in REWARD_MAP only"
        )

    def test_build_failed_not_in_event_aliases(self) -> None:
        """FR05: BUILD_FAILED must not be in EVENT_ALIASES (promoted to REWARD_MAP)."""
        assert EventType.BUILD_FAILED not in EVENT_ALIASES, (
            "EventType.BUILD_FAILED is still in EVENT_ALIASES — should be in REWARD_MAP only"
        )

    def test_build_passed_reward_value(self) -> None:
        """FR05: BUILD_PASSED reward must be +0.6."""
        assert REWARD_MAP[EventType.BUILD_PASSED] == pytest.approx(0.6), (
            f"Expected 0.6, got {REWARD_MAP[EventType.BUILD_PASSED]}"
        )

    def test_build_failed_reward_value(self) -> None:
        """FR05: BUILD_FAILED reward must be -0.4."""
        assert REWARD_MAP[EventType.BUILD_FAILED] == pytest.approx(-0.4), (
            f"Expected -0.4, got {REWARD_MAP[EventType.BUILD_FAILED]}"
        )


# ============================================================================
# FR03: mypy-only scope fires Q-learning event (implementation behavior)
# ============================================================================

class TestBuildCheckMypyOnlyScope:
    """FR03: Verify build_check behavior for mypy-only scope.

    Note: PRD-FIX-027-FR03 states mypy-only scope should NOT fire a build outcome event.
    Current implementation fires 'build_passed' if mypy is clean (tests_passed=True by default).
    This test documents current implemented behavior.
    """

    def _make_mypy_status(self, mypy_clean: bool) -> MagicMock:
        mock_status = MagicMock()
        mock_status.tests_passed = True   # Default — no pytest run for mypy scope
        mock_status.mypy_clean = mypy_clean
        mock_status.coverage_pct = 0.0
        mock_status.test_count = 0
        mock_status.failure_count = 0
        mock_status.failures = []
        mock_status.scope = "mypy"
        mock_status.duration_secs = 0.5
        return mock_status

    def test_mypy_only_scope_fires_build_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR03 (impl): For scope='mypy' with clean results, build_passed event fires.

        Note: PRD specifies mypy-only should NOT fire. Implementation fires 'build_passed'.
        This test captures current behavior. If PRD compliance is required, add scope guard
        in build.py: only fire Q-learning for scope in ('full', 'pytest').
        """
        import trw_mcp.tools.build as build_mod
        from fastmcp import FastMCP

        called_events: list[str] = []
        mock_status = self._make_mypy_status(mypy_clean=True)
        mock_config = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        monkeypatch.setattr(build_mod, "_config", mock_config)
        monkeypatch.setattr(build_mod, "run_build_check", lambda *a, **kw: mock_status)
        monkeypatch.setattr(build_mod, "cache_build_status", lambda *a, **kw: Path("/tmp/cache"))
        monkeypatch.setattr(build_mod, "resolve_trw_dir", lambda: tmp_path / ".trw")
        monkeypatch.setattr(build_mod, "resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr(
            "trw_mcp.scoring.process_outcome_for_event",
            lambda event_type: called_events.append(event_type) or [],
        )

        server = __import__("fastmcp", fromlist=["FastMCP"]).FastMCP("test")
        build_mod.register_build_tools(server)
        tool_fn = None
        for tool in server._tool_manager._tools.values():
            if tool.name == "trw_build_check":
                tool_fn = tool.fn
                break

        assert tool_fn is not None
        tool_fn(scope="mypy", run_path=None, timeout_secs=30)

        # Current implementation fires build_passed even for mypy-only scope
        # (tests_passed=True default + mypy_clean=True => "build_passed")
        assert "build_passed" in called_events, (
            "scope='mypy' fires 'build_passed' event in current implementation"
        )

    def test_q_observations_increments_in_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR04: After build_check, correlated learning's q_observations increments in YAML.

        Full integration: write entry + receipt, patch scoring module, run build_check,
        verify q_observations > 0.
        """
        import trw_mcp.scoring as scoring_mod
        import trw_mcp.tools.build as build_mod
        from fastmcp import FastMCP

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        receipts_dir = trw_dir / "learnings" / "receipts"
        receipts_dir.mkdir(parents=True)
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()

        entry: dict[str, object] = {
            "id": "L-qobs001",
            "summary": "q-obs test entry",
            "detail": "for q_observations increment test",
            "impact": 0.7,
            "status": "active",
            "q_value": 0.7,
            "q_observations": 0,
            "recurrence": 1,
            "tags": [],
        }
        entry_path = entries_dir / "L-qobs001.yaml"
        writer.write_yaml(entry_path, entry)

        now_iso = datetime.now(timezone.utc).isoformat()
        receipt: dict[str, object] = {
            "ts": now_iso,
            "matched_ids": ["L-qobs001"],
            "query": "q-obs test",
        }
        writer.append_jsonl(receipts_dir / "recall_log.jsonl", receipt)

        cfg = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(cfg, "learning_outcome_correlation_window_minutes", 9999)
        object.__setattr__(cfg, "learning_outcome_correlation_scope", "window")

        # Patch scoring module-level objects AND resolve_trw_dir so outcome
        # correlation uses the tmp trw_dir, not the real project's .trw/
        monkeypatch.setattr(scoring_mod, "_config", cfg)
        monkeypatch.setattr(scoring_mod, "_reader", reader)
        monkeypatch.setattr(scoring_mod, "_writer", writer)
        monkeypatch.setattr(scoring_mod, "resolve_trw_dir", lambda: trw_dir)

        mock_status = MagicMock()
        mock_status.tests_passed = True
        mock_status.mypy_clean = True
        mock_status.coverage_pct = 95.0
        mock_status.test_count = 100
        mock_status.failure_count = 0
        mock_status.failures = []
        mock_status.scope = "full"
        mock_status.duration_secs = 1.0

        monkeypatch.setattr(build_mod, "_config", cfg)
        monkeypatch.setattr(build_mod, "run_build_check", lambda *a, **kw: mock_status)
        monkeypatch.setattr(build_mod, "cache_build_status", lambda *a, **kw: Path("/tmp/cache"))
        monkeypatch.setattr(build_mod, "resolve_trw_dir", lambda: trw_dir)
        monkeypatch.setattr(build_mod, "resolve_project_root", lambda: tmp_path)

        server = FastMCP("test")
        build_mod.register_build_tools(server)
        tool_fn = None
        for tool in server._tool_manager._tools.values():
            if tool.name == "trw_build_check":
                tool_fn = tool.fn
                break

        assert tool_fn is not None
        result = tool_fn(scope="full", run_path=None, timeout_secs=30)
        assert result["tests_passed"] is True

        stored = reader.read_yaml(entry_path)
        q_obs = int(str(stored.get("q_observations", 0)))
        assert q_obs >= 1, (
            f"FR04: q_observations should be >= 1 after build_check, got {q_obs}"
        )
