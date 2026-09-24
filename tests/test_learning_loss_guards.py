"""Red tests for confirmed learning-loss paths (REFACTOR-CATALOG X-01, REF-002, X-04).

X-01: PRD-CORE-042-FR03 says a merge appends the incoming detail under an audit
marker. The implementation appends it only when it is longer, and never keeps
the incoming summary.
REF-002: PRD-CORE-244-FR10 says protected and permanent entries are never
removed automatically. Consolidation archives cluster members without checking.
X-04: with no warm embedder, consolidation archives clusters formed only by
shared tags, with no semantic check.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests._consolidation_test_helpers import make_vec, write_entry
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.consolidation import consolidate_cycle
from trw_mcp.state.dedup import merge_into_survivor
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


def _today() -> str:
    return datetime.now(tz=timezone.utc).date().isoformat()


class TestMergeKeepsIncomingContent:
    def test_shorter_incoming_detail_is_appended_under_audit_marker(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path,
            existing_detail="a long existing detail that is certainly longer than the new one",
            new_detail="short but distinct fact",
        )
        merge_into_survivor(path, new_data, reader, writer)
        detail = str(reader.read_yaml(path)["detail"])
        assert detail.endswith(f"\n---\nMerged from L-new01 on {_today()}:\nshort but distinct fact")

    def test_incoming_summary_survives_the_merge(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(tmp_path)
        new_data["summary"] = "incoming summary that says something new"
        merge_into_survivor(path, new_data, reader, writer)
        merged = reader.read_yaml(path)
        assert merged["summary"] == "summary"  # the survivor's summary is unchanged
        assert str(merged["detail"]).endswith(
            f"\n---\nMerged from L-new01 on {_today()}: incoming summary that says something new\n"
            "longer new detail with more info"
        )

    def test_a_detail_already_present_verbatim_is_not_appended_again(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(
            tmp_path, existing_detail="alpha. beta gamma.", new_detail="beta gamma."
        )
        new_data["summary"] = "summary"
        merge_into_survivor(path, new_data, reader, writer)
        assert reader.read_yaml(path)["detail"] == "alpha. beta gamma."

    def test_a_multiline_incoming_summary_stays_on_one_header_line(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(tmp_path, new_detail="body")
        new_data["summary"] = "first line\nsecond line"
        merge_into_survivor(path, new_data, reader, writer)
        detail = str(reader.read_yaml(path)["detail"])
        assert detail.endswith(f"\n---\nMerged from L-new01 on {_today()}: first line second line\nbody")

    def test_an_identical_incoming_summary_is_not_repeated(self, tmp_path: Path) -> None:
        from tests._factories import make_merge_scenario

        path, new_data, reader, writer = make_merge_scenario(tmp_path)
        new_data["summary"] = "summary"
        merge_into_survivor(path, new_data, reader, writer)
        detail = str(reader.read_yaml(path)["detail"])
        assert detail.endswith(f"\n---\nMerged from L-new01 on {_today()}:\nlonger new detail with more info")


def _cluster_of(entries_dir: Path, writer: FileStateWriter, protected_id: str | None) -> list[str]:
    ids = [f"entry{i:03d}" for i in range(4)]
    for i, entry_id in enumerate(ids):
        path = write_entry(entries_dir, writer, entry_id, summary=f"pattern {i}", tags=["a", "b", "c"])
        if entry_id == protected_id:
            data = FileStateReader().read_yaml(path)
            data["protection_tier"] = "protected"
            writer.write_yaml(path, data)
    return ids


def _consolidated_into(trw_dir: Path, entry_id: str) -> object:
    for path in trw_dir.rglob(f"{entry_id}.yaml"):
        data = FileStateReader().read_yaml(path)
        if "consolidated_into" in data:
            return data["consolidated_into"]
    return None


class TestConsolidationHonoursProtection:
    def test_protected_entry_is_never_archived(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        writer = FileStateWriter()
        _cluster_of(entries_dir, writer, protected_id="entry000")
        cfg = TRWConfig(memory_consolidation_enabled=True, memory_consolidation_min_cluster=3)
        llm = MagicMock(available=False)
        with (
            patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")),
            patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True),
            patch(
                "trw_mcp.state.memory_adapter.embed_text_batch", side_effect=lambda texts: [make_vec(1.0)] * len(texts)
            ),
            patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm),
        ):
            consolidate_cycle(trw_dir, config=cfg)
        assert _consolidated_into(trw_dir, "entry000") is None


def _snapshot(trw_dir: Path) -> dict[str, bytes]:
    return {str(p.relative_to(trw_dir)): p.read_bytes() for p in sorted(trw_dir.rglob("*")) if p.is_file()}


class TestTagFallbackIsNotDestructive:
    def test_no_warm_embedder_defers_and_writes_nothing_then_a_warm_retry_consolidates(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        ids = _cluster_of(entries_dir, FileStateWriter(), protected_id=None)
        cfg = TRWConfig(memory_consolidation_enabled=True, memory_consolidation_min_cluster=3)
        llm = MagicMock(available=False)
        before = _snapshot(trw_dir)
        with (
            patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")),
            patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=None),
            patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm),
        ):
            result = consolidate_cycle(trw_dir, config=cfg, allow_cold_embedder_load=False)
        assert (result["status"], result["reason"], result["consolidated_count"]) == (
            "deferred",
            "no_semantic_check",
            0,
        )
        assert _snapshot(trw_dir) == before  # no file created, changed or moved

        with (
            patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")),
            patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=object()),
            patch(
                "trw_mcp.state.memory_adapter.embed_text_batch",
                side_effect=lambda texts: [make_vec(1.0)] * len(texts),
            ),
            patch("trw_mcp.state.consolidation._cycle.LLMClient", return_value=llm),
        ):
            retried = consolidate_cycle(trw_dir, config=cfg, allow_cold_embedder_load=False)
        assert retried["status"] == "completed"
        assert [i for i in ids if _consolidated_into(trw_dir, i) is not None] == ids

    def test_dry_run_still_reports_tag_overlap_candidates(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        _cluster_of(entries_dir, FileStateWriter(), protected_id=None)
        cfg = TRWConfig(memory_consolidation_enabled=True, memory_consolidation_min_cluster=3)
        before = _snapshot(trw_dir)
        with (
            patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")),
            patch("trw_mcp.state._memory_connection.get_initialized_embedder", return_value=None),
        ):
            preview = consolidate_cycle(trw_dir, config=cfg, dry_run=True, allow_cold_embedder_load=False)
        assert preview["clusters"]
        assert _snapshot(trw_dir) == before


def test_backend_load_path_also_keeps_protected_entries_out_of_clusters(tmp_path: Path) -> None:
    """CORE-244-FR10 on the backend path, not only the YAML fallback."""
    from trw_mcp.state.consolidation._clustering import find_clusters

    entries_dir = tmp_path / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    rows = [{"id": i, "summary": "s", "detail": "d", "protection_tier": "normal"} for i in ("a", "b", "c")]
    rows += [{"id": "p", "summary": "s", "detail": "d", "protection_tier": "protected"}]
    with (
        patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=rows),
        patch("trw_mcp.state.memory_adapter.embedding_available", return_value=True),
        patch(
            "trw_mcp.state.memory_adapter.embed_text_batch",
            side_effect=lambda texts: [make_vec(1.0)] * len(texts),
        ),
    ):
        clusters = find_clusters(entries_dir, FileStateReader(), min_cluster_size=3)
    assert [sorted(str(e["id"]) for e in c) for c in clusters] == [["a", "b", "c"]]


def test_a_protected_audit_finding_still_counts_toward_audit_pattern_promotion(tmp_path: Path) -> None:
    """Exemption forbids removal, not reporting: the read-only audit report still sees it."""
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    writer = FileStateWriter()
    for n, prd in enumerate(("PRD-QUAL-056", "PRD-CORE-104", "PRD-CORE-125"), start=1):
        path = write_entry(
            entries_dir,
            writer,
            f"L-00{n}",
            summary=f"Integration wiring missing in remediation {n}",
            tags=["audit-finding", "impl_gap", prd],
        )
        if n == 1:
            data = FileStateReader().read_yaml(path)
            data["protection_tier"] = "permanent"
            writer.write_yaml(path, data)
    cfg = TRWConfig(audit_pattern_promotion_threshold=3)
    with (
        patch("trw_mcp.state.memory_adapter.list_active_learnings", side_effect=RuntimeError("force yaml")),
        patch("trw_mcp.state.consolidation._cycle.semantic_clustering_ready", return_value=True),
        patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]),
    ):
        result = consolidate_cycle(trw_dir, config=cfg)
    promotions = result["audit_pattern_promotions"]
    assert isinstance(promotions, list) and len(promotions) == 1
    assert promotions[0]["prd_count"] == 3


def test_archive_originals_skips_an_exempt_member_even_if_clustered(tmp_path: Path) -> None:
    """Defence in depth: a cluster built elsewhere still never archives an exempt entry."""
    from trw_mcp.state.consolidation._archive import _archive_originals

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()
    writer, reader = FileStateWriter(), FileStateReader()
    for entry_id in ("keep", "a", "b"):
        write_entry(entries_dir, writer, entry_id)
    cluster = [dict(reader.read_yaml(entries_dir / f"{i}.yaml")) for i in ("keep", "a", "b")]
    cluster[0]["protection_tier"] = "permanent"
    _archive_originals(cluster, "L-cons", entries_dir, reader, writer)  # type: ignore[arg-type]
    assert "consolidated_into" not in reader.read_yaml(entries_dir / "keep.yaml")
    assert reader.read_yaml(entries_dir / "a.yaml")["consolidated_into"] == "L-cons"
