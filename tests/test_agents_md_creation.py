"""Tests for AGENTS.md creation and merge behavior."""

from __future__ import annotations

from pathlib import Path

from tests._test_agents_md_support import (
    _TRW_SECTION,
    _extract_trw_section,
    _patched_learning_env,
)
from trw_mcp.state.claude_md import TRW_MARKER_END, TRW_MARKER_START, merge_trw_section


class TestAgentsMdCreation:
    """Test AGENTS.md file creation via trw_claude_md_sync."""

    def test_agents_md_created_on_root_sync(self, tmp_project: Path) -> None:
        """The auto path no longer writes AGENTS.md for ANY detected client.

        Was: create ``.opencode/`` and assert AGENTS.md appears. Three withdrawals
        have since removed the premise. On the auto branch ``write_agents`` requires
        both a non-empty ``instruction_targets`` and a detected client whose profile
        claims ``agents_md``. The sync set is
        ``{antigravity-cli, codex, copilot, opencode}`` and **none of them claims
        agents_md** any more; the two clients that do (cursor-cli, cursor-ide) are in
        ``INSTRUCTION_SYNC_EXCLUSIONS``, so they contribute no targets.

        The test kept passing only because cursor-ide still carried a stray
        ``agents_md=True`` alongside its own carrier — the defect this change fixes.
        So it now asserts the withdrawal is complete, which is what
        PRD-CORE-240-FR04 requires, instead of asserting a write that should no
        longer happen. ``trw_instructions_sync(client="cursor-cli")`` still writes
        AGENTS.md through the explicit-client branch; that is cursor-cli's own
        carrier and is covered elsewhere.
        """
        (tmp_project / ".opencode").mkdir(exist_ok=True)
        with _patched_learning_env(tmp_project, agents_md_enabled=True) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["agents_md_synced"] is False
        assert not (tmp_project / "AGENTS.md").exists()

    def test_agents_md_content_matches_claude_md(self, tmp_project: Path) -> None:
        """AGENTS.md TRW section matches CLAUDE.md TRW section."""
        claude_target = tmp_project / "CLAUDE.md"
        agents_target = tmp_project / "AGENTS.md"

        merge_trw_section(claude_target, _TRW_SECTION, 200)
        merge_trw_section(agents_target, _TRW_SECTION, 200)

        claude_section = _extract_trw_section(claude_target.read_text(encoding="utf-8"))
        agents_section = _extract_trw_section(agents_target.read_text(encoding="utf-8"))

        assert claude_section == agents_section

    def test_agents_md_disabled_config(self, tmp_project: Path) -> None:
        """AGENTS.md is NOT created when agents_md_enabled=False."""
        with _patched_learning_env(tmp_project, agents_md_enabled=False) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["agents_md_synced"] is False
        assert result["agents_md_path"] is None
        assert not (tmp_project / "AGENTS.md").exists()

    def test_agents_md_preserves_existing_content(self, tmp_project: Path) -> None:
        """Existing non-TRW content in AGENTS.md is preserved."""
        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            "# My Custom Agents Config\n\nSome existing content.\n",
            encoding="utf-8",
        )

        merge_trw_section(agents_path, _TRW_SECTION, 200)

        content = agents_path.read_text(encoding="utf-8")
        assert "# My Custom Agents Config" in content
        assert "Some existing content." in content
        assert TRW_MARKER_START in content

    def test_agents_md_idempotent(self, tmp_project: Path) -> None:
        """Running sync three times stabilizes content (idempotent after first)."""
        agents_path = tmp_project / "AGENTS.md"
        trw_section = f"\n{TRW_MARKER_START}\n## TRW Section\n- test learning\n{TRW_MARKER_END}\n"

        merge_trw_section(agents_path, trw_section, 200)
        merge_trw_section(agents_path, trw_section, 200)
        second_content = agents_path.read_text(encoding="utf-8")

        merge_trw_section(agents_path, trw_section, 200)
        third_content = agents_path.read_text(encoding="utf-8")

        assert second_content == third_content

    def test_overflow_refuses_instead_of_cutting_inside_the_markers(self, tmp_project: Path) -> None:
        """PRD-FIX-123-FR01, inverting PRD-QUAL-018-FR02.

        This test used to assert that a truncation marker appeared and the file
        was clipped to the limit. That behaviour destroyed 128 hand-written
        lines in a reported incident, so the assertion is inverted: nothing is
        written and the file is byte-identical.
        """
        target = tmp_project / "CLAUDE.md"
        user_lines = [f"# Line {i}" for i in range(200)]
        target.write_text("\n".join(user_lines) + "\n", encoding="utf-8")
        before = target.read_bytes()

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Section\n- learning 1\n- learning 2\n{TRW_MARKER_END}\n"
        verdict = merge_trw_section(target, trw_section, max_lines=100, project_root=tmp_project)

        assert verdict.written is False
        assert verdict.refusal is not None
        assert verdict.refusal["reason"] == "oversized"
        assert target.read_bytes() == before
        assert "truncated" not in target.read_text(encoding="utf-8").lower()

    def test_overflow_without_markers_also_refuses(self, tmp_project: Path) -> None:
        """The marker-less fallback dropped 170 of 200 user lines AND the section."""
        target = tmp_project / "CLAUDE.md"
        target.write_text("\n".join(f"# Line {i}" for i in range(200)) + "\n", encoding="utf-8")
        before = target.read_bytes()

        verdict = merge_trw_section(target, "\n## New Section\n- content\n", max_lines=50, project_root=tmp_project)

        assert verdict.written is False
        assert target.read_bytes() == before

    def test_user_content_is_never_trimmed_to_make_room_for_trw(self, tmp_project: Path) -> None:
        """PRD-FIX-123-FR01: TRW no longer chooses its own bytes over the user's."""
        target = tmp_project / "CLAUDE.md"
        user_lines = [f"# User line {i}" for i in range(150)]
        target.write_text("\n".join(user_lines) + "\n", encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Generated\n- item a\n- item b\n- item c\n{TRW_MARKER_END}\n"
        verdict = merge_trw_section(target, trw_section, max_lines=50, project_root=tmp_project)

        assert verdict.written is False
        content = target.read_text(encoding="utf-8")
        assert all(f"# User line {i}" in content for i in range(150))

        # Control: with room for both, the merge still lands exactly as before.
        assert merge_trw_section(target, trw_section, max_lines=500, project_root=tmp_project).written is True
        merged = target.read_text(encoding="utf-8")
        assert all(f"# User line {i}" in merged for i in range(150))
        assert "item a" in merged and "item b" in merged and "item c" in merged

    def test_agents_md_root_scope_only(self, tmp_project: Path) -> None:
        """AGENTS.md is only synced for root scope, not sub scope."""
        sub_dir = tmp_project / "submodule"
        sub_dir.mkdir()

        with _patched_learning_env(tmp_project, agents_md_enabled=True) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="sub", target_dir=str(sub_dir))

        assert result["agents_md_synced"] is False
        assert not (tmp_project / "AGENTS.md").exists()
