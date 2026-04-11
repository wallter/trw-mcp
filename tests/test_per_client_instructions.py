"""Tests for per-client instruction generation — PRD-CORE-115.

Tests for model-family-specific instruction generation, per-client instruction files
(.opencode/INSTRUCTIONS.md, .codex/INSTRUCTIONS.md), and AGENTS.md migration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._opencode import (
    detect_model_family,
    generate_codex_instructions,
    generate_opencode_instructions,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._agents_md import (
    TRW_MARKER_END,
    TRW_MARKER_START,
    _migrate_trw_content_from_agents_md,
)
from trw_mcp.state.claude_md._static_sections import (
    render_codex_instructions,
    render_codex_trw_section,
    render_opencode_instructions,
)

# ── Render Tests ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRenderCodexInstructions:
    """Tests for render_codex_instructions()."""

    def test_render_codex_instructions_returns_markdown(self) -> None:
        """render_codex_instructions() returns valid markdown string."""
        result = render_codex_instructions()
        assert isinstance(result, str)
        assert "## Codex Workflow" in result
        assert "trw_session_start" in result
        assert "trw_deliver" in result

    def test_render_codex_instructionsstructure(self) -> None:
        """render_codex_instructions() has expected 5-step workflow structure."""
        result = render_codex_instructions()

        steps = [
            "Start",
            "Delegate",
            "Verify",
            "Learn",
            "Finish",
        ]

        for step in steps:
            assert f"**{step}**" in result or f"**{step}**" in result or f"**{step.lower().capitalize()}**" in result

    def test_codex_guidance_avoids_stale_budget_and_framework_claims(self) -> None:
        """Codex instructions should not claim a fixed 200K budget or require FRAMEWORK.md."""
        result = render_codex_instructions()

        assert "200K" not in result
        assert "Read `.trw/frameworks/FRAMEWORK.md`" not in result
        assert "features.codex_hooks = true" not in result

    def test_codex_guidance_matches_current_docs(self) -> None:
        """Codex docs and renderer both describe the same supported runtime surfaces."""
        docs_text = (Path(__file__).resolve().parents[2] / "docs" / "CLIENT-PROFILES.md").read_text(encoding="utf-8")
        result = render_codex_instructions()

        assert "## Codex Support Surface" in docs_text
        assert ".codex/INSTRUCTIONS.md" in docs_text
        assert ".codex/agents/*.toml" in docs_text
        assert "experimental and optional" in docs_text.lower()
        assert "AGENTS.md" in docs_text

        assert ".codex/INSTRUCTIONS.md" in result
        assert ".codex/agents/*.toml" in result
        assert "experimental and optional" in result.lower()
        assert "AGENTS.md" in result

    def test_codex_agents_section_avoids_stale_guidance(self) -> None:
        """Codex AGENTS.md guidance should stay portable and fail open on hooks."""
        result = render_codex_trw_section()

        assert "200K" not in result
        assert "Read `.trw/frameworks/FRAMEWORK.md`" not in result
        assert ".codex/agents/*.toml" in result
        assert "experimental and optional" in result.lower()


@pytest.mark.unit
class TestRenderOpencodeInstructions:
    """Tests for render_opencode_instructions(model_family)."""

    @pytest.mark.parametrize(
        ("model_family", "expected_title"),
        [
            ("qwen", "# Qwen-Coder-Next TRW Instructions"),
            ("gpt", "# GPT TRW Instructions"),
            ("claude", "# Claude TRW Instructions"),
            ("generic", "# TRW Instructions"),
        ],
    )
    def test_model_family_specific_title(self, model_family: str, expected_title: str) -> None:
        """Each model family has its own title."""
        result = render_opencode_instructions(model_family)

        assert expected_title in result, f"Missing title '{expected_title}' for {model_family}"

    def test_all_model_families_share_common_workflow(self) -> None:
        """All model families share the same 5-step workflow."""
        for model_family in ["qwen", "gpt", "claude", "generic"]:
            result = render_opencode_instructions(model_family)

            assert "trw_session_start" in result, f"Missing trw_session_start for {model_family}"
            assert "trw_deliver" in result, f"Missing trw_deliver for {model_family}"

    @pytest.mark.parametrize(
        ("model_family", "expected_checkpoint_support"),
        [
            ("qwen", True),
            ("gpt", True),
            ("claude", True),
            ("generic", True),
        ],
    )
    def test_checkpoint_reference_toggles_per_family(
        self, model_family: str, expected_checkpoint_support: bool
    ) -> None:
        """trw_checkpoint reference is present inqwen/gpt/claude but not generic."""
        result = render_opencode_instructions(model_family)

        has_checkpoint = "trw_checkpoint" in result
        assert has_checkpoint == expected_checkpoint_support, (
            f"Expected checkpoint {'present' if expected_checkpoint_support else 'absent'} "
            f"for {model_family}, but was {'present' if has_checkpoint else 'absent'}"
        )

    def test_qwen_specific_notes_contains_qwen_content(self) -> None:
        """Qwen-specific instructions contain Qwen-relevant guidance."""
        result = render_opencode_instructions("qwen")

        assert "Qwen" in result
        assert "vLLM" in result or "vllm" in result

    def test_gpt_specific_notes_contains_gpt_content(self) -> None:
        """GPT-specific instructions contain GPT-relevant guidance."""
        result = render_opencode_instructions("gpt")

        assert "GPT" in result
        assert "chain" in result.lower() or "reasoning" in result.lower()

    def test_claude_specific_notes_contains_claude_content(self) -> None:
        """Claude-specific instructions contain Claude-relevant guidance."""
        result = render_opencode_instructions("claude")

        assert "Claude" in result
        assert "extended thinking" in result.lower() or "XML" in result


# ── Generate Instructions Tests ───────────────────────────────────────────


@pytest.mark.unit
class TestGenerateOpencodeInstructions:
    """Tests for generate_opencode_instructions()."""

    def test_creates_instructions_file(self, tmp_path: Path) -> None:
        """generate_opencode_instructions() creates .opencode/INSTRUCTIONS.md."""
        result = generate_opencode_instructions(tmp_path, "qwen")

        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        assert instructions_path.exists()
        assert result["created"] or result["updated"]
        assert str(instructions_path.relative_to(tmp_path)) in (result["created"] + result["updated"])

    def test_preserves_existing_unmodified_content(self, tmp_path: Path) -> None:
        """If file exists with same content, returns preserved."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        instructions_path.write_text(render_opencode_instructions("qwen"), encoding="utf-8")

        result = generate_opencode_instructions(tmp_path, "qwen")

        assert result["preserved"]
        assert not result["created"]
        assert not result["updated"]

    def test_overwrites_modified_content_with_force(self, tmp_path: Path) -> None:
        """With force=True, overwrites existing file even if modified."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        instructions_path.write_text("old content", encoding="utf-8")

        result = generate_opencode_instructions(tmp_path, "qwen", force=True)

        assert result["updated"] or result["created"]
        assert "old content" not in instructions_path.read_text(encoding="utf-8")

    def test_returns_errors_on_directory_creation_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError during directory creation is captured in errors."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"

        def mock_mkdir(*args: object, **kwargs: object) -> None:
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "mkdir", mock_mkdir)

        result = generate_opencode_instructions(tmp_path, "qwen")

        assert result["errors"]
        assert any("Permission denied" in err for err in result["errors"])

    def test_returns_errors_on_write_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during file write is captured in errors."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)

        def mock_write(*args: object, **kwargs: object) -> None:
            raise OSError("Disk full")

        monkeypatch.setattr(Path, "write_text", mock_write)

        result = generate_opencode_instructions(tmp_path, "qwen")

        assert result["errors"]
        assert any("Disk full" in err for err in result["errors"])


@pytest.mark.unit
class TestGenerateCodexInstructions:
    """Tests for generate_codex_instructions()."""

    def test_creates_instructions_file(self, tmp_path: Path) -> None:
        """generate_codex_instructions() creates .codex/INSTRUCTIONS.md."""
        result = generate_codex_instructions(tmp_path)

        instructions_path = tmp_path / ".codex" / "INSTRUCTIONS.md"
        assert instructions_path.exists()
        assert result["created"] or result["updated"]
        assert str(instructions_path.relative_to(tmp_path)) in (result["created"] + result["updated"])

    def test_preserves_existing_unmodified_content(self, tmp_path: Path) -> None:
        """If file exists with same content, returns preserved."""
        instructions_path = tmp_path / ".codex" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        instructions_path.write_text(render_codex_instructions(), encoding="utf-8")

        result = generate_codex_instructions(tmp_path)

        assert result["preserved"]
        assert not result["created"]
        assert not result["updated"]

    def test_overwrites_modified_content_with_force(self, tmp_path: Path) -> None:
        """With force=True, overwrites existing file."""
        instructions_path = tmp_path / ".codex" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        instructions_path.write_text("old content", encoding="utf-8")

        result = generate_codex_instructions(tmp_path, force=True)

        assert result["updated"] or result["created"]
        assert "old content" not in instructions_path.read_text(encoding="utf-8")


# ── Model Family Detection Tests ──────────────────────────────────────────


@pytest.mark.unit
class TestDetectModelFamily:
    """Tests for detect_model_family()."""

    @pytest.mark.parametrize(
        ("model_name", "expected_family"),
        [
            ("qwen", "qwen"),
            ("Qwen2.5-Coder", "qwen"),
            ("Qwen3-Coder-Next", "qwen"),
            ("gpt-4o", "gpt"),
            ("GPT-5.4", "gpt"),
            ("claude-3-5-sonnet", "claude"),
            ("claude-3-7-sonnet", "claude"),
            ("some-other-model", "generic"),
            ("", "generic"),
            ("unknown", "generic"),
        ],
    )
    def test_correct_model_family_detection(self, model_name: str, expected_family: str) -> None:
        """Model names are correctly mapped to families."""
        opencode_json = {"model": model_name}
        result = detect_model_family(opencode_json)
        assert result == expected_family

    def test_empty_model_returns_generic(self) -> None:
        """Empty model field defaults to generic."""
        opencode_json: dict[str, str] = {}
        result = detect_model_family(opencode_json)
        assert result == "generic"

    def test_case_insensitive_matching(self) -> None:
        """Model detection is case-insensitive."""
        for model in ["QWEN", "QWEN2", "GPT-4", "CLAUDE-3"]:
            opencode_json = {"model": model}
            result = detect_model_family(opencode_json)
            assert result != "generic", f"Case-insensitive detection failed for {model}"


# ── AGENTS.md Migration Tests ─────────────────────────────────────────────


@pytest.mark.unit
class TestMigrateTrwContentFromAgentsMd:
    """Tests for _migrate_trw_content_from_agents_md()."""

    def test_returns_false_when_agents_md_not_exists(self, tmp_path: Path) -> None:
        """No AGENTS.md → migration does not occur."""
        from trw_mcp.models.config import TRWConfig

        migrated, path = _migrate_trw_content_from_agents_md(tmp_path, TRWConfig())

        assert migrated is False
        assert path == ""

    def test_returns_false_when_no_trw_markers(self, tmp_path: Path) -> None:
        """AGENTS.md without TRW markers → no migration."""
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# My Project\n\nNo TRW content here.\n", encoding="utf-8")

        migrated, path = _migrate_trw_content_from_agents_md(tmp_path, TRWConfig())

        assert migrated is False
        assert path == ""

    def test_strips_empty_trw_markers_from_agents_md(self, tmp_path: Path) -> None:
        """Empty TRW markers are stripped from AGENTS.md and migrated=True is returned."""
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text(
            f"# My Project\n\n{TRW_MARKER_START}\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        migrated, path = _migrate_trw_content_from_agents_md(tmp_path, TRWConfig())

        # Empty markers are still cleaned up — markers removed, user content preserved.
        assert migrated is True
        assert path == ""
        content = agents_path.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in content
        assert TRW_MARKER_END not in content
        assert "# My Project" in content

    def test_migrates_trw_content_to_opencode_instructions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TRW section in AGENTS.md migrates to .opencode/INSTRUCTIONS.md."""
        agents_path = tmp_path / "AGENTS.md"
        trw_content = f"{TRW_MARKER_START}\n## TRW Content\n\nContent here.\n{TRW_MARKER_END}"
        agents_path.write_text(f"# My Project\n\n{trw_content}", encoding="utf-8")

        # Mock detect_ide to return opencode
        def mock_detect_ide(path: Path) -> list[str]:
            return ["opencode"]

        monkeypatch.setattr("trw_mcp.bootstrap._utils.detect_ide", mock_detect_ide)

        migrated, path = _migrate_trw_content_from_agents_md(tmp_path, TRWConfig())

        assert migrated is True
        assert path != ""

        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        assert instructions_path.exists()

    def test_migrates_trw_content_to_codex_instructions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW section in AGENTS.md migrates to .codex/INSTRUCTIONS.md when codex detected."""
        agents_path = tmp_path / "AGENTS.md"
        trw_content = f"{TRW_MARKER_START}\n## TRW Content\n\nContent here.\n{TRW_MARKER_END}"
        agents_path.write_text(f"# My Project\n\n{trw_content}", encoding="utf-8")

        def mock_detect_ide(path: Path) -> list[str]:
            return ["codex"]

        monkeypatch.setattr("trw_mcp.bootstrap._utils.detect_ide", mock_detect_ide)

        migrated, path = _migrate_trw_content_from_agents_md(tmp_path, TRWConfig())

        assert migrated is True
        assert path != ""

        instructions_path = tmp_path / ".codex" / "INSTRUCTIONS.md"
        assert instructions_path.exists()


@pytest.mark.unit
class TestWriteTargetsInstructionPath:
    """Tests for WriteTargets.instruction_path field extension."""

    def test_instruction_path_default_empty(self) -> None:
        """instruction_path defaults to empty string."""
        from trw_mcp.models.config import WriteTargets

        targets = WriteTargets()
        assert targets.instruction_path == ""

    def test_instruction_path_can_be_set(self) -> None:
        """instruction_path can be configured."""
        from trw_mcp.models.config import WriteTargets

        targets = WriteTargets(instruction_path=".opencode/INSTRUCTIONS.md")
        assert targets.instruction_path == ".opencode/INSTRUCTIONS.md"

    def test_instruction_path_frozen(self) -> None:
        """WriteTargets is frozen, instruction_path cannot be mutated."""
        from trw_mcp.models.config import WriteTargets

        targets = WriteTargets()
        with pytest.raises(Exception):
            targets.instruction_path = "new-value"  # type: ignore[misc]
