"""Tests for nudge history in CeremonyState (PRD-CORE-103-FR02).

Tests cover:
- NudgeHistoryEntry TypedDict creation
- Serialization round-trip (write + read)
- _from_dict fail-open deserialization (malformed, missing)
- _parse_nudge_history edge cases
- record_nudge_shown mutation (new entry, update, phase append, cap eviction)
- clear_nudge_history reset
- is_nudge_eligible dedup logic
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state._nudge_state import (
    _NUDGE_HISTORY_CAP,
    CeremonyState,
    NudgeHistoryEntry,
    _from_dict,
    _parse_nudge_history,
    clear_nudge_history,
    is_nudge_eligible,
    read_ceremony_state,
    record_nudge_shown,
    write_ceremony_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_trw_dir(tmp_path: Path) -> Path:
    """Create .trw/context/ directory and return the .trw path."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


# ---------------------------------------------------------------------------
# NudgeHistoryEntry TypedDict creation
# ---------------------------------------------------------------------------


class TestNudgeHistoryEntry:
    def test_create_entry(self) -> None:
        """NudgeHistoryEntry can be constructed as a TypedDict."""
        entry = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT"],
            turn_first_shown=5,
            last_shown_turn=5,
        )
        assert entry["phases_shown"] == ["IMPLEMENT"]
        assert entry["turn_first_shown"] == 5
        assert entry["last_shown_turn"] == 5

    def test_entry_is_plain_dict(self) -> None:
        """NudgeHistoryEntry is a plain dict at runtime (TypedDict)."""
        entry = NudgeHistoryEntry(
            phases_shown=["VALIDATE"],
            turn_first_shown=1,
            last_shown_turn=3,
        )
        assert isinstance(entry, dict)


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestNudgeHistorySerialization:
    def test_round_trip(self, tmp_path: Path) -> None:
        """nudge_history round-trips through JSON serialization."""
        trw_dir = _setup_trw_dir(tmp_path)

        state = CeremonyState()
        state.nudge_history["L-a3Fq"] = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT"],
            turn_first_shown=5,
            last_shown_turn=5,
        )
        write_ceremony_state(trw_dir, state)

        loaded = read_ceremony_state(trw_dir)
        assert "L-a3Fq" in loaded.nudge_history
        assert loaded.nudge_history["L-a3Fq"]["phases_shown"] == ["IMPLEMENT"]
        assert loaded.nudge_history["L-a3Fq"]["turn_first_shown"] == 5
        assert loaded.nudge_history["L-a3Fq"]["last_shown_turn"] == 5

    def test_round_trip_multiple_entries(self, tmp_path: Path) -> None:
        """Multiple nudge_history entries survive round-trip."""
        trw_dir = _setup_trw_dir(tmp_path)

        state = CeremonyState()
        state.nudge_history["L-001"] = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT", "VALIDATE"],
            turn_first_shown=1,
            last_shown_turn=10,
        )
        state.nudge_history["L-002"] = NudgeHistoryEntry(
            phases_shown=["REVIEW"],
            turn_first_shown=3,
            last_shown_turn=3,
        )
        write_ceremony_state(trw_dir, state)

        loaded = read_ceremony_state(trw_dir)
        assert len(loaded.nudge_history) == 2
        assert loaded.nudge_history["L-001"]["phases_shown"] == ["IMPLEMENT", "VALIDATE"]
        assert loaded.nudge_history["L-002"]["turn_first_shown"] == 3

    def test_empty_nudge_history_round_trip(self, tmp_path: Path) -> None:
        """Empty nudge_history survives round-trip (default)."""
        trw_dir = _setup_trw_dir(tmp_path)

        state = CeremonyState()
        write_ceremony_state(trw_dir, state)

        loaded = read_ceremony_state(trw_dir)
        assert loaded.nudge_history == {}


# ---------------------------------------------------------------------------
# _from_dict deserialization edge cases
# ---------------------------------------------------------------------------


class TestFromDictDeserialization:
    def test_missing_nudge_history_defaults_empty(self) -> None:
        """Missing nudge_history field defaults to empty dict."""
        state = _from_dict({"session_started": True})
        assert state.nudge_history == {}
        assert state.session_started is True

    def test_nudge_history_not_a_dict_failopen(self) -> None:
        """Non-dict nudge_history deserializes to empty dict (fail-open)."""
        state = _from_dict({"nudge_history": "not_a_dict", "session_started": True})
        assert state.nudge_history == {}
        assert state.session_started is True

    def test_nudge_history_null_failopen(self) -> None:
        """null nudge_history deserializes to empty dict (fail-open)."""
        state = _from_dict({"nudge_history": None})
        assert state.nudge_history == {}

    def test_nudge_history_list_failopen(self) -> None:
        """list nudge_history deserializes to empty dict (fail-open)."""
        state = _from_dict({"nudge_history": [1, 2, 3]})
        assert state.nudge_history == {}

    def test_malformed_entry_skipped(self) -> None:
        """Malformed entries inside nudge_history are skipped."""
        state = _from_dict(
            {
                "nudge_history": {
                    "L-good": {
                        "phases_shown": ["IMPLEMENT"],
                        "turn_first_shown": 5,
                        "last_shown_turn": 5,
                    },
                    "L-bad-val": "not_a_dict",
                    123: {"phases_shown": ["X"]},  # non-str key
                }
            }
        )
        assert len(state.nudge_history) == 1
        assert "L-good" in state.nudge_history

    def test_entry_with_non_string_phases_filtered(self) -> None:
        """Non-string items in phases_shown are filtered out."""
        state = _from_dict(
            {
                "nudge_history": {
                    "L-mix": {
                        "phases_shown": ["IMPLEMENT", 42, None, "VALIDATE"],
                        "turn_first_shown": 1,
                        "last_shown_turn": 2,
                    },
                }
            }
        )
        assert state.nudge_history["L-mix"]["phases_shown"] == ["IMPLEMENT", "VALIDATE"]

    def test_missing_turn_fields_default_zero(self) -> None:
        """Missing turn fields default to 0."""
        state = _from_dict(
            {
                "nudge_history": {
                    "L-no-turns": {
                        "phases_shown": ["DELIVER"],
                    },
                }
            }
        )
        entry = state.nudge_history["L-no-turns"]
        assert entry["turn_first_shown"] == 0
        assert entry["last_shown_turn"] == 0


# ---------------------------------------------------------------------------
# _parse_nudge_history direct tests
# ---------------------------------------------------------------------------


class TestParseNudgeHistory:
    def test_valid_input(self) -> None:
        result = _parse_nudge_history(
            {
                "L-1": {
                    "phases_shown": ["IMPLEMENT"],
                    "turn_first_shown": 1,
                    "last_shown_turn": 2,
                }
            }
        )
        assert len(result) == 1
        assert result["L-1"]["turn_first_shown"] == 1

    def test_non_dict_returns_empty(self) -> None:
        assert _parse_nudge_history("bad") == {}
        assert _parse_nudge_history(42) == {}
        assert _parse_nudge_history(None) == {}
        assert _parse_nudge_history([]) == {}

    def test_empty_dict(self) -> None:
        assert _parse_nudge_history({}) == {}

    def test_type_error_in_entry_skipped(self) -> None:
        """Entry that causes TypeError/ValueError is skipped."""
        result = _parse_nudge_history(
            {
                "L-bad": {
                    "phases_shown": ["X"],
                    "turn_first_shown": "not_a_number",  # will cause ValueError on int()
                }
            }
        )
        # The int() call on "not_a_number" should succeed since int("not_a_number")
        # raises ValueError which is caught. So the entry is skipped.
        assert result == {}


# ---------------------------------------------------------------------------
# record_nudge_shown
# ---------------------------------------------------------------------------


class TestRecordNudgeShown:
    def test_new_entry(self, tmp_path: Path) -> None:
        """record_nudge_shown creates a new entry for unseen learning."""
        trw_dir = _setup_trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState())

        record_nudge_shown(trw_dir, "L-new", "IMPLEMENT", turn=3)

        loaded = read_ceremony_state(trw_dir)
        assert "L-new" in loaded.nudge_history
        entry = loaded.nudge_history["L-new"]
        assert entry["phases_shown"] == ["IMPLEMENT"]
        assert entry["turn_first_shown"] == 3
        assert entry["last_shown_turn"] == 3

    def test_update_existing_same_phase(self, tmp_path: Path) -> None:
        """Updating with same phase does not duplicate in phases_shown."""
        trw_dir = _setup_trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState())

        record_nudge_shown(trw_dir, "L-dup", "IMPLEMENT", turn=1)
        record_nudge_shown(trw_dir, "L-dup", "IMPLEMENT", turn=5)

        loaded = read_ceremony_state(trw_dir)
        entry = loaded.nudge_history["L-dup"]
        assert entry["phases_shown"] == ["IMPLEMENT"]  # no duplicate
        assert entry["turn_first_shown"] == 1  # unchanged
        assert entry["last_shown_turn"] == 5  # updated

    def test_update_existing_new_phase(self, tmp_path: Path) -> None:
        """Updating with a new phase appends to phases_shown."""
        trw_dir = _setup_trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState())

        record_nudge_shown(trw_dir, "L-cross", "IMPLEMENT", turn=1)
        record_nudge_shown(trw_dir, "L-cross", "VALIDATE", turn=7)

        loaded = read_ceremony_state(trw_dir)
        entry = loaded.nudge_history["L-cross"]
        assert entry["phases_shown"] == ["IMPLEMENT", "VALIDATE"]
        assert entry["turn_first_shown"] == 1
        assert entry["last_shown_turn"] == 7

    def test_default_turn_zero(self, tmp_path: Path) -> None:
        """turn parameter defaults to 0."""
        trw_dir = _setup_trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState())

        record_nudge_shown(trw_dir, "L-default", "IMPLEMENT")

        loaded = read_ceremony_state(trw_dir)
        entry = loaded.nudge_history["L-default"]
        assert entry["turn_first_shown"] == 0
        assert entry["last_shown_turn"] == 0


# ---------------------------------------------------------------------------
# Capacity cap
# ---------------------------------------------------------------------------


class TestCapacityCap:
    def test_cap_enforced(self, tmp_path: Path) -> None:
        """Nudge history is capped at _NUDGE_HISTORY_CAP entries."""
        trw_dir = _setup_trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState())

        # Insert 105 entries (5 over cap)
        for i in range(105):
            record_nudge_shown(trw_dir, f"L-{i:04d}", "IMPLEMENT", turn=i)

        loaded = read_ceremony_state(trw_dir)
        assert len(loaded.nudge_history) == _NUDGE_HISTORY_CAP

    def test_evicts_oldest_by_last_shown_turn(self, tmp_path: Path) -> None:
        """When cap is reached, the entry with lowest last_shown_turn is evicted."""
        trw_dir = _setup_trw_dir(tmp_path)

        # Pre-fill to exactly cap
        state = CeremonyState()
        for i in range(_NUDGE_HISTORY_CAP):
            state.nudge_history[f"L-{i:04d}"] = NudgeHistoryEntry(
                phases_shown=["IMPLEMENT"],
                turn_first_shown=i + 10,  # oldest is L-0000 at turn 10
                last_shown_turn=i + 10,
            )
        write_ceremony_state(trw_dir, state)

        # Add one more -- should evict L-0000 (last_shown_turn=10, the oldest)
        record_nudge_shown(trw_dir, "L-NEW", "VALIDATE", turn=999)

        loaded = read_ceremony_state(trw_dir)
        assert len(loaded.nudge_history) == _NUDGE_HISTORY_CAP
        assert "L-0000" not in loaded.nudge_history  # evicted
        assert "L-NEW" in loaded.nudge_history


# ---------------------------------------------------------------------------
# clear_nudge_history (compaction reset)
# ---------------------------------------------------------------------------


class TestClearNudgeHistory:
    def test_clears_history(self, tmp_path: Path) -> None:
        """clear_nudge_history empties the history."""
        trw_dir = _setup_trw_dir(tmp_path)

        state = CeremonyState()
        state.nudge_history["L-a3Fq"] = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT"],
            turn_first_shown=1,
            last_shown_turn=1,
        )
        write_ceremony_state(trw_dir, state)

        clear_nudge_history(trw_dir)
        loaded = read_ceremony_state(trw_dir)
        assert loaded.nudge_history == {}

    def test_clears_preserves_other_fields(self, tmp_path: Path) -> None:
        """clear_nudge_history does not affect other CeremonyState fields."""
        trw_dir = _setup_trw_dir(tmp_path)

        state = CeremonyState()
        state.session_started = True
        state.checkpoint_count = 3
        state.nudge_history["L-x"] = NudgeHistoryEntry(
            phases_shown=["REVIEW"],
            turn_first_shown=1,
            last_shown_turn=1,
        )
        write_ceremony_state(trw_dir, state)

        clear_nudge_history(trw_dir)
        loaded = read_ceremony_state(trw_dir)
        assert loaded.nudge_history == {}
        assert loaded.session_started is True
        assert loaded.checkpoint_count == 3

    def test_clears_already_empty(self, tmp_path: Path) -> None:
        """clear_nudge_history on empty history is a no-op."""
        trw_dir = _setup_trw_dir(tmp_path)
        write_ceremony_state(trw_dir, CeremonyState())

        clear_nudge_history(trw_dir)
        loaded = read_ceremony_state(trw_dir)
        assert loaded.nudge_history == {}


# ---------------------------------------------------------------------------
# is_nudge_eligible
# ---------------------------------------------------------------------------


class TestIsNudgeEligible:
    def test_empty_history_eligible(self) -> None:
        """Learning not in history is eligible."""
        state = CeremonyState()
        assert is_nudge_eligible(state, "L-a3Fq", "IMPLEMENT") is True

    def test_same_phase_not_eligible(self) -> None:
        """Learning already shown in same phase is NOT eligible."""
        state = CeremonyState()
        state.nudge_history["L-a3Fq"] = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT"],
            turn_first_shown=1,
            last_shown_turn=1,
        )
        assert is_nudge_eligible(state, "L-a3Fq", "IMPLEMENT") is False

    def test_different_phase_eligible(self) -> None:
        """Learning shown in different phase IS eligible."""
        state = CeremonyState()
        state.nudge_history["L-a3Fq"] = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT"],
            turn_first_shown=1,
            last_shown_turn=1,
        )
        assert is_nudge_eligible(state, "L-a3Fq", "VALIDATE") is True

    def test_multiple_phases_shown(self) -> None:
        """Learning shown in multiple phases is not eligible in any of them."""
        state = CeremonyState()
        state.nudge_history["L-multi"] = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT", "VALIDATE", "REVIEW"],
            turn_first_shown=1,
            last_shown_turn=10,
        )
        assert is_nudge_eligible(state, "L-multi", "IMPLEMENT") is False
        assert is_nudge_eligible(state, "L-multi", "VALIDATE") is False
        assert is_nudge_eligible(state, "L-multi", "REVIEW") is False
        assert is_nudge_eligible(state, "L-multi", "DELIVER") is True

    def test_unknown_learning_id_eligible(self) -> None:
        """Unknown learning_id is always eligible (not in history)."""
        state = CeremonyState()
        state.nudge_history["L-other"] = NudgeHistoryEntry(
            phases_shown=["IMPLEMENT"],
            turn_first_shown=1,
            last_shown_turn=1,
        )
        assert is_nudge_eligible(state, "L-unknown", "IMPLEMENT") is True
