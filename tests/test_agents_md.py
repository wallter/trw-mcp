"""Tests for PRD-INFRA-001: AGENTS.md cross-tool compatibility sync."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import TRW_MARKER_END, TRW_MARKER_START, merge_trw_section
from trw_mcp.tools.learning import register_learning_tools

# Reusable TRW section for merge_trw_section tests
_TRW_SECTION = f"\n{TRW_MARKER_START}\n## TRW Section\n- test\n{TRW_MARKER_END}\n"


def _extract_trw_section(content: str) -> str:
    """Extract the TRW marker-delimited section from file content."""
    start = content.index(TRW_MARKER_START)
    end = content.index(TRW_MARKER_END) + len(TRW_MARKER_END)
    return content[start:end]


@contextmanager
def _patched_learning_env(
    project_root: Path,
    *,
    agents_md_enabled: bool = True,
) -> Generator[dict[str, Any], None, None]:
    """Patch learning tool dependencies and yield a tool-name-to-tool map.

    Centralizes the 5 patches + server setup shared across integration tests.
    """
    with (
        patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=project_root / ".trw"),
        patch("trw_mcp.tools.learning.get_config", return_value=TRWConfig(agents_md_enabled=agents_md_enabled)),
        patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
        patch("trw_mcp.state.claude_md.resolve_project_root", return_value=project_root),
        patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=project_root / ".trw"),
    ):
        server = FastMCP("test")
        register_learning_tools(server)
        tools = get_tools_sync(server)
        yield tools


class TestAgentsMdCreation:
    """Test AGENTS.md file creation via trw_claude_md_sync."""

    def test_agents_md_created_on_root_sync(self, tmp_project: Path) -> None:
        """AGENTS.md is created when scope='root', agents_md_enabled=True, and opencode detected."""
        # FR13: AGENTS.md requires opencode IDE detection (via .opencode/ dir)
        (tmp_project / ".opencode").mkdir(exist_ok=True)
        with _patched_learning_env(tmp_project, agents_md_enabled=True) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert result["agents_md_synced"] is True
        agents_path = tmp_project / "AGENTS.md"
        assert agents_path.exists()
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content

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

        # First call creates the file
        merge_trw_section(agents_path, trw_section, 200)

        # Second call replaces markers -- may differ from first due to .lstrip()
        merge_trw_section(agents_path, trw_section, 200)
        second_content = agents_path.read_text(encoding="utf-8")

        # Third call should produce identical content to second
        merge_trw_section(agents_path, trw_section, 200)
        third_content = agents_path.read_text(encoding="utf-8")

        assert second_content == third_content

    def test_truncation_preserves_trw_markers(self, tmp_project: Path) -> None:
        """QUAL-018: Truncation never cuts inside TRW marker boundaries."""
        target = tmp_project / "CLAUDE.md"
        # Create a large user section that will exceed the line limit
        user_lines = [f"# Line {i}" for i in range(200)]
        user_content = "\n".join(user_lines) + "\n"
        target.write_text(user_content, encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Section\n- learning 1\n- learning 2\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=100)

        content = target.read_text(encoding="utf-8")
        assert TRW_MARKER_START in content, "TRW start marker must survive truncation"
        assert TRW_MARKER_END in content, "TRW end marker must survive truncation"
        assert "learning 1" in content, "TRW section content must survive truncation"
        assert "truncated" in content.lower(), "Truncation comment should be present"
        assert content.split("\n").__len__() <= 102, "Total lines should respect limit"

    def test_truncation_without_markers_falls_back(self, tmp_project: Path) -> None:
        """QUAL-018: Without TRW markers, truncation falls back to simple slice."""
        target = tmp_project / "CLAUDE.md"
        content = "\n".join([f"# Line {i}" for i in range(200)]) + "\n"
        target.write_text(content, encoding="utf-8")

        # No TRW section — just truncate
        merge_trw_section(target, "\n## New Section\n- content\n", max_lines=50)

        result = target.read_text(encoding="utf-8")
        lines = result.split("\n")
        assert len(lines) <= 52  # 50 + truncation comment + possible trailing newline

    def test_truncation_user_content_trimmed_not_trw(self, tmp_project: Path) -> None:
        """QUAL-018: User content is trimmed, TRW section is preserved intact."""
        target = tmp_project / "CLAUDE.md"
        user_lines = [f"# User line {i}" for i in range(150)]
        target.write_text("\n".join(user_lines) + "\n", encoding="utf-8")

        trw_section = f"\n{TRW_MARKER_START}\n## TRW Generated\n- item a\n- item b\n- item c\n{TRW_MARKER_END}\n"
        merge_trw_section(target, trw_section, max_lines=50)

        content = target.read_text(encoding="utf-8")
        # TRW section must be fully intact
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content
        assert "item a" in content
        assert "item b" in content
        assert "item c" in content
        # User content should be truncated
        assert "User line 149" not in content

    def test_agents_md_root_scope_only(self, tmp_project: Path) -> None:
        """AGENTS.md is only synced for root scope, not sub scope."""
        sub_dir = tmp_project / "submodule"
        sub_dir.mkdir()

        with _patched_learning_env(tmp_project, agents_md_enabled=True) as tools:
            result = tools["trw_claude_md_sync"].fn(
                scope="sub",
                target_dir=str(sub_dir),
            )

        assert result["agents_md_synced"] is False
        assert not (tmp_project / "AGENTS.md").exists()


# ---------------------------------------------------------------------------
# Tests for _migrate_trw_content_from_agents_md (PRD-CORE-115 FR08)
# ---------------------------------------------------------------------------


class TestMigrateTrwContentFromAgentsMd:
    """Tests for AGENTS.md TRW content migration — FR08."""

    def test_strips_trw_markers_from_agents_md(self, tmp_project: Path) -> None:
        """TRW markers are removed from AGENTS.md after migration."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._agents_md import _migrate_trw_content_from_agents_md

        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Section\n\n{TRW_MARKER_START}\n## TRW\n- gotcha\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is True
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in content
        assert TRW_MARKER_END not in content

    def test_preserves_user_content_after_stripping(self, tmp_project: Path) -> None:
        """User-authored content before and after TRW block is preserved."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._agents_md import _migrate_trw_content_from_agents_md

        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# My Custom Config\n\nUser note.\n\n"
            f"{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n\n"
            "Post-TRW user content.\n",
            encoding="utf-8",
        )

        _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        content = agents_path.read_text(encoding="utf-8")
        assert "# My Custom Config" in content
        assert "User note." in content
        assert "Post-TRW user content." in content

    def test_strips_trw_auto_comment_along_with_markers(self, tmp_project: Path) -> None:
        """TRW_AUTO_COMMENT that precedes TRW block is removed alongside the markers."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._agents_md import _migrate_trw_content_from_agents_md
        from trw_mcp.state.claude_md._parser import TRW_AUTO_COMMENT

        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Section\n\n"
            f"{TRW_AUTO_COMMENT}\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is True
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_AUTO_COMMENT not in content
        assert TRW_MARKER_START not in content
        assert TRW_MARKER_END not in content
        assert "# User Section" in content

    def test_idempotent_when_no_ide_detected(self, tmp_project: Path) -> None:
        """Running migration twice on a project with no IDE produces same result."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._agents_md import _migrate_trw_content_from_agents_md

        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Content\n\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        # First run strips markers
        migrated1, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())
        content_after_first = agents_path.read_text(encoding="utf-8")

        # Second run: no markers left, returns False
        migrated2, _ = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())
        content_after_second = agents_path.read_text(encoding="utf-8")

        assert migrated1 is True
        assert migrated2 is False  # No markers left — nothing to do
        assert content_after_first == content_after_second  # File unchanged on second run

    def test_returns_false_when_agents_md_missing(self, tmp_project: Path) -> None:
        """No AGENTS.md → (False, '') returned immediately."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._agents_md import _migrate_trw_content_from_agents_md

        migrated, path = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is False
        assert path == ""

    def test_strips_markers_even_when_no_ide_detected(self, tmp_project: Path) -> None:
        """TRW markers are stripped from AGENTS.md even when no IDE config dir is present."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._agents_md import _migrate_trw_content_from_agents_md

        # tmp_project has no .opencode/ or .codex/ directory
        agents_path = tmp_project / "AGENTS.md"
        agents_path.write_text(
            f"# User Content\n\n{TRW_MARKER_START}\n## TRW\n- item\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated, path = _migrate_trw_content_from_agents_md(tmp_project, TRWConfig())

        assert migrated is True
        assert path == ""  # No per-client instruction file created
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in content
        assert "# User Content" in content


# ---------------------------------------------------------------------------
# Tests for _sync_instruction_file_if_needed (PRD-CORE-115 FR06)
# ---------------------------------------------------------------------------


class TestSyncInstructionFileIfNeeded:
    """Unit tests for _sync_instruction_file_if_needed."""

    def test_returns_false_when_instruction_path_is_none(self, tmp_project: Path) -> None:
        """No instruction_path → (False, None) returned, no files created."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(None, tmp_project, "claude-code")

        assert synced is False
        assert path is None

    def test_returns_false_when_instruction_path_is_empty(self, tmp_project: Path) -> None:
        """Empty string instruction_path → (False, None) returned."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed("", tmp_project, "claude-code")

        assert synced is False
        assert path is None

    def test_codex_instruction_file_created(self, tmp_project: Path) -> None:
        """codex client with .codex/INSTRUCTIONS.md path → file is created."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(
            ".codex/INSTRUCTIONS.md", tmp_project, "codex"
        )

        assert synced is True
        assert path is not None
        instructions_file = tmp_project / ".codex" / "INSTRUCTIONS.md"
        assert instructions_file.exists()
        content = instructions_file.read_text(encoding="utf-8")
        assert "trw_session_start" in content or "TRW" in content

    def test_opencode_instruction_file_created_generic(self, tmp_project: Path) -> None:
        """opencode client with .opencode/INSTRUCTIONS.md path → file is created (generic model)."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(
            ".opencode/INSTRUCTIONS.md", tmp_project, "opencode"
        )

        assert synced is True
        assert path is not None
        instructions_file = tmp_project / ".opencode" / "INSTRUCTIONS.md"
        assert instructions_file.exists()
        content = instructions_file.read_text(encoding="utf-8")
        assert "trw_session_start" in content or "TRW" in content

    def test_opencode_instruction_uses_model_family_from_opencode_json(self, tmp_project: Path) -> None:
        """opencode sync reads model family from opencode.json and generates correct content."""
        import json

        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        # Write an opencode.json with a GPT model
        opencode_json_path = tmp_project / "opencode.json"
        opencode_json_path.write_text(json.dumps({"model": "gpt-4o"}), encoding="utf-8")

        synced, path = _sync_instruction_file_if_needed(
            ".opencode/INSTRUCTIONS.md", tmp_project, "opencode"
        )

        assert synced is True
        instructions_file = tmp_project / ".opencode" / "INSTRUCTIONS.md"
        assert instructions_file.exists()
        # GPT-family-specific content should be present
        content = instructions_file.read_text(encoding="utf-8")
        assert "GPT" in content

    def test_codex_path_inferred_from_instruction_path_for_auto_client(self, tmp_project: Path) -> None:
        """auto client with .codex/ instruction_path → generates codex instructions."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(
            ".codex/INSTRUCTIONS.md", tmp_project, "auto"
        )

        assert synced is True
        assert (tmp_project / ".codex" / "INSTRUCTIONS.md").exists()

    def test_opencode_path_inferred_from_instruction_path_for_all_client(self, tmp_project: Path) -> None:
        """all client with .opencode/ instruction_path → generates opencode instructions."""
        from trw_mcp.state.claude_md._agents_md import _sync_instruction_file_if_needed

        synced, path = _sync_instruction_file_if_needed(
            ".opencode/INSTRUCTIONS.md", tmp_project, "all"
        )

        assert synced is True
        assert (tmp_project / ".opencode" / "INSTRUCTIONS.md").exists()


class TestSyncIncludesInstructionFile:
    """Integration tests verifying sync result includes instruction_file fields (FR06)."""

    def test_sync_result_has_instruction_file_fields(self, tmp_project: Path) -> None:
        """trw_claude_md_sync result always includes instruction_file_synced and instruction_file_path keys."""
        with _patched_learning_env(tmp_project) as tools:
            result = tools["trw_claude_md_sync"].fn(scope="root")

        assert "instruction_file_synced" in result
        assert "instruction_file_path" in result

    def test_codex_sync_creates_instruction_file(self, tmp_project: Path) -> None:
        """trw_claude_md_sync with client='codex' creates .codex/INSTRUCTIONS.md."""
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_project / ".trw"),
            patch("trw_mcp.tools.learning.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_project / ".trw"),
        ):
            server = FastMCP("test")
            register_learning_tools(server)
            tools = get_tools_sync(server)
            result = tools["trw_claude_md_sync"].fn(scope="root", client="codex")

        assert result["instruction_file_synced"] is True
        assert result["instruction_file_path"] is not None
        assert (tmp_project / ".codex" / "INSTRUCTIONS.md").exists()

    def test_opencode_sync_creates_instruction_file(self, tmp_project: Path) -> None:
        """trw_claude_md_sync with client='opencode' creates .opencode/INSTRUCTIONS.md."""
        with (
            patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=tmp_project / ".trw"),
            patch(
                "trw_mcp.tools.learning.get_config",
                return_value=TRWConfig(),
            ),
            patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=tmp_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=tmp_project / ".trw"),
        ):
            server = FastMCP("test")
            register_learning_tools(server)
            tools = get_tools_sync(server)
            result = tools["trw_claude_md_sync"].fn(scope="root", client="opencode")

        assert result["instruction_file_synced"] is True
        assert result["instruction_file_path"] is not None
        assert (tmp_project / ".opencode" / "INSTRUCTIONS.md").exists()

    def test_determine_write_targets_returns_codex_instruction_path(self, tmp_project: Path) -> None:
        """_determine_write_targets with client='codex' returns .codex/INSTRUCTIONS.md."""
        from trw_mcp.state.claude_md._agents_md import _determine_write_targets

        config = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets(
            "codex", config, tmp_project, "root"
        )

        assert write_claude is False
        assert instruction_path == ".codex/INSTRUCTIONS.md"

    def test_determine_write_targets_returns_opencode_instruction_path(self, tmp_project: Path) -> None:
        """_determine_write_targets with client='opencode' returns .opencode/INSTRUCTIONS.md."""
        from trw_mcp.state.claude_md._agents_md import _determine_write_targets

        config = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets(
            "opencode", config, tmp_project, "root"
        )

        assert write_claude is False
        assert instruction_path == ".opencode/INSTRUCTIONS.md"

    def test_determine_write_targets_returns_none_for_claude_code(self, tmp_project: Path) -> None:
        """_determine_write_targets with client='claude-code' returns empty instruction_path."""
        from trw_mcp.state.claude_md._agents_md import _determine_write_targets

        config = TRWConfig()
        write_claude, write_agents, instruction_path = _determine_write_targets(
            "claude-code", config, tmp_project, "root"
        )

        assert write_claude is True
        # claude-code uses instruction_path=".claude/INSTRUCTIONS.md" from the profile
        assert instruction_path == ".claude/INSTRUCTIONS.md"


# ---------------------------------------------------------------------------
# Tests for FR04: OpenCode Model-Specific Instruction Variants (PRD-CORE-115)
# ---------------------------------------------------------------------------


class TestRenderOpencodeInstructions:
    """Unit tests for render_opencode_instructions — FR04 model-specific variants."""

    def _render(self, family: str) -> str:
        from trw_mcp.state.claude_md._static_sections import render_opencode_instructions

        return render_opencode_instructions(family)

    def test_qwen_content_is_distinct_from_gpt(self) -> None:
        """Qwen and GPT variants produce materially different content."""
        qwen = self._render("qwen")
        gpt = self._render("gpt")
        assert qwen != gpt

    def test_qwen_content_is_distinct_from_claude(self) -> None:
        """Qwen and Claude variants produce materially different content."""
        qwen = self._render("qwen")
        claude = self._render("claude")
        assert qwen != claude

    def test_gpt_content_is_distinct_from_claude(self) -> None:
        """GPT and Claude variants produce materially different content."""
        gpt = self._render("gpt")
        claude = self._render("claude")
        assert gpt != claude

    def test_generic_content_is_distinct_from_all_named_families(self) -> None:
        """Generic variant is distinct from all named family variants."""
        generic = self._render("generic")
        for family in ("qwen", "gpt", "claude"):
            assert generic != self._render(family), f"generic should differ from {family}"

    def test_qwen_contains_context_budget_guidance(self) -> None:
        """Qwen variant includes 32K context budget guidance."""
        content = self._render("qwen")
        assert "32K" in content

    def test_qwen_contains_think_tag_guidance(self) -> None:
        """Qwen variant mentions /think tags for reasoning."""
        content = self._render("qwen")
        assert "/think" in content

    def test_qwen_contains_vllm_bug_workaround(self) -> None:
        """Qwen variant includes vLLM tool-call bug workaround guidance."""
        content = self._render("qwen")
        # The known vLLM streaming parser bug must be documented
        assert "vLLM" in content or "vllm" in content

    def test_gpt_contains_context_budget_guidance(self) -> None:
        """GPT variant includes 128K+ context budget guidance."""
        content = self._render("gpt")
        assert "128K" in content

    def test_gpt_contains_chain_of_thought(self) -> None:
        """GPT variant mentions chain-of-thought reasoning."""
        content = self._render("gpt")
        assert "chain-of-thought" in content.lower() or "chain of thought" in content.lower()

    def test_gpt_mentions_o3_or_o1_models(self) -> None:
        """GPT variant includes guidance for o3/o1 reasoning models."""
        content = self._render("gpt")
        assert "o3" in content or "o1" in content

    def test_claude_contains_200k_context_guidance(self) -> None:
        """Claude variant includes 200K context budget guidance."""
        content = self._render("claude")
        assert "200K" in content

    def test_claude_contains_extended_thinking(self) -> None:
        """Claude variant mentions extended thinking."""
        content = self._render("claude")
        assert "extended thinking" in content.lower()

    def test_claude_contains_xml_tags(self) -> None:
        """Claude variant mentions XML tag conventions."""
        content = self._render("claude")
        assert "XML" in content or "<task>" in content or "xml" in content.lower()

    def test_generic_assumes_32k_budget(self) -> None:
        """Generic fallback variant assumes 32K context budget."""
        content = self._render("generic")
        assert "32K" in content

    def test_generic_contains_explicit_mcp_examples(self) -> None:
        """Generic variant includes explicit MCP tool examples."""
        content = self._render("generic")
        assert "trw_session_start()" in content
        assert "trw_deliver()" in content
        assert "trw_checkpoint" in content

    def test_unknown_family_falls_back_to_generic(self) -> None:
        """Unknown model family falls back gracefully to generic variant."""
        generic = self._render("generic")
        unknown = self._render("llama")
        assert unknown == generic

    def test_all_variants_contain_trw_session_start(self) -> None:
        """All variants instruct agents to call trw_session_start()."""
        for family in ("qwen", "gpt", "claude", "generic"):
            content = self._render(family)
            assert "trw_session_start" in content, f"{family} missing trw_session_start"

    def test_all_variants_contain_trw_deliver(self) -> None:
        """All variants instruct agents to call trw_deliver()."""
        for family in ("qwen", "gpt", "claude", "generic"):
            content = self._render(family)
            assert "trw_deliver" in content, f"{family} missing trw_deliver"

    def test_no_variant_contains_claude_md_reference(self) -> None:
        """No OpenCode variant references CLAUDE.md — that is Claude Code only."""
        for family in ("qwen", "gpt", "claude", "generic"):
            content = self._render(family)
            assert "CLAUDE.md" not in content, f"{family} contains forbidden CLAUDE.md reference"

    def test_qwen_word_count_within_target(self) -> None:
        """Qwen variant stays within 2000 words (light-mode target per NFR01)."""
        content = self._render("qwen")
        word_count = len(content.split())
        assert word_count <= 2000, f"Qwen variant has {word_count} words (target ≤2000)"

    def test_gpt_word_count_within_target(self) -> None:
        """GPT variant stays within 2000 words."""
        content = self._render("gpt")
        word_count = len(content.split())
        assert word_count <= 2000, f"GPT variant has {word_count} words (target ≤2000)"

    def test_claude_word_count_within_target(self) -> None:
        """Claude variant stays within 4000 words (full-mode target per NFR01)."""
        content = self._render("claude")
        word_count = len(content.split())
        assert word_count <= 4000, f"Claude variant has {word_count} words (target ≤4000)"

    def test_generic_word_count_within_target(self) -> None:
        """Generic variant stays within 2000 words."""
        content = self._render("generic")
        word_count = len(content.split())
        assert word_count <= 2000, f"Generic variant has {word_count} words (target ≤2000)"


# ---------------------------------------------------------------------------
# Tests for FR05: detect_model_family classification (PRD-CORE-115)
# ---------------------------------------------------------------------------


class TestDetectModelFamily:
    """Unit tests for detect_model_family — FR05 model ID classification."""

    def _detect(self, model: str) -> str:
        from trw_mcp.bootstrap._opencode import detect_model_family

        return detect_model_family({"model": model})

    def test_qwen_model_id_detected(self) -> None:
        """vllm/Qwen/Qwen3-Coder-Next-FP8 maps to 'qwen'."""
        assert self._detect("vllm/Qwen/Qwen3-Coder-Next-FP8") == "qwen"

    def test_qwen_lowercase_detected(self) -> None:
        """qwen3-coder (lowercase) maps to 'qwen'."""
        assert self._detect("qwen3-coder") == "qwen"

    def test_gpt_model_id_detected(self) -> None:
        """gpt-5.4 maps to 'gpt'."""
        assert self._detect("gpt-5.4") == "gpt"

    def test_gpt4o_model_id_detected(self) -> None:
        """gpt-4o maps to 'gpt'."""
        assert self._detect("gpt-4o") == "gpt"

    def test_o3_mini_model_id_detected(self) -> None:
        """o3-mini maps to 'gpt'."""
        assert self._detect("o3-mini") == "gpt"

    def test_o3_model_id_detected(self) -> None:
        """o3 maps to 'gpt'."""
        assert self._detect("o3") == "gpt"

    def test_o1_model_id_detected(self) -> None:
        """o1 maps to 'gpt'."""
        assert self._detect("o1") == "gpt"

    def test_o1_preview_model_id_detected(self) -> None:
        """o1-preview maps to 'gpt'."""
        assert self._detect("o1-preview") == "gpt"

    def test_claude_sonnet_model_id_detected(self) -> None:
        """claude-sonnet-4-6 maps to 'claude'."""
        assert self._detect("claude-sonnet-4-6") == "claude"

    def test_claude_opus_model_id_detected(self) -> None:
        """claude-opus-4-6 maps to 'claude'."""
        assert self._detect("claude-opus-4-6") == "claude"

    def test_unknown_model_falls_back_to_generic(self) -> None:
        """Unknown model ID maps to 'generic'."""
        assert self._detect("my-custom-model-7b") == "generic"

    def test_empty_model_falls_back_to_generic(self) -> None:
        """Empty model field maps to 'generic'."""
        from trw_mcp.bootstrap._opencode import detect_model_family

        assert detect_model_family({}) == "generic"
        assert detect_model_family({"model": ""}) == "generic"

    def test_llama_model_falls_back_to_generic(self) -> None:
        """llama3 model maps to 'generic'."""
        assert self._detect("meta/llama3-70b-instruct") == "generic"

    def test_case_insensitive_detection(self) -> None:
        """Detection is case-insensitive for all families."""
        assert self._detect("GPT-4O") == "gpt"
        assert self._detect("CLAUDE-SONNET") == "claude"
        assert self._detect("QWEN3-CODER") == "qwen"
