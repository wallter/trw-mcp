"""Cross-client parity for the /trw-feedback skill (PRD-CORE-182 / PRD-INFRA-132 FR05).

The feedback channel must be reachable from every client that ships the guided
slash-command skill. The generic ``.claude/skills/`` path is covered by
``test_bootstrap_feedback_install.py``; this module locks in parity for the
curated **codex**, **copilot**, **opencode**, and **copilot-plugin** bundled
skill subsets, which previously omitted ``trw-feedback`` even though their
injected instructions reference ``/trw-feedback`` and the canonical
``trw_submit_feedback`` MCP tool.

It also asserts that EVERY bundled ``trw-feedback`` copy is model-invocable
(no ``disable-model-invocation: true`` flag) — the field-bug fix that lets
agents and sub-agents submit feedback without a human present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._codex import _CODEX_SKILLS_DIR, install_codex_skills
from trw_mcp.bootstrap._copilot import _COPILOT_SKILLS_DIR, install_copilot_skills
from trw_mcp.bootstrap._opencode import install_opencode_skills
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.models.skill_manifest import validate_skill_markdown

from ._copilot_test_support import fake_git_repo  # noqa: F401

# Dest path the OpenCode installer writes the curated skill subset to.
_OPENCODE_DEST_SKILLS = (".opencode", "skills")

# Bundled source dirs (one per client variant) that must each ship a valid,
# model-invocable trw-feedback skill.
_BUNDLED_FEEDBACK_SOURCES: tuple[Path, ...] = (
    _DATA_DIR / "skills" / "trw-feedback" / "SKILL.md",
    _DATA_DIR / "codex" / "skills" / "trw-feedback" / "SKILL.md",
    _DATA_DIR / "copilot" / "skills" / "trw-feedback" / "SKILL.md",
    _DATA_DIR / "opencode" / "skills" / "trw-feedback" / "SKILL.md",
    _DATA_DIR / "copilot" / "plugin" / "skills" / "trw-feedback" / "SKILL.md",
)


def _assert_valid_feedback_skill(skill_md: Path) -> None:
    """The installed SKILL.md exists, passes the manifest contract, wires the
    canonical MCP tool, and is model-invocable (behavior, not mere existence)."""
    assert skill_md.exists(), f"trw-feedback SKILL.md not installed at {skill_md}"
    content = skill_md.read_text(encoding="utf-8")
    result = validate_skill_markdown(content, path=skill_md, mode="compat")
    assert result.ok, f"installed SKILL.md failed validation: {[e.reason for e in result.errors]}"
    assert result.manifest is not None
    assert result.manifest.name == "trw-feedback"
    # The skill is only useful if it actually drives the canonical tool.
    assert "trw_submit_feedback" in content, "feedback skill must reference the trw_submit_feedback tool"
    # The whole point of the field-bug fix: agents/sub-agents MAY invoke it.
    assert "disable-model-invocation: true" not in content, (
        f"trw-feedback at {skill_md} must be model-invocable — "
        "found disable-model-invocation: true in frontmatter"
    )


class TestFeedbackSkillClientParity:
    """FR05 parity: every curated client subset bundles /trw-feedback too."""

    def test_codex_installs_feedback_skill(self, tmp_path: Path) -> None:
        result = install_codex_skills(tmp_path)
        assert not result.get("errors")
        _assert_valid_feedback_skill(tmp_path / _CODEX_SKILLS_DIR / "trw-feedback" / "SKILL.md")

    def test_copilot_installs_feedback_skill(self, fake_git_repo: Path) -> None:
        result = install_copilot_skills(fake_git_repo)
        assert not result["errors"]
        _assert_valid_feedback_skill(fake_git_repo / _COPILOT_SKILLS_DIR / "trw-feedback" / "SKILL.md")

    def test_opencode_installs_feedback_skill(self, tmp_path: Path) -> None:
        """OpenCode's inventory-driven installer now ships trw-feedback into
        ``.opencode/skills`` (the channel was previously unreachable there)."""
        result = install_opencode_skills(tmp_path)
        assert not result["errors"]
        _assert_valid_feedback_skill(tmp_path.joinpath(*_OPENCODE_DEST_SKILLS, "trw-feedback", "SKILL.md"))

    def test_copilot_plugin_bundles_feedback_skill(self) -> None:
        """The Copilot plugin is packaged verbatim (its own plugin.json, no
        bootstrap install fn), so assert the bundled source ships a valid,
        model-invocable trw-feedback skill."""
        _assert_valid_feedback_skill(
            _DATA_DIR / "copilot" / "plugin" / "skills" / "trw-feedback" / "SKILL.md"
        )


class TestFeedbackSkillModelInvocable:
    """The field bug was a disabled flag — lock model-invocability for every
    bundled copy, not just the freshly installed ones."""

    @pytest.mark.parametrize("source", _BUNDLED_FEEDBACK_SOURCES, ids=lambda p: str(p.parent.parent.name))
    def test_bundled_source_is_model_invocable(self, source: Path) -> None:
        assert source.exists(), f"bundled trw-feedback SKILL.md missing at {source}"
        _assert_valid_feedback_skill(source)
