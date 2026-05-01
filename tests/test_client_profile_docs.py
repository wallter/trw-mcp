"""Documentation parity tests for client profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, resolve_client_profile
from trw_mcp.models.config._client_profile import CeremonyWeights, WriteTargets

_CLIENT_PROFILES_DOC = Path(__file__).resolve().parents[2] / "docs" / "CLIENT-PROFILES.md"


def _read_client_profiles_doc() -> str:
    return _CLIENT_PROFILES_DOC.read_text(encoding="utf-8")


def _extract_section(doc_text: str, section_heading: str) -> str:
    section_start = doc_text.index(section_heading)
    next_section = doc_text.find("\n## ", section_start + 1)
    return doc_text[section_start:] if next_section == -1 else doc_text[section_start:next_section]


def _extract_markdown_table_rows(doc_text: str, heading: str) -> list[list[str]]:
    lines = doc_text.splitlines()
    start = lines.index(heading)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("|"):
            table_start = idx
            break
    else:  # pragma: no cover
        raise AssertionError(f"No markdown table found after {heading!r}")

    rows: list[list[str]] = []
    for line in lines[table_start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _extract_profile_config_table(doc_text: str, section_heading: str) -> dict[str, str]:
    section = _extract_section(doc_text, section_heading)
    return {row[0]: row[1] for row in _extract_markdown_table_rows(section, "### Profile Configuration")}


def _format_context_window_tokens(tokens: int) -> str:
    return f"{tokens // 1_000}K" if tokens % 1_000 == 0 else str(tokens)


def _format_ceremony_weights(weights: CeremonyWeights) -> str:
    return "/".join(
        str(value)
        for value in (
            weights.session_start,
            weights.deliver,
            weights.checkpoint,
            weights.learn,
            weights.build_check,
            weights.review,
        )
    )


@pytest.mark.unit
def test_codex_profile_contract_is_explicit() -> None:
    """Codex exposes only the light-profile contract declared in _profiles.py."""
    profile = resolve_client_profile("codex")
    light_profile = resolve_client_profile("opencode")

    assert profile.display_name == "Codex CLI"
    assert profile.ceremony_mode == "light"
    assert profile.write_targets.agents_md is True
    assert profile.write_targets.instruction_path == ".codex/INSTRUCTIONS.md"
    assert profile.context_window_tokens == 32_000
    assert profile.instruction_max_lines == 200
    assert profile.default_model_tier == "local-small"
    assert _format_ceremony_weights(profile.ceremony_weights) == "30/30/5/20/15/0"
    assert profile.scoring_weights == light_profile.scoring_weights
    assert profile.mandatory_phases == ["implement", "deliver"]
    assert profile.hooks_enabled is False
    assert profile.include_framework_ref is False
    assert not hasattr(profile, "include_agent" + "_teams")
    assert profile.include_delegation is False
    assert profile.skills_enabled is False
    assert profile.mcp_instructions_enabled is False
    assert profile.learning_recall_enabled is True
    assert profile.tool_exposure_mode == "standard"


@pytest.mark.unit
def test_codex_quick_reference_row_matches_profile_contract() -> None:
    """CLIENT-PROFILES quick reference stays aligned with the Codex profile."""
    profile = resolve_client_profile("codex")
    rows = _extract_markdown_table_rows(_read_client_profiles_doc(), "## Quick Reference")
    quick_ref = {row[0].strip("`"): row[1:] for row in rows}

    assert "codex" in quick_ref
    mode, context, ceremony, write_target, review_weight = quick_ref["codex"]
    assert mode == profile.ceremony_mode
    assert context == _format_context_window_tokens(profile.context_window_tokens)
    assert ceremony == _format_ceremony_weights(profile.ceremony_weights)
    assert write_target == "`AGENTS.md`"
    assert review_weight == str(profile.ceremony_weights.review)


@pytest.mark.unit
def test_opencode_quick_reference_row_matches_profile_contract() -> None:
    """CLIENT-PROFILES quick reference stays aligned with the OpenCode profile."""
    profile = resolve_client_profile("opencode")
    rows = _extract_markdown_table_rows(_read_client_profiles_doc(), "## Quick Reference")
    quick_ref = {row[0].strip("`"): row[1:] for row in rows}

    assert "opencode" in quick_ref
    mode, context, ceremony, write_target, review_weight = quick_ref["opencode"]
    assert mode == profile.ceremony_mode
    assert context == _format_context_window_tokens(profile.context_window_tokens)
    assert ceremony == _format_ceremony_weights(profile.ceremony_weights)
    assert write_target == "`AGENTS.md`"
    assert review_weight == str(profile.ceremony_weights.review)


@pytest.mark.unit
def test_opencode_docs_managed_artifacts_match_current_contract() -> None:
    """OpenCode support docs enumerate the managed artifacts and lifecycle guarantees."""
    opencode_section = _extract_section(_read_client_profiles_doc(), "## OpenCode Support Surface")
    for expected in [
        "- `AGENTS.md`",
        "- `.opencode/INSTRUCTIONS.md`",
        "- `.opencode/commands/trw-deliver.md`",
        "- `.opencode/agents/trw-implementer.md`",
        "- `.opencode/skills/trw-deliver/SKILL.md`",
    ]:
        assert expected in opencode_section

    assert "bootstrap and update flows manage" in opencode_section
    assert "User-created neighboring files" in opencode_section


@pytest.mark.unit
def test_codex_docs_profile_configuration_matches_profile_contract() -> None:
    """CLIENT-PROFILES Codex section documents the profile contract and runtime notes."""
    profile = resolve_client_profile("codex")
    doc_text = _read_client_profiles_doc()
    codex_section = _extract_section(doc_text, "## Codex Support Surface")
    config_rows = _extract_profile_config_table(doc_text, "## Codex Support Surface")

    assert config_rows == {
        "Mode": f"`{profile.ceremony_mode}`",
        "Context": _format_context_window_tokens(profile.context_window_tokens),
        "Ceremony weights": f"`{_format_ceremony_weights(profile.ceremony_weights)}`",
        "Write target": "`AGENTS.md`",
        "Instructions path": f"`{profile.write_targets.instruction_path}`",
        "Hooks": "Disabled",
        "Framework ref": "Disabled",
        "Delegation": "Disabled",
        "Skills": "Disabled",
        "Learning recall": "Enabled",
        "MCP instructions": "Disabled",
        "Tool exposure": f"`{profile.tool_exposure_mode}`",
    }
    assert "Agent teams" not in config_rows
    assert "current Codex runtime surfaces" in codex_section
    assert "shared `_light_profile(...)` contract" in codex_section
    assert (
        "The Codex profile models `.codex/INSTRUCTIONS.md` as its `instruction_path` "
        "while keeping `AGENTS.md` as the profile's top-level write target."
    ) in codex_section
    assert (
        "Hooks, framework reference content, delegation content, and skills are "
        "intentionally disabled in the profile contract."
    ) in codex_section
    assert (
        "`skills_enabled = false` is a profile-layer prompt/exposure setting; it does not suppress "
        "the installer-managed `.agents/skills/` helper directories that Codex may reference from "
        "`skills.config`."
    ) in codex_section
    assert "model_instructions_file" in codex_section
    assert ".codex/agents/*.toml" in codex_section
    assert "features.codex_hooks = true" in codex_section


@pytest.mark.integration
def test_codex_profile_capability_change_alters_write_target_behavior(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Changing a Codex profile capability changes the consumed write-target behavior."""
    from trw_mcp.models.config._profiles import _PROFILES
    from trw_mcp.state.claude_md._agents_md import _determine_write_targets

    codex_profile = resolve_client_profile("codex")
    overridden_profile = codex_profile.model_copy(
        update={
            "write_targets": WriteTargets(
                agents_md=False,
                instruction_path=".codex/ALT-INSTRUCTIONS.md",
            )
        }
    )
    monkeypatch.setitem(_PROFILES, "codex", overridden_profile)

    write_claude, write_agents, instruction_path = _determine_write_targets(
        "codex",
        TRWConfig(),
        tmp_path,
        "root",
    )

    assert write_claude is False
    assert write_agents is False
    assert instruction_path == ".codex/ALT-INSTRUCTIONS.md"
