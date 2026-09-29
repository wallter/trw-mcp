"""Tests for per-client instruction generation — PRD-CORE-115.

Tests for portable instruction generation, per-client instruction files
(.opencode/INSTRUCTIONS.md, .codex/INSTRUCTIONS.md), and AGENTS.md migration.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

# Public-mirror guard: this test asserts a MONOREPO invariant (repo-root
# scripts/ + docs/ layout) absent from the standalone trw-mcp PyPI/GitHub
# mirror. Skip cleanly there; the monorepo CI still enforces it.
from tests._layout import MONOREPO_ROOT

if MONOREPO_ROOT is None:
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

from trw_mcp.bootstrap._opencode import (
    generate_codex_instructions,
    generate_opencode_instructions,
)
from trw_mcp.state.claude_md._static_sections import (
    render_codex_instructions,
    render_opencode_instructions,
)
from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

# ── Render Tests ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRenderCodexInstructions:
    """Tests for render_codex_instructions()."""

    def test_render_codex_instructions_returns_markdown(self) -> None:
        """render_codex_instructions() returns valid markdown string."""
        result = render_codex_instructions()
        assert isinstance(result, str)
        assert result.startswith("# Codex TRW Instructions\n")
        assert "trw_session_start" in result
        assert "trw_deliver" in result

    def test_codex_instruction_file_carries_the_whole_protocol(self) -> None:
        """QUAL-113-FR03's 2,025-byte cap is retired, and the reason matters.

        The cap was a token budget whose stated premise was "Codex deltas stay
        small; AGENTS.md owns generic workflow". PRD-CORE-240-FR04 removes that
        premise: TRW no longer writes into codex's AGENTS.md, which the USER
        owns. A cap that forces content out of the only remaining carrier would
        push it nowhere, so the budget is replaced by a completeness assertion.

        Kept generous rather than removed: this file is loaded on every Codex
        turn, so unbounded growth is still a real cost.
        """
        result = render_codex_instructions()

        assert "trw_session_start" in result
        assert render_deliver_gate_statement().strip() in result
        assert "OpenAI developer docs MCP server" in result, "codex-specific framing must survive the merge"
        assert len(result.encode("utf-8")) <= 16_000, "carrier grew past its ceiling — re-measure before raising"

    def test_codex_only_project_has_no_second_surface_to_duplicate(self, tmp_path: Path) -> None:
        """QUAL-113-FR02 guarded duplication across codex's TWO surfaces. It has one.

        The old assertion compared the codex file against the repo's root
        AGENTS.md and allowed only the gate to appear in both. That was the right
        property while TRW wrote BOTH files for codex. Now it writes only
        `.codex/INSTRUCTIONS.md`, so the duplication it guarded cannot occur:
        there is no TRW-authored AGENTS.md in a codex project to duplicate.

        The honest residue, recorded rather than hidden: codex still READS a
        user's AGENTS.md as its project doc. In a MIXED project — say codex plus
        cursor-cli, where cursor-cli's carrier is AGENTS.md — a codex session
        loads both and does see the protocol twice. That is a token cost in a
        multi-client repo, accepted deliberately as the price of not injecting
        into a file the user owns.
        """
        import subprocess

        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="codex")

        agents = tmp_path / "AGENTS.md"
        if agents.is_file():
            assert "<!-- trw:start -->" not in agents.read_text(encoding="utf-8")
        assert (tmp_path / ".codex" / "INSTRUCTIONS.md").is_file()

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
        assert "stable in the locally verified cli" in docs_text.lower()
        assert "optional and trust-gated" in docs_text.lower()
        assert "AGENTS.md" in docs_text

        assert ".codex/INSTRUCTIONS.md" in result
        assert ".codex/agents/*.toml" in result
        assert "stable in current codex" in result.lower()
        assert "optional and trust-gated" in result.lower()
        assert "AGENTS.md" in result

    def test_codex_instructions_carry_delegation_protocol(self) -> None:
        """PRD-CORE-252 OQ-3 (resolved 2026-09-04): codex's rendered
        `.codex/INSTRUCTIONS.md` carries the delegation protocol block, since
        its measured carrier stack (largest agent + AGENTS.md + config.toml)
        fits comfortably within its 32K budget. Red if `include_delegation`
        is ever reverted to False for codex, or if the wiring into
        ``render_codex_instructions`` regresses.
        """
        result = render_codex_instructions()

        # PRD-CORE-301-FR13: the shared block points at the guide; FRAMEWORK.md serves it.
        from trw_mcp.state.claude_md.sections._delegation import DELEGATION_GUIDE_POINTER

        assert DELEGATION_GUIDE_POINTER in result

    def test_opencode_instructions_omit_delegation_protocol(self) -> None:
        """opencode shares `_light_profile(...)` with codex but was not
        re-measured for this change, so it must stay unaffected."""
        result = render_opencode_instructions()

        assert "## TRW Delegation & Orchestration (Auto-Generated)" not in result
        assert "DELEGATION AND FILE OWNERSHIP" not in result

    def test_codex_framing_avoids_stale_guidance(self) -> None:
        """Codex framing should stay portable and fail open on hooks."""
        result = render_codex_instructions()

        assert "200K" not in result
        assert "Read `.trw/frameworks/FRAMEWORK.md`" not in result
        assert ".codex/agents/*.toml" in result
        assert "optional and trust-gated" in result.lower()


@pytest.mark.unit
class TestRenderOpencodeInstructions:
    """Tests for render_opencode_instructions() (PRD-CORE-301-FR02: shared block plus opencode framing)."""

    def test_provider_specific_notes_are_not_core_workflow(self) -> None:
        """v25 portable instructions do not embed model-family prompt recipes."""
        result = render_opencode_instructions()

        assert "vLLM" not in result
        assert "chain-of-thought" not in result
        assert "extended thinking" not in result.lower()

    def test_shared_instruction_output_has_no_foreign_client_identity(self) -> None:
        """QUAL-113 FR04: shared lifecycle prose stays provider-neutral."""
        result = render_opencode_instructions().lower()

        assert "openai" not in result
        assert "anthropic" not in result
        assert "claude code" not in result
        assert "codex" not in result


# ── Generate Instructions Tests ───────────────────────────────────────────


@pytest.mark.unit
class TestGenerateOpencodeInstructions:
    """Tests for generate_opencode_instructions()."""

    def test_creates_instructions_file(self, tmp_path: Path) -> None:
        """generate_opencode_instructions() creates .opencode/INSTRUCTIONS.md."""
        result = generate_opencode_instructions(tmp_path)

        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        assert instructions_path.exists()
        assert result["created"] or result["updated"]
        assert str(instructions_path.relative_to(tmp_path)) in (result["created"] + result["updated"])

    def test_preserves_existing_unmodified_content(self, tmp_path: Path) -> None:
        """If file exists with same content, returns preserved."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        instructions_path.write_text(render_opencode_instructions(), encoding="utf-8")

        result = generate_opencode_instructions(tmp_path)

        assert result["preserved"]
        assert not result["created"]
        assert not result["updated"]

    def test_overwrites_modified_content_with_force(self, tmp_path: Path) -> None:
        """With force=True, overwrites existing file even if modified."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        instructions_path.write_text("old content", encoding="utf-8")

        result = generate_opencode_instructions(tmp_path, force=True)

        assert result["updated"] or result["created"]
        assert "old content" not in instructions_path.read_text(encoding="utf-8")

    def test_preserves_user_modified_content_with_manifest_hash(self, tmp_path: Path) -> None:
        """Manifest hash mismatch preserves user-edited OpenCode instructions."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        original = render_opencode_instructions()
        instructions_path.write_text("customized instructions", encoding="utf-8")

        result = generate_opencode_instructions(
            tmp_path,
            manifest_hashes={
                ".opencode/INSTRUCTIONS.md": hashlib.sha256(original.encode("utf-8")).hexdigest(),
            },
        )

        assert result["preserved"] == [".opencode/INSTRUCTIONS.md"]
        assert instructions_path.read_text(encoding="utf-8") == "customized instructions"

    def test_returns_errors_on_directory_creation_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError during directory creation is captured in errors."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"

        def mock_mkdir(*args: object, **kwargs: object) -> None:
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "mkdir", mock_mkdir)

        result = generate_opencode_instructions(tmp_path)

        assert result["errors"]
        assert any("Permission denied" in err for err in result["errors"])

    def test_returns_errors_on_write_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OSError during file write is captured in errors."""
        instructions_path = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)

        def mock_write(*args: object, **kwargs: object) -> None:
            raise OSError("Disk full")

        # PRD-FIX-123-FR06 moved this write onto the atomic
        # ``FileStateWriter.write_text`` path (temp file + rename), which does not
        # go through ``Path.write_text`` — patching only the latter would leave the
        # write succeeding and assert nothing.
        monkeypatch.setattr(Path, "write_text", mock_write)
        monkeypatch.setattr("trw_mcp.state.persistence.FileStateWriter.write_text", mock_write)

        result = generate_opencode_instructions(tmp_path)

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

    def test_preserves_user_modified_content_with_manifest_hash(self, tmp_path: Path) -> None:
        """Manifest hash mismatch preserves user-edited Codex instructions."""
        instructions_path = tmp_path / ".codex" / "INSTRUCTIONS.md"
        instructions_path.parent.mkdir(parents=True)
        original = render_codex_instructions()
        instructions_path.write_text("customized codex instructions", encoding="utf-8")

        result = generate_codex_instructions(
            tmp_path,
            manifest_hashes={
                ".codex/INSTRUCTIONS.md": hashlib.sha256(original.encode("utf-8")).hexdigest(),
            },
        )

        assert result["preserved"] == [".codex/INSTRUCTIONS.md"]
        assert instructions_path.read_text(encoding="utf-8") == "customized codex instructions"


# ── Model Family Detection Tests ──────────────────────────────────────────


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
            targets.instruction_path = "new-value"
