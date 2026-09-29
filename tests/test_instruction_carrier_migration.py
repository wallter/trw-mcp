"""Orphan-strip coverage for the instruction-file carrier — PRD-QUAL-131-FR06.

The externalization carrier that once wrote the TRW block into a ``.trw``
sidecar is gone (PRD-QUAL-143-FR01), and PRD-CORE-341 deleted the sidecar
retirement and the legacy-import-to-inline conversion this file used to pin.
The replacement behaviour (link body, migration of an old block, regeneration
of a stale header-marked ``.trw/INSTRUCTIONS.md``, no ``.retired`` copies)
lives in ``test_agents_md_link.py``. What remains here is the AGENTS.md
orphan-strip, unrelated to the carrier.
"""

from __future__ import annotations


class TestOrphanStripIsWiredAndLineAnchored:
    """PRD-QUAL-131-FR06: the AGENTS.md orphan-strip exists, runs, and is line-anchored.

    TRW 8.0 removed the CLAUDE.md sibling (TRW no longer writes CLAUDE.md), so
    the prose-marker safety case (a substring marker matcher once destroyed 705
    ROADMAP lines) is exercised through the AGENTS.md strip production invokes.
    """

    _S = "<!-- trw:start -->"
    _E = "<!-- trw:end -->"

    def test_the_agents_md_orphan_strip_is_wired_not_merely_defined(self) -> None:
        """It was deleted for having zero callers. It is back — with a caller.

        The original shipped with six passing tests and no production call site:
        every test invoked the helper directly, so they proved the code worked
        and said nothing about whether it ran. Deleting it was correct. The NEED
        was real though (a project installed before opencode's AGENTS.md was
        withdrawn keeps a frozen block forever), so it is restored and called
        from the update path. This asserts the CALL SITE, since that is the part
        that was missing; the behavior itself is covered end-to-end through
        `update_project` in test_instruction_include_matrix.py.
        """
        import inspect

        from trw_mcp.bootstrap import _template_updater
        from trw_mcp.state.claude_md import _orphan_strip

        assert hasattr(_orphan_strip, "strip_orphaned_agents_md_block")
        assert "strip_orphaned_agents_md_block(" in inspect.getsource(_template_updater._update_mcp_config), (
            "restored but unwired again"
        )

    def test_a_marker_mentioned_in_prose_does_not_delete_user_content(self, tmp_path: Path) -> None:
        """``_strip_trw_section`` DELETES what it spans, so matching must be line-anchored.

        Exercised through the AGENTS.md entry point the update path calls.
        """
        from trw_mcp.state.claude_md import _orphan_strip

        claude = tmp_path / "AGENTS.md"
        claude.write_text(
            f"# Doc\n\nWe delimit with `{self._S}`.\n\nKeep me.\n\n{self._S}\nblock\n{self._E}\n",
            encoding="utf-8",
        )

        _orphan_strip.strip_orphaned_agents_md_block(tmp_path, ["codex"])

        text = claude.read_text(encoding="utf-8")
        assert "Keep me." in text, "content around a prose mention was deleted"
        assert f"We delimit with `{self._S}`." in text

    def test_user_content_outside_the_markers_survives(self, tmp_path: Path) -> None:
        """CONSTITUTION HB-2: only the TRW-marked region may be removed.

        Proved on the path that actually strips files.
        """
        from trw_mcp.state.claude_md import _orphan_strip

        claude = tmp_path / "AGENTS.md"
        claude.write_text(
            f"# Mine\n\nMy own rules.\n\n{self._S}\nStale injected TRW text\n{self._E}\n\nMore of mine.\n",
            encoding="utf-8",
        )

        assert _orphan_strip.strip_orphaned_agents_md_block(tmp_path, ["codex"]) is True

        text = claude.read_text(encoding="utf-8")
        assert "Stale injected TRW text" not in text
        assert "My own rules." in text
        assert "More of mine." in text
