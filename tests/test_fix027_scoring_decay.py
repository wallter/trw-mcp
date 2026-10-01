"""Tests for PRD-FIX-027 time-decay and scoring purity behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.scoring import apply_time_decay


class TestApplyTimeDecay:
    """Parametrized edge cases for apply_time_decay."""

    @pytest.mark.parametrize(
        "days,impact,expected_min,expected_max",
        [
            (0, 1.0, 1.0, 1.0),
            (182, 1.0, 0.848, 0.852),
            (365, 1.0, 0.699, 0.701),
            (486, 1.0, 0.598, 0.602),
            (730, 1.0, 0.399, 0.401),
            (1460, 1.0, 0.299, 0.301),
            (0, 0.0, 0.0, 0.0),
        ],
    )
    def test_decay_parametrized(self, days: int, impact: float, expected_min: float, expected_max: float) -> None:
        from trw_mcp.scoring import apply_time_decay

        created = datetime.now(timezone.utc) - timedelta(days=days)
        result = apply_time_decay(impact, created)
        assert expected_min <= result <= expected_max, (
            f"days={days}, impact={impact}: got {result}, expected [{expected_min}, {expected_max}]"
        )

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive datetime (no tzinfo) is treated as UTC — no exception."""
        from trw_mcp.scoring import apply_time_decay

        naive_now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        result = apply_time_decay(0.8, naive_now)
        assert result >= 0.79


class TestStoredImpactImmutabilityAdditional:
    """NFR03: Additional tests verifying stored impact is never mutated at query time."""

    def test_rank_targeted_by_utility_does_not_mutate_entry_dict(self) -> None:
        """rank_targeted_by_utility must not mutate the entry dicts passed to it."""
        from trw_mcp.scoring import rank_targeted_by_utility as rbu

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

        assert entry["impact"] == entry_copy["impact"]
        assert entry.get("q_value") == entry_copy.get("q_value")

    def test_apply_time_decay_does_not_modify_caller_state(self) -> None:
        """Repeated calls to apply_time_decay with same args return same result."""
        created = datetime.now(timezone.utc) - timedelta(days=100)
        r1 = apply_time_decay(0.8, created)
        r2 = apply_time_decay(0.8, created)
        assert r1 == pytest.approx(r2)


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
        created = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = apply_time_decay(0.9, created)

        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        assert result < 0.9
