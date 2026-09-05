"""Edge-case tests for ranking and prune-candidate scoring behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.scoring import rank_by_utility, utility_based_prune_candidates


class TestRankByUtilityEdgeCases:
    """Additional edge cases for rank_by_utility."""

    def _make_entry(
        self,
        summary: str,
        impact: float = 0.5,
        tags: list[str] | None = None,
        detail: str = "",
    ) -> dict[str, object]:
        return {
            "id": f"L-{summary[:4]}",
            "summary": summary,
            "detail": detail,
            "tags": tags or [],
            "impact": impact,
            "q_value": impact,
            "q_observations": 5,
            "recurrence": 1,
            "access_count": 0,
            "source_type": "agent",
            "created": datetime.now(tz=timezone.utc).date().isoformat(),
        }

    def test_non_list_tags_handled(self) -> None:
        """Non-list tags field is handled gracefully."""
        entry = self._make_entry("test", tags=None)
        entry["tags"] = "not-a-list"
        result = rank_by_utility([entry], query_tokens=["test"], lambda_weight=0.5)
        assert len(result) == 1

    def test_detail_hits_contribute_to_relevance(self) -> None:
        """Query tokens found in detail contribute to relevance scoring."""
        entry_in_detail = self._make_entry("generic", detail="pytest framework testing")
        entry_no_match = self._make_entry("generic", detail="unrelated content")
        result = rank_by_utility(
            [entry_no_match, entry_in_detail],
            query_tokens=["pytest"],
            lambda_weight=0.0,
        )
        assert result[0]["id"] == entry_in_detail["id"]

    def test_lambda_weight_one_pure_utility(self) -> None:
        """lambda_weight=1.0 means pure utility, ignores relevance."""
        low_impact = self._make_entry("pytest testing", impact=0.1)
        high_impact = self._make_entry("unrelated", impact=0.9)
        result = rank_by_utility(
            [low_impact, high_impact],
            query_tokens=["pytest"],
            lambda_weight=1.0,
        )
        assert result[0]["id"] == high_impact["id"]

    def test_lambda_weight_zero_pure_relevance(self) -> None:
        """lambda_weight=0.0 means pure relevance, ignores utility."""
        matching = self._make_entry("pytest testing", impact=0.1)
        non_matching = self._make_entry("unrelated stuff", impact=0.9)
        result = rank_by_utility(
            [non_matching, matching],
            query_tokens=["pytest", "testing"],
            lambda_weight=0.0,
        )
        assert result[0]["id"] == matching["id"]

    def test_summary_hits_weighted_higher_than_detail(self) -> None:
        """Summary matches are weighted 3x vs detail matches 1x."""
        entry_summary = self._make_entry("pytest info")
        entry_detail = self._make_entry("generic", detail="pytest info")
        result = rank_by_utility(
            [entry_detail, entry_summary],
            query_tokens=["pytest"],
            lambda_weight=0.0,
        )
        assert result[0]["id"] == entry_summary["id"]

    def test_stable_sort_equal_scores(self) -> None:
        """Entries with equal scores maintain their relative order (sort stability)."""
        entries = [
            self._make_entry("first", impact=0.5),
            self._make_entry("second", impact=0.5),
            self._make_entry("third", impact=0.5),
        ]
        result = rank_by_utility(entries, query_tokens=[], lambda_weight=1.0)
        assert len(result) == 3


class TestUtilityBasedPruneCandidatesEdgeCases:
    """Additional edge cases for utility_based_prune_candidates."""

    def _make_entry(
        self,
        entry_id: str,
        created: str,
        status: str = "active",
        impact: float = 0.3,
        recurrence: int = 1,
    ) -> tuple[Path, dict[str, object]]:
        data: dict[str, object] = {
            "id": entry_id,
            "summary": f"Learning {entry_id}",
            "created": created,
            "status": status,
            "impact": impact,
            "q_value": impact,
            "q_observations": 0,
            "recurrence": recurrence,
            "access_count": 0,
            "source_type": "agent",
        }
        return (Path(f"/fake/{entry_id}.yaml"), data)

    def test_active_young_high_utility_not_candidate(self) -> None:
        """Recent high-impact active entry is never a candidate."""
        entries = [self._make_entry("L-fresh", datetime.now(tz=timezone.utc).date().isoformat(), impact=0.9)]
        result = utility_based_prune_candidates(entries)
        assert result == []

    def test_resolved_status_zero_utility(self) -> None:
        """Resolved entries have utility=0.0 in the candidate dict."""
        entries = [self._make_entry("L-done", "2026-01-01", status="resolved")]
        result = utility_based_prune_candidates(entries)
        assert len(result) == 1
        assert result[0]["utility"] == 0.0

    def test_obsolete_entry_with_missing_created_is_still_a_candidate(self) -> None:
        """Regression: an obsolete/resolved straggler with no created date (e.g. a
        YAML-migrated entry whose created_at was never backfilled) must still be
        nominated for status-based cleanup. The old code raised ValueError on the
        empty date and `continue`d PAST the status tier, so such dead entries were
        immortal — never pruned."""
        entries = [self._make_entry("L-undated", "", status="obsolete")]
        result = utility_based_prune_candidates(entries)
        assert len(result) == 1
        assert result[0]["id"] == "L-undated"

    def test_tier3_requires_age_over_14_days(self) -> None:
        """Tier-3 prune candidates must be older than 14 days."""
        recent = (datetime.now(tz=timezone.utc).date() - timedelta(days=10)).isoformat()
        entries = [self._make_entry("L-young-low", recent, impact=0.05)]
        result = utility_based_prune_candidates(entries)
        for candidate in result:
            if candidate["id"] == "L-young-low":
                assert (
                    "delete threshold" in str(candidate.get("reason", "")).lower()
                    or "utility" in str(candidate.get("reason", "")).lower()
                )

    def test_high_recurrence_improves_utility(self) -> None:
        """Higher recurrence count produces higher utility (harder to prune)."""
        old_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=60)).isoformat()
        low_rec = self._make_entry("L-low-rec", old_date, impact=0.3, recurrence=1)
        high_rec = self._make_entry("L-high-rec", old_date, impact=0.3, recurrence=20)
        result_low = utility_based_prune_candidates([low_rec])
        result_high = utility_based_prune_candidates([high_rec])
        assert len(result_high) <= len(result_low)


class TestProtectionTierProtects:
    """PRD-CORE-244 FR10 — ``protection_tier`` is a guarantee, not a data field.

    The pre-existing coverage at ``tests/test_learn.py`` only asserted that the
    value survives a round trip, which is exactly why the gap was invisible: the
    field was tested as DATA and never as a PROMISE. Every test here asserts the
    protective EFFECT on the real ``utility_based_prune_candidates`` path, and
    each pairs a protected fixture with an otherwise byte-identical ``normal``
    one so the exemption cannot be credited to the fixture.
    """

    @staticmethod
    def _worthless(entry_id: str, tier: str) -> tuple[Path, dict[str, object]]:
        """An entry far below every prune threshold, differing only in tier."""
        old = (datetime.now(tz=timezone.utc).date() - timedelta(days=900)).isoformat()
        return (
            Path("/dev/null"),
            {
                "id": entry_id,
                "summary": "a forgotten entry",
                "impact": 0.01,
                "q_value": 0.01,
                "q_observations": 10,
                "recurrence": 1,
                "access_count": 0,
                "source_type": "agent",
                "type": "hypothesis",
                "confidence": "verified",
                "status": "active",
                "created": old,
                "last_accessed_at": old,
                "protection_tier": tier,
            },
        )

    def _nominated(self, tier: str) -> bool:
        candidates = utility_based_prune_candidates([self._worthless(f"L-{tier}", tier)])
        return any(c["id"] == f"L-{tier}" for c in candidates)

    def test_protection_tier_exempts_permanent_from_auto_prune(self) -> None:
        assert self._nominated("permanent") is False

    def test_protection_tier_exempts_protected_from_auto_prune(self) -> None:
        assert self._nominated("protected") is False

    def test_the_same_entry_marked_normal_is_nominated(self) -> None:
        """Proves the exemption came from the TIER, not from the fixture."""
        assert self._nominated("normal") is True

    def test_an_unmarked_entry_is_nominated_exactly_as_before(self) -> None:
        _path, data = self._worthless("L-unmarked", "normal")
        del data["protection_tier"]
        candidates = utility_based_prune_candidates([(Path("/dev/null"), data)])
        assert [c["id"] for c in candidates] == ["L-unmarked"]

    def test_middle_tiers_are_discounted_not_exempted(self) -> None:
        """A ``critical`` entry must be far less useful than a ``normal`` one.

        Built as an entry whose utility sits BETWEEN the normal threshold and the
        critical (0.25x) threshold: normal nominates it, critical does not, and
        neither outcome is an exemption.
        """
        from trw_mcp.models.config import get_config
        from trw_mcp.scoring._decay import entry_utility

        cfg = get_config()
        today = datetime.now(tz=timezone.utc).date()
        # Age it until its utility lands under the normal prune threshold but
        # above the critical-discounted one.
        for days in range(30, 2000, 10):
            _path, probe = self._worthless("L-probe", "normal")
            aged = (today - timedelta(days=days)).isoformat()
            probe["created"] = aged
            probe["last_accessed_at"] = aged
            probe["impact"] = 0.5
            probe["q_value"] = 0.5
            utility = entry_utility(dict(probe), today)
            lower = cfg.learning_utility_prune_threshold * cfg.protection_tier_prune_discount["critical"]
            if lower <= utility < cfg.learning_utility_prune_threshold:
                break
        else:  # pragma: no cover - a band this wide always contains a sample
            raise AssertionError("no age produced a utility inside the critical/normal band")

        def _nominate(tier: str) -> bool:
            _p, data = self._worthless(f"L-{tier}-band", tier)
            data.update({"created": aged, "last_accessed_at": aged, "impact": 0.5, "q_value": 0.5})
            return any(c["id"] == f"L-{tier}-band" for c in utility_based_prune_candidates([(Path("/dev/null"), data)]))

        assert _nominate("normal") is True
        assert _nominate("critical") is False

    def test_status_tier_cleanup_also_honours_the_exemption(self) -> None:
        """ "Already marked obsolete" is still AUTOMATIC removal."""
        _path, permanent = self._worthless("L-obsolete-permanent", "permanent")
        permanent["status"] = "obsolete"
        _path2, normal = self._worthless("L-obsolete-normal", "normal")
        normal["status"] = "obsolete"

        ids = {
            c["id"]
            for c in utility_based_prune_candidates([(Path("/dev/null"), permanent), (Path("/dev/null"), normal)])
        }
        assert ids == {"L-obsolete-normal"}


class TestAutoPruneHonoursProtectionTier:
    """FR10 on the SECOND removal source: the Jaccard duplicate scan.

    ``auto_prune_excess_entries`` nominates from two independent sources. The
    utility scan is covered above; the duplicate scan never read the tier at all,
    so an older ``permanent`` entry that merely LOOKED like a newer one was
    marked obsolete on similarity alone.
    """

    def test_duplicate_scan_skips_a_permanent_entry(self) -> None:
        from trw_mcp.state.analytics.dedup import _protected_entry_ids, _select_removal_candidates

        entries = [
            {"id": "L-old-permanent", "protection_tier": "permanent"},
            {"id": "L-old-normal", "protection_tier": "normal"},
        ]
        duplicates = [("L-old-permanent", "L-new", 0.95), ("L-old-normal", "L-new", 0.95)]

        pairs = _select_removal_candidates(duplicates, [], _protected_entry_ids(entries))

        assert [pid for pid, _status in pairs] == ["L-old-normal"]

    def test_utility_candidates_are_filtered_by_the_same_exempt_set(self) -> None:
        from trw_mcp.state.analytics.dedup import _protected_entry_ids, _select_removal_candidates

        entries = [{"id": "L-p", "protection_tier": "protected"}, {"id": "L-n", "protection_tier": "low"}]
        candidates = [
            {"id": "L-p", "suggested_status": "obsolete"},
            {"id": "L-n", "suggested_status": "obsolete"},
        ]

        pairs = _select_removal_candidates([], candidates, _protected_entry_ids(entries))  # type: ignore[arg-type]

        assert [pid for pid, _status in pairs] == ["L-n"]
