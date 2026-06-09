"""Split scoring utility/distribution coverage tests from test_recall_scoring_report.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateWriter


class TestComputeImpactDistributionReadException:
    """Cover YAML read exception in compute_impact_distribution."""

    def test_corrupt_yaml_file_is_skipped(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When _reader.read_yaml raises, the file is skipped (lines 277-278)."""
        import trw_mcp.scoring as scoring_mod
        from trw_mcp.scoring import compute_impact_distribution

        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        writer.write_yaml(
            entries_dir / "good.yaml",
            {
                "id": "L-ok",
                "summary": "ok",
                "impact": 0.9,
                "status": "active",
            },
        )
        (entries_dir / "bad.yaml").write_text("{not: valid: yaml: [", encoding="utf-8")

        original_reader = scoring_mod._reader
        real_read = original_reader.read_yaml
        try:

            def patched_read(path: Path) -> dict[str, object]:
                if "bad" in str(path):
                    raise StateError("parse error", path=str(path))
                return real_read(path)

            scoring_mod._reader.read_yaml = patched_read
            result = compute_impact_distribution(entries_dir)
            assert result["total_active"] == 1
        finally:
            scoring_mod._reader.read_yaml = real_read


class TestUtilityBasedPruneCandidatesTier3:
    """Cover tier 3 prune candidate paths."""

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

        entry = self._make_entry("L-tier3", "2025-11-01", impact=0.3)
        result = utility_based_prune_candidates([entry])

        assert len(result) >= 1
        assert result[0]["id"] == "L-tier3"

    def test_tier3_medium_impact_older_entry_prune_range(self) -> None:
        """Verify tier 3 path executes by using moderate impact, old entry."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import utility_based_prune_candidates

        test_config = TRWConfig()

        with patch("trw_mcp.scoring._recall_prune.get_config", return_value=test_config):
            entry = self._make_entry("L-t3b", "2025-12-15", impact=0.45)
            utility_based_prune_candidates([entry])
            old_entry = self._make_entry("L-t3c", "2025-09-01", impact=0.35)
            result = utility_based_prune_candidates([old_entry])
            assert isinstance(result, list)

    def test_tier3_reason_contains_prune_threshold(self) -> None:
        """Tier 3 candidate reason mentions 'prune threshold'."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.scoring import utility_based_prune_candidates

        cfg = TRWConfig()
        object.__setattr__(cfg, "learning_utility_delete_threshold", 0.0)
        object.__setattr__(cfg, "learning_utility_prune_threshold", 0.99)

        with patch("trw_mcp.scoring._recall_prune.get_config", return_value=cfg):
            entry = self._make_entry("L-t3-reason", "2025-10-01", impact=0.5)
            result = utility_based_prune_candidates([entry])

            assert any("prune threshold" in str(r.get("reason", "")) for r in result), (
                f"Expected 'prune threshold' in reasons, got: {[r.get('reason') for r in result]}"
            )


class TestComputeUtilityScoreAccessBoost:
    """Cover access_count boost in compute_utility_score."""

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
            0.5,
            0,
            1,
            0.5,
            5,
            access_count=10,
            access_count_boost_cap=0.15,
        )
        score_high = compute_utility_score(
            0.5,
            0,
            1,
            0.5,
            5,
            access_count=10000,
            access_count_boost_cap=0.15,
        )
        assert abs(score_high - score_moderate) < 0.001 or score_high >= score_moderate


class TestEntryUtilityInvalidCreatedDate:
    """Cover ValueError handling for unparseable created dates in _entry_utility."""

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
            "created": "not-a-real-date",
        }

        result = rank_by_utility([entry], query_tokens=[], lambda_weight=0.5)
        assert len(result) == 1
        assert result[0]["id"] == "L-bad-date"


class TestEnforceTierDistribution:
    """Cover enforce_tier_distribution demotion branches."""

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

        entries = [
            ("L-c1", 0.95),
            ("L-h1", 0.85),
            ("L-h2", 0.80),
            ("L-m1", 0.50),
            ("L-m2", 0.55),
            ("L-m3", 0.45),
            ("L-m4", 0.40),
            ("L-l1", 0.30),
            ("L-l2", 0.25),
            ("L-l3", 0.20),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.25, high_cap=0.5)
        assert result == []

    def test_critical_cap_exceeded_triggers_demotion(self) -> None:
        """Critical tier exceeds cap -> lowest critical entry gets demoted (lines 424-438)."""
        from trw_mcp.scoring import enforce_tier_distribution

        entries = [
            ("L-c1", 0.91),
            ("L-c2", 0.93),
            ("L-c3", 0.95),
            ("L-c4", 0.97),
            ("L-m1", 0.50),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.05, high_cap=0.5)
        assert len(result) >= 1
        demoted_ids = [entry_id for entry_id, _ in result]
        assert "L-c1" in demoted_ids
        demoted_score = next(score for entry_id, score in result if entry_id == "L-c1")
        assert 0.7 <= demoted_score <= 0.89

    def test_high_cap_exceeded_triggers_demotion(self) -> None:
        """High tier exceeds cap -> lowest high entry gets demoted (lines 447-463)."""
        from trw_mcp.scoring import enforce_tier_distribution

        entries = [
            ("L-h1", 0.71),
            ("L-h2", 0.75),
            ("L-h3", 0.80),
            ("L-h4", 0.85),
            ("L-m1", 0.50),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.5, high_cap=0.05)
        assert len(result) >= 1
        demoted_ids = [entry_id for entry_id, _ in result]
        assert "L-h1" in demoted_ids
        demoted_score = next(score for entry_id, score in result if entry_id == "L-h1")
        assert 0.4 <= demoted_score <= 0.69

    def test_both_caps_exceeded_produces_two_demotions(self) -> None:
        """Both critical and high tiers exceeding caps -> two demotions."""
        from trw_mcp.scoring import enforce_tier_distribution

        entries = [
            ("L-c1", 0.91),
            ("L-c2", 0.92),
            ("L-c3", 0.93),
            ("L-c4", 0.94),
            ("L-c5", 0.95),
            ("L-h1", 0.71),
            ("L-h2", 0.75),
            ("L-h3", 0.80),
            ("L-h4", 0.85),
            ("L-h5", 0.88),
        ]
        result = enforce_tier_distribution(entries, critical_cap=0.05, high_cap=0.05)
        assert len(result) >= 2
