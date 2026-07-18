"""PRD discovery and sequence coverage tests split from test_prd_audit_claudemd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.prd_utils import _deep_merge, discover_governing_prds, next_prd_sequence

from ._prd_audit_claudemd_support import _writer


class TestDiscoverGoverningPrds:
    """Cover lines 365-366, 375-377: tier 1 and tier 2 discovery paths."""

    def test_tier1_explicit_prd_scope(self, tmp_path: Path) -> None:
        """Cover lines 365-366: prd_scope from run.yaml."""
        run_dir = tmp_path / "test-run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        _writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "test-123",
                "prd_scope": ["PRD-CORE-007", "PRD-FIX-006"],
            },
        )
        result = discover_governing_prds(run_dir)
        assert result == ["PRD-CORE-007", "PRD-FIX-006"]

    def test_tier2_plan_md_scan(self, tmp_path: Path) -> None:
        """Cover lines 375-377: fallback to plan.md scanning."""
        run_dir = tmp_path / "test-run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        reports = run_dir / "reports"
        reports.mkdir()
        # No prd_scope in run.yaml
        _writer.write_yaml(meta / "run.yaml", {"run_id": "test-123"})
        # Plan.md has PRD references
        (reports / "plan.md").write_text(
            "# Plan\n\nImplements PRD-CORE-009. Depends on PRD-FIX-006.\n",
            encoding="utf-8",
        )
        result = discover_governing_prds(run_dir)
        assert "PRD-CORE-009" in result
        assert "PRD-FIX-006" in result

    def test_tier3_empty_when_no_sources(self, tmp_path: Path) -> None:
        """Tier 3: no run.yaml, no plan.md → empty list."""
        run_dir = tmp_path / "test-run"
        run_dir.mkdir()
        result = discover_governing_prds(run_dir)
        assert result == []

    def test_tier1_with_empty_prd_scope_falls_to_tier2(self, tmp_path: Path) -> None:
        """Empty prd_scope list in run.yaml must fall through to tier 2."""
        run_dir = tmp_path / "test-run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        reports = run_dir / "reports"
        reports.mkdir()
        _writer.write_yaml(
            meta / "run.yaml",
            {
                "run_id": "test-123",
                "prd_scope": [],  # empty list — should fall through
            },
        )
        (reports / "plan.md").write_text(
            "References PRD-QUAL-013.\n",
            encoding="utf-8",
        )
        result = discover_governing_prds(run_dir)
        assert "PRD-QUAL-013" in result


class TestDeepMergeEdgeCases:
    """Cover line 391: _deep_merge early return when target is not a dict."""

    def test_non_dict_target_is_noop(self) -> None:
        # Calling _deep_merge on a non-dict target should not raise
        _deep_merge("not a dict", {"key": "value"})
        # No exception = success

    def test_non_dict_target_none_is_noop(self) -> None:
        _deep_merge(None, {"key": "value"})

    def test_nested_dict_values_are_merged_recursively(self) -> None:
        target: dict[str, object] = {
            "dates": {"created": "2026-01-01", "updated": "2026-01-01"},
            "title": "Original",
        }
        source: dict[str, object] = {
            "dates": {"updated": "2026-02-22"},
            "title": "Updated",
        }
        _deep_merge(target, source)
        dates = target["dates"]
        assert isinstance(dates, dict)
        assert dates["updated"] == "2026-02-22"
        assert dates["created"] == "2026-01-01"  # preserved
        assert target["title"] == "Updated"


class TestNextPrdSequence:
    """Cover lines 429-430: archive directory scanning."""

    def test_scans_archive_prds_dir(self, tmp_path: Path) -> None:
        """Archive PRDs should prevent ID reuse."""
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        archive_dir = tmp_path / "archive" / "prds"
        archive_dir.mkdir(parents=True)

        # Active PRDs: CORE-001 through CORE-003
        for i in range(1, 4):
            (prds_dir / f"PRD-CORE-{i:03d}.md").write_text("---\nid: x\n---\n")

        # Archived PRD: CORE-010 (higher than active)
        (archive_dir / "PRD-CORE-010.md").write_text("---\nid: x\n---\n")

        result = next_prd_sequence(prds_dir, "CORE")
        # Should be max(3, 10) + 1 = 11
        assert result == 11

    def test_no_archive_dir_still_works(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        (prds_dir / "PRD-FIX-005.md").write_text("---\nid: x\n---\n")
        result = next_prd_sequence(prds_dir, "FIX")
        assert result == 6

    def test_empty_prds_dir_returns_one(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        result = next_prd_sequence(prds_dir, "CORE")
        assert result == 1

    def test_non_numeric_stem_skipped(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        (prds_dir / "PRD-CORE-ABC.md").write_text("---\nid: x\n---\n")
        (prds_dir / "PRD-CORE-002.md").write_text("---\nid: x\n---\n")
        result = next_prd_sequence(prds_dir, "CORE")
        assert result == 3

    def test_suffixed_filename_owns_its_sequence(self, tmp_path: Path) -> None:
        """PRD-QUAL-121-FR02: suffixed stems must be counted — the pre-fix parser
        skipped them, so the allocator re-issued their identifiers (the root cause
        of the 13 duplicate-ID pairs in the 2026-07-11 baseline census)."""
        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        (prds_dir / "PRD-CORE-002.md").write_text("---\nid: x\n---\n")
        (prds_dir / "PRD-CORE-153-registry-hygiene.md").write_text("---\nid: x\n---\n")
        assert next_prd_sequence(prds_dir, "CORE") == 154


class TestFindIdentityCollisions:
    """PRD-QUAL-121-FR02: shared collision rule for allocation."""

    def test_exact_and_suffixed_and_archived_owners_conflict(self, tmp_path: Path) -> None:
        from trw_mcp.state.prd_utils import find_identity_collisions

        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        archive = tmp_path / "archive" / "prds"
        archive.mkdir(parents=True)
        exact = prds_dir / "PRD-CORE-153.md"
        exact.write_text("---\nprd:\n  id: PRD-CORE-153\n---\n")
        suffixed = archive / "PRD-CORE-153-registry-hygiene.md"
        suffixed.write_text("---\nprd:\n  id: PRD-CORE-153\n---\n")

        conflicts = find_identity_collisions(prds_dir, "PRD-CORE-153")
        assert str(exact) in conflicts and str(suffixed) in conflicts

    def test_frontmatter_claimed_id_conflicts_despite_foreign_filename(self, tmp_path: Path) -> None:
        """Re-audit finding 1 (2026-07-11): the fixture must NOT match the old
        PRD-*.md glob, or the test cannot detect a revert of the *.md widening."""
        from trw_mcp.state.prd_utils import find_identity_collisions

        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        # Genuinely foreign-named: no PRD- prefix at all.
        renamed = prds_dir / "registry-hygiene-notes.md"
        renamed.write_text("---\nprd:\n  id: PRD-CORE-153\n  title: Renamed\n---\n")
        assert find_identity_collisions(prds_dir, "PRD-CORE-153") == [str(renamed)]
        # And the PRD-prefixed variant still conflicts (pre-fix behavior kept).
        prefixed = prds_dir / "PRD-CORE-999.md"
        prefixed.write_text("---\nprd:\n  id: PRD-CORE-153\n  title: Renamed2\n---\n")
        assert str(prefixed) in find_identity_collisions(prds_dir, "PRD-CORE-153")

    def test_free_id_and_prefix_lookalike_do_not_conflict(self, tmp_path: Path) -> None:
        from trw_mcp.state.prd_utils import find_identity_collisions

        prds_dir = tmp_path / "prds"
        prds_dir.mkdir()
        # PRD-CORE-1530 is NOT owned by PRD-CORE-153 (name-boundary rule).
        (prds_dir / "PRD-CORE-1530.md").write_text("---\nprd:\n  id: PRD-CORE-1530\n---\n")
        assert find_identity_collisions(prds_dir, "PRD-CORE-153") == []


class TestDiscoverGoverningPrdsExceptionHandlers:
    """Cover prd_utils.py lines 365-366 and 376-377: exception handlers."""

    def test_tier1_read_error_falls_through_to_tier2(self, tmp_path: Path) -> None:
        """Cover lines 365-366: StateError in tier 1 read."""
        run_dir = tmp_path / "test-run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        reports = run_dir / "reports"
        reports.mkdir()

        # Write corrupt run.yaml that triggers read error
        (meta / "run.yaml").write_text(": bad yaml: [broken\n", encoding="utf-8")
        (reports / "plan.md").write_text("Implements PRD-CORE-011.\n", encoding="utf-8")

        result = discover_governing_prds(run_dir)
        # Should fall through to tier 2 plan.md scan
        assert "PRD-CORE-011" in result

    def test_tier2_oserror_falls_through_to_tier3(self, tmp_path: Path) -> None:
        """Cover lines 376-377: OSError in plan.md read."""
        run_dir = tmp_path / "test-run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        reports = run_dir / "reports"
        reports.mkdir()

        # No prd_scope in run.yaml
        _writer.write_yaml(meta / "run.yaml", {"run_id": "test-123"})

        # Create plan.md but patch read_text to raise OSError
        (reports / "plan.md").write_text("placeholder", encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=OSError("read failed")):
            result = discover_governing_prds(run_dir)

        # Falls through to tier 3 — empty list
        assert result == []
