"""PRD-CORE-278 FR08/FR09 — where a learning came from, and who gets the slot.

The defect these pin (learning L-XIhp, verified 2026-09-16): a store holding
1,330 rows pulled from other projects presented them as THIS repository's
knowledge on four surfaces, including the single nudge line a session is most
likely to act on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

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
    def test_payload_origin_is_recorded_verbatim(self, tmp_path: Path) -> None:
        stored = self._merge(tmp_path, {"origin_project": "acme-web", "origin": "backend"})
        assert stored.metadata[ORIGIN_PROJECT_KEY] == "acme-web"
        assert stored.metadata["origin"] == "backend"

    def test_missing_origin_becomes_the_literal_unknown(self, tmp_path: Path) -> None:
        stored = self._merge(tmp_path, {"origin": "backend"})
        assert stored.metadata[ORIGIN_PROJECT_KEY] == UNKNOWN_ORIGIN_PROJECT

    def test_namespace_and_tags_are_never_read_as_origin(self, tmp_path: Path) -> None:
        """A guess written into durable metadata is indistinguishable from a fact."""
        stored = self._merge(tmp_path, {"project": "acme-web", "repo": "acme/web"}, tags=["acme-web", "project:acme"])
        assert stored.metadata[ORIGIN_PROJECT_KEY] == UNKNOWN_ORIGIN_PROJECT

    def _merge(self, tmp_path: Path, metadata: dict[str, Any], tags: list[str] | None = None) -> Any:
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        from trw_mcp.sync.pull import SyncPuller

        backend = SQLiteBackend(tmp_path / "memory.db", dim=8)
        puller = SyncPuller(backend_url="http://example.com", api_key="key", trw_dir=tmp_path)
        with patch("trw_mcp.state._memory_connection.get_backend", return_value=backend):
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
        stored = backend.get("team-sync-remote-1", namespace="default")
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
            patch("trw_mcp.state.recall_factories.recall_baseline_high_impact", return_value=[*foreign, local]),
            patch("trw_mcp.state.recall_factories.recall_recent_bypass", return_value=[]),
            patch(
                "trw_mcp.tools._recall_assertion_verification._verify_assertions",
                side_effect=lambda rows, *_a, **_k: rows,
            ),
        ):
            learnings, _auto, _extra = helpers.perform_session_recalls(tmp_path, "*", config, helpers.FileStateReader())

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
    def test_recall_impl_module_is_not_modified_by_this_change(self) -> None:
        """NFR02: total_available keeps its bounded pre-cap meaning.

        PRD-FIX-141 owns ``_recall_impl.py``; this PRD neither redefines the
        field nor edits the module that documents it.
        """
        import inspect

        from trw_mcp.tools import _recall_impl

        source = inspect.getsource(_recall_impl)
        assert "total_available" in source
        assert "origin_project" not in source
        assert "demote_unattributable" not in source


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
