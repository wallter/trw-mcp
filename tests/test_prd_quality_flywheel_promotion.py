"""Audit pattern promotion tests for the PRD quality flywheel."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.consolidation import consolidate_cycle
from trw_mcp.state.persistence import FileStateWriter


def _write_learning_entry(
    writer: FileStateWriter,
    entries_dir: Path,
    entry_id: str,
    summary: str,
    tags: list[str],
) -> None:
    writer.write_yaml(
        entries_dir / f"{entry_id}.yaml",
        {
            "id": entry_id,
            "summary": summary,
            "detail": summary,
            "status": "active",
            "tags": tags,
            "impact": 0.7,
            "created": "2026-04-01",
            "updated": "2026-04-01",
        },
    )


def test_audit_pattern_promotion(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    _write_learning_entry(
        writer,
        entries_dir,
        "L-001",
        "Integration wiring missing in remediation 1",
        ["audit-finding", "impl_gap", "PRD-QUAL-056"],
    )
    _write_learning_entry(
        writer,
        entries_dir,
        "L-002",
        "Integration wiring missing in remediation 2",
        ["audit-finding", "impl_gap", "PRD-CORE-104"],
    )
    _write_learning_entry(
        writer,
        entries_dir,
        "L-003",
        "Integration wiring missing in remediation 3",
        ["audit-finding", "impl_gap", "PRD-CORE-125"],
    )

    cfg = TRWConfig(audit_pattern_promotion_threshold=3)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    assert result["status"] == "no_clusters"
    assert result["audit_pattern_promotion_threshold"] == 3
    assert result["audit_pattern_promotions"] == [
        {
            "category": "impl_gap",
            "normalized_pattern": "integration remediation wiring",
            "pattern_summary": "Integration wiring missing in remediation 2",
            "prd_count": 3,
            "prd_ids": ["PRD-CORE-104", "PRD-CORE-125", "PRD-QUAL-056"],
            "sample_summaries": [
                "Integration wiring missing in remediation 2",
                "Integration wiring missing in remediation 3",
                "Integration wiring missing in remediation 1",
            ],
            "synthesized_summary": (
                "Recurring impl gap pattern: Integration wiring missing in remediation 2. "
                "Observed across 3 PRDs. Prevention: Verify the production call path and "
                "integration wiring before closing remediation."
            ),
            "prevention_strategy": "Verify the production call path and integration wiring before closing remediation.",
            "nudge_line": "Recurring impl gap: Integration wiring missing in remediation 2",
        }
    ]


def test_audit_pattern_promotion_respects_config_threshold(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    for idx, prd_id in enumerate(("PRD-QUAL-056", "PRD-CORE-104", "PRD-CORE-125"), start=1):
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-10{idx}",
            f"Recurring audit finding {idx}",
            ["audit-finding", "test_gap", prd_id],
        )

    cfg = TRWConfig(audit_pattern_promotion_threshold=4)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    assert result["status"] == "no_clusters"
    assert result["audit_pattern_promotion_threshold"] == 4
    assert result["audit_pattern_promotions"] == []


def test_audit_pattern_promotion_supports_integration_and_traceability_categories(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    for idx, prd_id in enumerate(("PRD-QUAL-056", "PRD-CORE-104", "PRD-CORE-125"), start=1):
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-20{idx}",
            f"Service callback wiring missing from runtime path {idx}",
            ["audit-finding", "integration_gap", prd_id],
        )
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-30{idx}",
            f"Traceability matrix stale after remediation update {idx}",
            ["audit-finding", "traceability_gap", prd_id],
        )

    cfg = TRWConfig(audit_pattern_promotion_threshold=3)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    promotions = {
        (entry["category"], entry["normalized_pattern"]): entry for entry in result["audit_pattern_promotions"]
    }
    integration = promotions[("integration_gap", "callback path runtime service wiring")]
    traceability = promotions[("traceability_gap", "after matrix remediation stale traceability update")]
    assert integration["prevention_strategy"] == (
        "Exercise end-to-end integration points, not just isolated units, before delivery."
    )
    assert "Observed across 3 PRDs" in integration["synthesized_summary"]
    assert traceability["prevention_strategy"] == (
        "Update traceability artifacts alongside code changes before delivery sign-off."
    )
    assert traceability["pattern_summary"] == "Traceability matrix stale after remediation update 2"


def test_audit_pattern_promotion_does_not_promote_same_category_count_without_shared_pattern(tmp_path: Path) -> None:
    writer = FileStateWriter()
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)

    summaries = [
        ("PRD-QUAL-056", "Missing null guard in parser"),
        ("PRD-CORE-104", "Retry budget never resets"),
        ("PRD-CORE-125", "Feature flag default is inverted"),
    ]
    for idx, (prd_id, summary) in enumerate(summaries, start=1):
        _write_learning_entry(
            writer,
            entries_dir,
            f"L-40{idx}",
            summary,
            ["audit-finding", "impl_gap", prd_id],
        )

    cfg = TRWConfig(audit_pattern_promotion_threshold=3)
    with patch("trw_mcp.state.consolidation._cycle.find_clusters", return_value=[]):
        result = consolidate_cycle(trw_dir, config=cfg)

    assert result["audit_pattern_promotions"] == []
