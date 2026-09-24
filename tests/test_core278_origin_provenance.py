"""PRD-CORE-278 FR08/FR09 — where a learning came from, and who gets the slot.

The defect these pin (learning L-XIhp, verified 2026-09-16): a store holding
1,330 rows pulled from other projects presented them as THIS repository's
knowledge on four surfaces, including the single nudge line a session is most
likely to act on.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._layout import MONOREPO_ROOT, requires_monorepo
from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state._origin_project import (
    ORIGIN_PROJECT_KEY,
    UNKNOWN_ORIGIN_PROJECT,
    demote_unattributable,
    is_attributable_to_this_project,
    nudge_eligible_pool,
    origin_project,
)


def _local(entry_id: str = "L-local", **extra: Any) -> dict[str, Any]:
    return {"id": entry_id, "summary": "local finding", "impact": 0.9, "metadata": {}, **extra}


def _synced(entry_id: str = "team-sync-L-remote", **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": entry_id,
        "summary": "another project's finding",
        "impact": 0.9,
        "source": "team_sync",
        "metadata": {ORIGIN_PROJECT_KEY: UNKNOWN_ORIGIN_PROJECT},
    }
    row.update(extra)
    return row


class TestOriginProjectIsRecordedNeverInferred:
    def test_payload_origin_is_recorded_verbatim(self, daemon_checkout: DaemonCheckout) -> None:
        stored = self._merge(daemon_checkout, {"origin_project": "acme-web", "origin": "backend"})
        assert stored.metadata[ORIGIN_PROJECT_KEY] == "acme-web"
        assert stored.metadata["origin"] == "backend"

    def test_missing_origin_becomes_the_literal_unknown(self, daemon_checkout: DaemonCheckout) -> None:
        stored = self._merge(daemon_checkout, {"origin": "backend"})
        assert stored.metadata[ORIGIN_PROJECT_KEY] == UNKNOWN_ORIGIN_PROJECT

    def test_namespace_and_tags_are_never_read_as_origin(self, daemon_checkout: DaemonCheckout) -> None:
        """A guess written into durable metadata is indistinguishable from a fact."""
        stored = self._merge(
            daemon_checkout, {"project": "acme-web", "repo": "acme/web"}, tags=["acme-web", "project:acme"]
        )
        assert stored.metadata[ORIGIN_PROJECT_KEY] == UNKNOWN_ORIGIN_PROJECT

    def _merge(self, daemon_checkout: DaemonCheckout, metadata: dict[str, Any], tags: list[str] | None = None) -> Any:
        # PRD-CORE-280 slice e1: merge_team_learnings already routes through
        # ``selected_store`` (PRD-CORE-298 FR01), so a migrated checkout merges
        # over the daemon with no production change needed — read back through
        # the same store instead of a raw ``SQLiteBackend``.
        from tests._path_isolation import set_current_root
        from trw_mcp.state._store_selection import selected_store
        from trw_mcp.sync.pull import SyncPuller

        trw_dir = daemon_checkout.trw_dir
        # PRD-CORE-280 slice e1: `_isolate_trw_dir` points the isolated
        # resolve_trw_dir() stand-in at tmp_path, but `daemon_checkout` lives at
        # tmp_path/repo/.trw. A lazily-resolving background thread (e.g. a
        # telemetry flush timer) would otherwise land on the wrong -- or a
        # LATER test's -- directory. Re-point it to keep this test's window
        # internally consistent.
        set_current_root(trw_dir.parent)
        puller = SyncPuller(backend_url="http://example.com", api_key="key", trw_dir=trw_dir)
        result = puller.merge_team_learnings(
            [
                {
                    "source_learning_id": "remote-1",
                    "summary": "team learning",
                    "detail": "detail",
                    "impact": 0.8,
                    "tags": tags or [],
                    "type": "pattern",
                    "status": "active",
                    "sync_seq": 7,
                    "metadata": metadata,
                }
            ]
        )
        assert result.applied == 1
        store, _namespace = selected_store(trw_dir)
        stored = store.get("team-sync-remote-1")
        assert stored is not None
        return stored

    def test_predicate_reads_recorded_origin_only(self) -> None:
        assert origin_project(_local()) == ""
        assert origin_project(_synced()) == UNKNOWN_ORIGIN_PROJECT
        assert origin_project(_synced(metadata={ORIGIN_PROJECT_KEY: "acme-web"})) == "acme-web"
        assert is_attributable_to_this_project(_local()) is True
        assert is_attributable_to_this_project(_synced()) is False
        assert is_attributable_to_this_project(_synced(metadata={ORIGIN_PROJECT_KEY: "acme-web"})) is False

    def test_a_synced_row_is_detected_without_its_source_field(self) -> None:
        assert is_attributable_to_this_project({"id": "team-sync-L-x", "metadata": {}}) is False


class TestProvenanceFailsOpen:
    def test_non_mapping_metadata_is_treated_as_local(self) -> None:
        assert is_attributable_to_this_project({"id": "L-1", "metadata": "not-a-mapping"}) is True

    def test_partition_preserves_input_on_an_unclassifiable_row(self) -> None:
        rows = [{"id": "L-1"}, {"id": "L-2"}]
        assert demote_unattributable(rows) == rows

    def test_partition_never_drops_a_row(self) -> None:
        rows = [_synced("team-sync-a"), _local("L-b"), _synced("team-sync-c")]
        partitioned = demote_unattributable(rows)
        assert {row["id"] for row in partitioned} == {row["id"] for row in rows}
        assert partitioned[0]["id"] == "L-b"

    # PRD-CORE-282 NFR04: both new call sites keep the ranked order when provenance breaks.
    def test_unclassifiable_rows_are_attributable_in_explicit_recall(self, tmp_path: Path) -> None:
        rows = [_synced("team-sync-1"), _local("L-1"), _synced("team-sync-2")]
        with patch("trw_mcp.state._origin_project.origin_project", side_effect=RuntimeError("broken")):
            result = _recall(tmp_path, rows)
        assert [row["id"] for row in result["learnings"]] == ["team-sync-1", "L-1", "team-sync-2"]

    def test_a_raising_partition_leaves_explicit_recall_order_unchanged(self, tmp_path: Path) -> None:
        rows = [_synced("team-sync-1"), _local("L-1"), _synced("team-sync-2")]
        with patch("trw_mcp.state._origin_project.is_attributable_to_this_project", side_effect=RuntimeError("broken")):
            result = _recall(tmp_path, rows)
        assert [row["id"] for row in result["learnings"]] == ["team-sync-1", "L-1", "team-sync-2"]

    # PRD-CORE-294 FR02 removed the auto-recall phase (`_phase_contextual_recall`);
    # the same partition now runs inside the surviving session-start recall
    # (`perform_session_recalls`). These two migrate the equivalent phase-level
    # coverage onto that path.
    def test_unclassifiable_rows_are_attributable_in_session_recall(self, tmp_path: Path) -> None:
        pool = _session_rows(["team-sync-1", "L-1", "team-sync-2", "L-2"])
        with patch("trw_mcp.state._origin_project.origin_project", side_effect=RuntimeError("broken")):
            # config.recall_max_results caps the final result at 3; ordering is
            # unchanged because the fail-open partition returns the input as-is.
            assert _session_recall(tmp_path, pool) == ["team-sync-1", "L-1", "team-sync-2"]

    def test_a_raising_partition_leaves_session_recall_order_unchanged(self, tmp_path: Path) -> None:
        pool = _session_rows(["team-sync-1", "L-1", "team-sync-2", "L-2"])
        with patch("trw_mcp.state._origin_project.is_attributable_to_this_project", side_effect=RuntimeError("broken")):
            assert _session_recall(tmp_path, pool) == ["team-sync-1", "L-1", "team-sync-2"]


class TestProjectScopedSurfacesDownweightForeignRows:
    def test_partition_is_stable_within_each_half(self) -> None:
        rows = [_synced("team-sync-1"), _synced("team-sync-2"), _local("L-1"), _local("L-2")]
        assert [row["id"] for row in demote_unattributable(rows)] == [
            "L-1",
            "L-2",
            "team-sync-1",
            "team-sync-2",
        ]

    def test_learnings_collector_keeps_a_local_row_beyond_the_upstream_cap(self) -> None:
        """Partitioning after a cap cannot recover what the cap excluded."""
        from trw_mcp.tools import _learnings_collector

        foreign = [_synced(f"team-sync-{index}") for index in range(5)]
        local = _local("L-mine")
        rows = [*foreign, local]

        with patch("trw_mcp.state.learning_injection.recall_learnings", return_value=rows):
            collected = _learnings_collector.collect_learnings(["src/thing.py"], top_n=3)

        assert collected[0].id == "L-mine"
        assert len(collected) == 3

    def test_summary_resource_renders_local_rows_first(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.resources import config as resources_config

        rows = [*[_synced(f"team-sync-{index}") for index in range(10)], _local("L-mine")]
        for row in rows:
            row["detail"] = "detail text"
        with patch.object(resources_config, "list_active_learnings", return_value=rows) as fetch:
            body = resources_config._build_learnings_summary(tmp_path, TRWConfig())
        # Over-fetch, THEN partition, THEN render: a local row sitting past the
        # rendered count is otherwise invisible no matter how it is sorted.
        assert fetch.call_args.kwargs["limit"] == 20
        assert "local finding" in body
        assert body.index("local finding") < body.index("another project's finding")

    def test_session_recall_promotes_local_rows_before_the_cap(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.tools import _session_recall_helpers as helpers

        foreign = [_synced(f"team-sync-{index}", created="2026-09-16") for index in range(8)]
        local = _local("L-mine", created="2026-09-16")
        config = TRWConfig()
        config.recall_max_results = 3

        with (
            patch("trw_mcp.state.recall_factories.recall_session_start", return_value=[*foreign, local]),
            patch(
                "trw_mcp.tools._recall_assertion_verification._verify_assertions",
                side_effect=lambda rows, *_a, **_k: rows,
            ),
        ):
            learnings, _extra = helpers.perform_session_recalls(
                tmp_path, "*", config, helpers.FileStateReader(), verbose=True
            )

        assert learnings, "session recall returned nothing"
        assert learnings[0]["id"] == "L-mine"


class TestNudgePoolPrecedence:
    def test_attribution_outranks_verification(self) -> None:
        """A verified claim about another repository is still about another repository."""
        local_unverified = _local("L-mine", verification_status="unknown")
        foreign_verified = _synced("team-sync-1", verification_status="verified")
        assert [row["id"] for row in nudge_eligible_pool([foreign_verified, local_unverified])] == ["L-mine"]

    def test_verified_wins_among_attributable_rows(self) -> None:
        unverified = _local("L-a", verification_status="unknown")
        verified = _local("L-b", verification_status="verified")
        assert [row["id"] for row in nudge_eligible_pool([unverified, verified])] == ["L-b"]

    def test_pool_is_never_emptied(self) -> None:
        rows = [_synced("team-sync-1", verification_status="unknown")]
        assert nudge_eligible_pool(rows) == rows
        assert nudge_eligible_pool([]) == []

    def test_narrowing_never_silences_the_surface(self, tmp_path: Path) -> None:
        """A preferred row with no renderable text must not suppress a contentful one."""
        from trw_mcp.state.ceremony_progress import CeremonyState
        from trw_mcp.tools import _ceremony_status_nudge as nudge_module

        local_blank = _local("L-mine", verification_status="unknown")
        foreign_with_text = _synced("team-sync-1", verification_status="unknown")
        texts = {"L-mine": "", "team-sync-1": "a usable line"}

        with (
            patch(
                "trw_mcp.state.recall_factories.recall_for_nudge_pool",
                return_value=[local_blank, foreign_with_text],
            ),
            patch.object(
                nudge_module,
                "_select_cached_or_deterministic_learning",
                side_effect=lambda pool, **_kw: pool[0],
            ),
            patch.object(
                nudge_module,
                "_deterministic_fallback_text",
                side_effect=lambda row: texts[str(row["id"])],
            ),
        ):
            content = nudge_module._try_learning_nudge_content(tmp_path, CeremonyState(phase="implement"))

        assert content == "Unverified: a usable line"

    def test_unverified_selection_is_labelled(self, tmp_path: Path) -> None:
        from trw_mcp.state.ceremony_progress import CeremonyState
        from trw_mcp.tools import _ceremony_status_nudge as nudge_module

        candidates = [_synced("team-sync-1", verification_status="unknown", nudge_line="a stale claim")]
        with (
            patch("trw_mcp.state.recall_factories.recall_for_nudge_pool", return_value=candidates),
            patch.object(nudge_module, "_select_cached_or_deterministic_learning", return_value=candidates[0]),
            patch.object(nudge_module, "_deterministic_fallback_text", return_value="a stale claim"),
        ):
            content = nudge_module._try_learning_nudge_content(tmp_path, CeremonyState(phase="implement"))

        assert content is not None
        assert content.startswith("Unverified: ")


class TestTotalAvailableIsUntouched:
    def test_attribution_reorders_but_never_changes_the_counts(self, tmp_path: Path) -> None:
        """CORE-278 NFR02 / CORE-282 FR01: the candidate pool keeps its size.

        CORE-278 pinned this by asserting ``_recall_impl.py`` did not mention
        attribution at all. CORE-282 wires attribution into that file on purpose,
        so the pin is now behavioural: the partition reorders, it never adds or
        drops a candidate. PRD-CORE-294 FR01 moved ``candidate_count`` and
        ``total_available`` out of the response and into the
        ``trw_recall_searched`` structlog event (counters a caller cannot act
        on); this asserts the same invariant through that event.
        """
        import structlog

        rows = [*[_synced(f"team-sync-{index}") for index in range(6)], *[_local(f"L-{index}") for index in range(2)]]
        with structlog.testing.capture_logs() as logs:
            result = _recall(tmp_path, rows, max_results=3)
        searched = next(log for log in logs if log.get("event") == "trw_recall_searched")
        assert searched["candidate_count"] == len(rows)
        assert [row["id"] for row in result["learnings"]] == ["L-0", "L-1", "team-sync-0"]


def _distinct(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every row its own text so exact-content dedup cannot collapse them."""
    return [{**row, "summary": f"{row['summary']} {row['id']}", "detail": f"detail {row['id']}"} for row in rows]


def _recall(
    tmp_path: Path,
    rows: list[dict[str, Any]],
    *,
    max_results: int | None = None,
    deprioritized_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Run the real ``execute_recall`` over *rows*, ranked exactly as given."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(exist_ok=True)
    with (
        patch("trw_mcp.tools._recall_impl.build_recall_context", return_value=None),
        patch("trw_mcp.tools._recall_impl._augment_with_remote", side_effect=lambda _q, m: (list(m), None)),
        patch("trw_mcp.tools._recall_impl._track_recall"),
    ):
        result = execute_recall(
            "shared query",
            trw_dir,
            TRWConfig(),
            max_results=max_results,
            deprioritized_ids=deprioritized_ids,
            _adapter_recall=lambda _dir, **_kw: _distinct(rows),
            _rank_by_utility=lambda matches, *_a, **_k: list(matches),
        )
    return dict(result)


def _scored(entry_id: str, score: float, **extra: Any) -> dict[str, Any]:
    row = _synced(entry_id, **extra) if entry_id.startswith("team-sync-") else _local(entry_id, **extra)
    return {**row, "combined_score": score}


def _ids(result: dict[str, Any]) -> list[str]:
    return [str(row["id"]) for row in result["learnings"]]


class TestExplicitRecallAttribution:
    """PRD-CORE-282 FR01: trw_recall halves a foreign row's score before dedup, budget and cap."""

    def test_local_rows_lead_a_pool_of_many_foreign_rows_in_ranked_order(self, tmp_path: Path) -> None:
        foreign = [_scored(f"team-sync-{index}", 0.9 - index * 0.01) for index in range(20)]
        local = [_scored("L-c", 0.6), _scored("L-a", 0.55), _scored("L-b", 0.5)]
        result = _recall(tmp_path, [*foreign, *local], max_results=0)
        ids = _ids(result)
        assert ids[:3] == ["L-c", "L-a", "L-b"]
        assert ids[3:] == [f"team-sync-{index}" for index in range(20)]
        # candidate_count moved to the trw_recall_searched structlog event
        # (PRD-CORE-294 FR01); the full 23-id membership check above already
        # proves nothing was dropped pre-cap.

    def test_fill_up_returns_the_local_row_then_the_top_foreign_rows(self, tmp_path: Path) -> None:
        foreign = [_scored(f"team-sync-{index}", 0.8 - index * 0.01) for index in range(10)]
        result = _recall(tmp_path, [*foreign, _scored("L-mine", 0.45)], max_results=5)
        assert _ids(result) == ["L-mine", "team-sync-0", "team-sync-1", "team-sync-2", "team-sync-3"]

    def test_a_foreign_row_twice_as_relevant_still_outranks_a_weak_local_match(self, tmp_path: Path) -> None:
        """The penalty is not a filter: the relevant foreign lesson stays reachable."""
        rows = [_scored("team-sync-strong", 1.0), _scored("L-weak", 0.3), _scored("team-sync-weak", 0.5)]
        assert _ids(_recall(tmp_path, rows, max_results=2)) == ["team-sync-strong", "L-weak"]

    def test_a_negative_foreign_score_is_pushed_down_not_raised(self, tmp_path: Path) -> None:
        rows = [_scored("L-penalized", -0.2), _scored("team-sync-penalized", -0.2)]
        assert _ids(_recall(tmp_path, rows)) == ["L-penalized", "team-sync-penalized"]

    def test_rows_without_a_score_fall_back_to_the_attribution_partition(self, tmp_path: Path) -> None:
        rows = [_synced("team-sync-1"), _local("L-1"), _synced("team-sync-2"), _local("L-2")]
        assert _ids(_recall(tmp_path, rows)) == ["L-1", "L-2", "team-sync-1", "team-sync-2"]

    def test_an_all_local_pool_keeps_the_ranker_order(self, tmp_path: Path) -> None:
        rows = [_scored("L-b", 0.2), _scored("L-a", 0.9), _scored("L-c", 0.5)]
        assert _ids(_recall(tmp_path, rows)) == ["L-b", "L-a", "L-c"]

    def test_temporal_eligibility_stays_outermost(self, tmp_path: Path) -> None:
        from trw_mcp.state.temporal_order import TEMPORAL_ELIGIBILITY_FIELD

        rows = [
            _scored("L-expired", 0.9, **{TEMPORAL_ELIGIBILITY_FIELD: False}),
            _scored("team-sync-valid", 0.4, **{TEMPORAL_ELIGIBILITY_FIELD: True}),
        ]
        assert _ids(_recall(tmp_path, rows)) == ["team-sync-valid", "L-expired"]

    def test_already_in_context_rows_go_behind_fresh_foreign_rows(self, tmp_path: Path) -> None:
        rows = [_scored("L-seen", 0.9), _scored("team-sync-fresh", 0.4)]
        result = _recall(tmp_path, rows, deprioritized_ids={"L-seen"})
        assert _ids(result) == ["team-sync-fresh", "L-seen"]

    def test_real_store_ranks_local_rows_above_twenty_synced_rows(
        self, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Integration: real execute_recall -> selected_store -> daemon, no ranker replaced.

        PRD-CORE-280 slice e1: seeded through the checkout's store (``put``,
        with an explicit ``entry_id``) instead of a raw ``backend.store(MemoryEntry(...))``
        call, since a migrated checkout has no in-process backend to poke directly.
        """
        from tests._path_isolation import set_current_root
        from trw_mcp.models.config import get_config
        from trw_mcp.state._store_selection import selected_store
        from trw_mcp.tools import _recall_impl

        monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
        monkeypatch.setattr(_recall_impl, "_augment_with_remote", lambda _q, rows: (rows, None))
        monkeypatch.setattr(_recall_impl, "build_recall_context", lambda *_a, **_k: None)
        trw_dir = daemon_checkout.trw_dir
        # PRD-CORE-280 slice e1: see the identical note in `_merge` above.
        set_current_root(trw_dir.parent)
        store, namespace = selected_store(trw_dir)
        for index in range(20):
            store.put(
                f"sqlite wal checkpoint lock contention observed in service {index}",
                namespace,
                {
                    "entry_id": f"team-sync-L-{index:02d}",
                    "importance": 0.9,
                    "source": "team_sync",
                    "detail": "detail",
                },
            )
        for index in range(3):
            store.put(
                f"sqlite wal checkpoint lock contention in memory.db writer {index}",
                namespace,
                {"entry_id": f"L-own{index}", "importance": 0.5, "detail": "detail"},
            )
        import structlog

        with structlog.testing.capture_logs() as logs:
            result = _recall_impl.execute_recall(
                "sqlite wal checkpoint lock",
                trw_dir,
                get_config(),
                max_results=5,
                include_tiers=["project"],
            )
        ids = [str(row["id"]) for row in result["learnings"]]
        assert sorted(ids[:3]) == ["L-own0", "L-own1", "L-own2"]
        assert all(i.startswith("team-sync-") for i in ids[3:])
        # PRD-CORE-294 FR01: candidate_count moved from the response to the
        # trw_recall_searched structlog event.
        searched = next(log for log in logs if log.get("event") == "trw_recall_searched")
        assert searched["candidate_count"] == 23


def _session_rows(ids: list[str]) -> list[dict[str, Any]]:
    rows = [_synced(i) if i.startswith("team-sync-") else _local(i) for i in ids]
    return [{**row, "verification_evidence": {"observation": "unknown"}} for row in rows]


def _session_recall(tmp_path: Path, pool: list[dict[str, Any]]) -> list[str]:
    """Run the real session-start recall (``perform_session_recalls``, PRD-CORE-294
    FR02) over a pool ranked exactly as given. This replaces the deleted
    ``_phase_contextual_recall`` phase helper this test used to exercise.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls

    config = TRWConfig()
    config.recall_max_results = 3
    with (
        patch("trw_mcp.state.recall_factories.recall_session_start", return_value=pool) as fetch,
        patch(
            "trw_mcp.tools._recall_assertion_verification._verify_assertions",
            side_effect=lambda rows, *_a, **_k: list(rows),
        ),
    ):
        items, _extra = perform_session_recalls(tmp_path, "shared query", config, FileStateReader(), verbose=True)
    # The over-fetch is what lets the partition recover a local row the cap would cut.
    assert fetch.call_args.kwargs["max_results"] == 6
    return [str(item["id"]) for item in items]


@requires_monorepo
class TestCore282BenchmarkQuerySet:
    """PRD-CORE-282 FR03: the benchmark fixture cannot drift from the PRD's section 13."""

    @staticmethod
    def _section_13() -> tuple[list[str], list[str]]:
        assert MONOREPO_ROOT is not None
        text = (MONOREPO_ROOT / "docs/requirements-aare-f/prds/PRD-CORE-282.md").read_text(encoding="utf-8")
        section = text.split("## 13. Appendix", 1)[1]
        queries = [m.group(2) for m in re.finditer(r"^(\d+)\. (.+)$", section, flags=re.MULTILINE)]
        files_block = section.split("Before-edit files:", 1)[1].split("\n\n", 1)[0]
        return queries, re.findall(r"`([^`]+)`", files_block)

    @staticmethod
    def _fixture() -> dict[str, Any]:
        assert MONOREPO_ROOT is not None
        path = MONOREPO_ROOT / "scripts/bench_recall_attribution_fixture.json"
        return dict(json.loads(path.read_text(encoding="utf-8")))

    def test_fixture_queries_equal_section_13(self) -> None:
        queries, files = self._section_13()
        fixture = self._fixture()
        assert len(queries) == 30
        assert [q["query"] for q in fixture["queries"]] == queries
        assert fixture["before_edit_files"] == files

    def test_fixture_carries_the_216_blind_labels(self) -> None:
        labels = [q["labels"] for q in self._fixture()["queries"]]
        assert sum(len(entry) for entry in labels) == 216
        assert {grade for entry in labels for grade in entry.values()} <= {0, 1, 2}

    def test_benchmark_classifies_foreign_rows_like_the_predicate(self) -> None:
        """The benchmark's own foreign rule must not diverge from the product predicate."""
        assert MONOREPO_ROOT is not None
        spec = importlib.util.spec_from_file_location(
            "bench_recall_attribution", MONOREPO_ROOT / "scripts/bench_recall_attribution.py"
        )
        assert spec is not None and spec.loader is not None
        bench = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bench)
        from trw_mcp.state import _origin_project

        assert bench.SYNCED_SOURCES == _origin_project._SYNCED_SOURCES
        assert bench.SYNCED_ID_PREFIX == _origin_project._SYNCED_ID_PREFIX
        assert bench.FIXTURE.name == "bench_recall_attribution_fixture.json"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
