"""Frontmatter/prose sync tests for FIX-056."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path


class TestUpdateFrontmatterProseSyncFR02:
    """Tests that update_frontmatter() syncs the prose Quick Reference status line."""

    def test_update_frontmatter_syncs_prose(self, tmp_path: Path) -> None:
        """update_frontmatter with status update must sync prose **Status** line."""
        from trw_mcp.state.prd_utils import parse_frontmatter, update_frontmatter

        prd_file = tmp_path / "PRD-TEST-001.md"
        prd_file.write_text(
            textwrap.dedent("""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test PRD
              version: '1.0'
              status: draft
              priority: P1
              category: TEST
            ---

            # PRD-TEST-001: Test PRD

            **Quick Reference**:
            - **Status**: Draft
            - **Priority**: P1

            ## 1. Problem Statement

            Body here.
        """),
            encoding="utf-8",
        )

        update_frontmatter(prd_file, {"status": "review"})

        updated = prd_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(updated)
        assert fm.get("status") == "review", f"Frontmatter status not updated: {fm.get('status')!r}"
        assert "- **Status**: Review" in updated, f"Prose status not synced in body:\n{updated}"

        prose_status_lines = re.findall(r"- \*\*Status\*\*: (\w+)", updated)
        assert all(status.lower() == "review" for status in prose_status_lines), (
            f"Found prose status lines not updated: {prose_status_lines}"
        )
