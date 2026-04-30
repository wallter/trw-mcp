"""RecallContext and decay coverage for CORE-116 recall scoring."""

from __future__ import annotations


class TestRecallContextNewFields:
    """Tests for PRD-CORE-116 new RecallContext fields."""

    def test_recall_context_new_fields(self) -> None:
        """client_profile and model_family are accepted as constructor args."""
        from trw_mcp.scoring._recall import RecallContext

        ctx = RecallContext(client_profile="opencode", model_family="gpt-4o")
        assert ctx.client_profile == "opencode"
        assert ctx.model_family == "gpt-4o"

    def test_recall_context_defaults(self) -> None:
        """All fields default to empty string or empty set."""
        from trw_mcp.scoring._recall import RecallContext

        ctx = RecallContext()
        assert ctx.current_phase is None
        assert ctx.inferred_domains == set()
        assert ctx.team == ""
        assert ctx.prd_knowledge_ids == set()
        assert ctx.modified_files == []
        assert ctx.client_profile == ""
        assert ctx.model_family == ""

    def test_recall_context_frozen(self) -> None:
        """RecallContext is frozen — attribute assignment raises."""
        from dataclasses import FrozenInstanceError

        import pytest

        from trw_mcp.scoring._recall import RecallContext

        ctx = RecallContext(client_profile="opencode")
        with pytest.raises(FrozenInstanceError):
            ctx.client_profile = "cursor"  # type: ignore[misc]


class TestTypeHalfLife:
    """Tests for _TYPE_HALF_LIFE values per PRD-CORE-116 spec."""

    def test_type_half_life_values(self) -> None:
        """All 5 type half-life values match the PRD spec."""
        from trw_mcp.scoring._decay import _TYPE_HALF_LIFE

        assert _TYPE_HALF_LIFE["incident"] == 90.0
        assert _TYPE_HALF_LIFE["pattern"] == 180.0
        assert _TYPE_HALF_LIFE["convention"] == 9999.0
        assert _TYPE_HALF_LIFE["hypothesis"] == 7.0
        assert _TYPE_HALF_LIFE["workaround"] == 14.0

    def test_pattern_decay_slower_than_workaround(self) -> None:
        """200-day-old pattern retains higher utility than same-age workaround."""
        from datetime import date, timedelta

        from trw_mcp.scoring._decay import _entry_utility

        today = date(2026, 4, 1)
        created = (today - timedelta(days=200)).isoformat()

        pattern_entry: dict[str, object] = {
            "type": "pattern",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "created": created,
            "confidence": "medium",
        }

        workaround_entry: dict[str, object] = dict(pattern_entry)
        workaround_entry["type"] = "workaround"

        utility_pattern = _entry_utility(pattern_entry, today)
        utility_workaround = _entry_utility(workaround_entry, today)

        assert utility_pattern > utility_workaround

    def test_convention_near_no_decay(self) -> None:
        """1000-day-old convention retains utility close to a fresh entry."""
        from datetime import date, timedelta

        from trw_mcp.scoring._decay import _entry_utility

        today = date(2026, 4, 1)
        old_created = (today - timedelta(days=1000)).isoformat()
        fresh_created = today.isoformat()

        base: dict[str, object] = {
            "type": "convention",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "confidence": "verified",
        }

        old_entry = dict(base, created=old_created)
        fresh_entry = dict(base, created=fresh_created)

        utility_old = _entry_utility(old_entry, today)
        utility_fresh = _entry_utility(fresh_entry, today)

        assert utility_old > utility_fresh * 0.8

    def test_hypothesis_decays_fast(self) -> None:
        """30-day-old hypothesis has significantly lower utility than fresh one."""
        from datetime import date, timedelta

        from trw_mcp.scoring._decay import _entry_utility

        today = date(2026, 4, 1)
        old_created = (today - timedelta(days=30)).isoformat()
        fresh_created = today.isoformat()

        base: dict[str, object] = {
            "type": "hypothesis",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "confidence": "low",
        }

        old_entry = dict(base, created=old_created)
        fresh_entry = dict(base, created=fresh_created)

        utility_old = _entry_utility(old_entry, today)
        utility_fresh = _entry_utility(fresh_entry, today)

        assert utility_old < utility_fresh * 0.5

    def test_incident_unverified_no_decay(self) -> None:
        """Unverified incident has near-zero decay (half_life=9999)."""
        from datetime import date, timedelta

        from trw_mcp.scoring._decay import _entry_utility

        today = date(2026, 4, 1)
        old_created = (today - timedelta(days=500)).isoformat()
        fresh_created = today.isoformat()

        base: dict[str, object] = {
            "type": "incident",
            "impact": 0.7,
            "q_value": 0.7,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 3,
            "source_type": "agent",
            "confidence": "unverified",
        }

        old_entry = dict(base, created=old_created)
        fresh_entry = dict(base, created=fresh_created)

        utility_old = _entry_utility(old_entry, today)
        utility_fresh = _entry_utility(fresh_entry, today)

        assert utility_old > utility_fresh * 0.8
