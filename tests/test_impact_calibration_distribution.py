"""Forced distribution impact calibration tests."""

from __future__ import annotations

from trw_mcp.scoring import enforce_tier_distribution


class TestEnforceTierDistribution:
    """Forced distribution cap enforcement (PRD-CORE-034-FR01)."""

    def test_empty_list_returns_no_demotions(self) -> None:
        result = enforce_tier_distribution([])
        assert result == []

    def test_single_entry_no_demotion(self) -> None:
        """Single critical entry: total < 5, no enforcement."""
        result = enforce_tier_distribution([("L-001", 0.95)])
        assert result == []

    def test_four_entries_no_enforcement(self) -> None:
        """Fewer than 5 total entries: caps are never triggered."""
        entries = [("L-%03d" % i, 0.95) for i in range(4)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_exactly_five_entries_enforcement_starts(self) -> None:
        """With 5 entries, cap enforcement is active."""
        entries = [("L-%03d" % i, 0.95) for i in range(5)]
        result = enforce_tier_distribution(entries)
        assert len(result) >= 1

    def test_within_critical_cap_no_demotion(self) -> None:
        """Critical tier at exactly cap: no demotions."""
        entries: list[tuple[str, float]] = [("L-crit", 0.95)]
        entries += [("L-med-%02d" % i, 0.5) for i in range(19)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_within_high_cap_no_demotion(self) -> None:
        """High tier at exactly cap: no demotions."""
        entries: list[tuple[str, float]] = [("L-high-%d" % i, 0.75) for i in range(4)]
        entries += [("L-med-%02d" % i, 0.5) for i in range(16)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_critical_tier_over_cap_demotes_one(self) -> None:
        """Critical tier at >5%: lowest critical entry demoted to high."""
        entries: list[tuple[str, float]] = [
            ("L-crit-low", 0.91),
            ("L-crit-high", 0.99),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(8)]
        result = enforce_tier_distribution(entries)
        assert len(result) == 1
        demoted_id, new_score = result[0]
        assert demoted_id == "L-crit-low"
        assert 0.7 <= new_score <= 0.89

    def test_high_tier_over_cap_demotes_one(self) -> None:
        """High tier at >20%: lowest high entry demoted to medium."""
        entries: list[tuple[str, float]] = [
            ("L-high-low", 0.71),
            ("L-high-mid", 0.75),
            ("L-high-high", 0.85),
            ("L-high-v2", 0.80),
            ("L-high-v3", 0.73),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(5)]
        result = enforce_tier_distribution(entries)
        demoted_ids = [d[0] for d in result]
        assert "L-high-low" in demoted_ids
        for _, new_score in result:
            assert 0.4 <= new_score <= 0.69

    def test_both_tiers_over_cap_demotes_from_both(self) -> None:
        """Both critical and high over cap: both get one demotion each."""
        entries: list[tuple[str, float]] = [
            ("L-crit-1", 0.92),
            ("L-crit-2", 0.96),
            ("L-high-1", 0.71),
            ("L-high-2", 0.75),
            ("L-high-3", 0.82),
            ("L-high-4", 0.85),
            ("L-high-5", 0.78),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(3)]
        result = enforce_tier_distribution(entries)
        crit_demotions = [(i, s) for i, s in result if 0.7 <= s <= 0.89]
        assert len(crit_demotions) >= 1

    def test_demotion_selects_lowest_critical(self) -> None:
        """Lowest-scored critical entry is the demotion victim."""
        entries: list[tuple[str, float]] = [
            ("L-lowest", 0.90),
            ("L-middle", 0.95),
            ("L-highest", 0.99),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(17)]
        result = enforce_tier_distribution(entries)
        ids_demoted = [d[0] for d in result]
        assert "L-lowest" in ids_demoted

    def test_demotion_selects_lowest_high(self) -> None:
        """Lowest-scored high entry is the demotion victim."""
        entries: list[tuple[str, float]] = [
            ("L-hlow", 0.70),
            ("L-hmid", 0.78),
            ("L-hhigh", 0.88),
        ]
        entries += [("L-med-%d" % i, 0.5) for i in range(7)]
        result = enforce_tier_distribution(entries)
        ids_demoted = [d[0] for d in result]
        assert "L-hlow" in ids_demoted

    def test_demoted_critical_score_in_high_range(self) -> None:
        """Critical demotion result is in [0.7, 0.89]."""
        entries = [("L-crit", 0.95)] + [("L-m-%d" % i, 0.5) for i in range(19)]
        result = enforce_tier_distribution(entries)
        for _, new_score in result:
            if new_score <= 0.89:
                assert 0.7 <= new_score <= 0.89

    def test_demoted_high_score_in_medium_range(self) -> None:
        """High demotion result is in [0.4, 0.69]."""
        entries = [("L-h-%d" % i, 0.75) for i in range(5)]
        entries += [("L-m-%d" % i, 0.5) for i in range(5)]
        result = enforce_tier_distribution(entries)
        for _, new_score in result:
            if new_score <= 0.69:
                assert 0.4 <= new_score <= 0.69

    def test_custom_critical_cap(self) -> None:
        """Custom critical_cap overrides config default."""
        entries = [("L-crit", 0.95)] + [("L-m-%d" % i, 0.5) for i in range(9)]
        default_result = enforce_tier_distribution(entries)
        assert len(default_result) >= 1
        custom_result = enforce_tier_distribution(entries, critical_cap=0.15)
        assert custom_result == []

    def test_custom_high_cap(self) -> None:
        """Custom high_cap overrides config default."""
        entries = [("L-h-%d" % i, 0.75) for i in range(3)]
        entries += [("L-m-%d" % i, 0.5) for i in range(7)]
        default_result = enforce_tier_distribution(entries)
        assert len(default_result) >= 1
        custom_result = enforce_tier_distribution(entries, high_cap=0.40)
        assert custom_result == []

    def test_all_medium_no_demotion(self) -> None:
        """All entries in medium tier: no critical/high entries → no demotions."""
        entries = [("L-%d" % i, 0.5) for i in range(20)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_all_low_no_demotion(self) -> None:
        """All entries in low tier: no demotions needed."""
        entries = [("L-%d" % i, 0.2) for i in range(20)]
        result = enforce_tier_distribution(entries)
        assert result == []

    def test_return_is_list_of_tuples(self) -> None:
        """Returns list of (id, float) tuples."""
        entries = [("L-crit", 0.95)] + [("L-m-%d" % i, 0.5) for i in range(19)]
        result = enforce_tier_distribution(entries)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            lid, score = item
            assert isinstance(lid, str)
            assert isinstance(score, float)
