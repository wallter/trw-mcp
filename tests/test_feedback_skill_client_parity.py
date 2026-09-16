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


def _bundled_skill_roots() -> list[Path]:
    """Every bundled ``skills/`` root that ships more than one skill — DERIVED.

    This list used to be a hardcoded tuple, and that is exactly how cursor-ide
    kept its gap: it curates its own skill list, ships fourteen skills, and
    omitted ``trw-feedback`` while the injected instruction told its agents to
    "surface the /trw-feedback skill". A hardcoded enumeration cannot notice a
    client it was never told about, so the check passed while the surface it
    describes was incomplete.

    The "more than one skill" filter keeps this from tripping over an empty or
    single-purpose directory; a real client bundle always carries a set.
    """
    roots = [d for d in _DATA_DIR.rglob("skills") if d.is_dir()]
    return sorted(d for d in roots if len([x for x in d.iterdir() if x.is_dir()]) > 1)


# Bundled source dirs that must each ship a valid, model-invocable trw-feedback
# skill. Derived from the tree so a NEW client bundle is covered on the day it
# lands rather than the day someone remembers to edit this file.
_BUNDLED_FEEDBACK_SOURCES: tuple[Path, ...] = tuple(
    root / "trw-feedback" / "SKILL.md" for root in _bundled_skill_roots()
)


def test_the_derived_source_list_is_not_empty_and_covers_the_known_clients() -> None:
    """Non-vacuity partner for the derivation above.

    A glob that matched nothing would make every parametrized case below vanish
    silently and the suite would still report green -- the same shape of failure
    the derivation exists to prevent. Pin the floor and the clients we know of.
    """
    roots = _bundled_skill_roots()
    assert len(roots) >= 5, f"derived only {len(roots)} bundled skill root(s): {roots}"
    found = {str(r.relative_to(_DATA_DIR)) for r in roots}
    for expected in ("skills", "codex/skills", "copilot/skills", "opencode/skills"):
        assert expected in found, f"{expected} missing from derived roots: {sorted(found)}"


def test_cursor_ide_curates_the_feedback_skill() -> None:
    """cursor-ide filters the shared bundle through its own curated list.

    It therefore has a skill mechanism AND uses it -- fourteen other skills ship
    -- so omitting this one was a gap, not a design decision. Asserted against
    the real generator, not the list, so a rename of either side is caught.
    """
    from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS, cursor_ide_skill_contents

    assert "trw-feedback" in _IDE_CURATED_SKILLS, "cursor-ide ships other skills but not the reporting channel itself"
    generated = cursor_ide_skill_contents()
    paths = [k for k in generated if "trw-feedback" in k]
    assert paths, f"cursor-ide generated no trw-feedback skill: {sorted(generated)}"
    body = generated[paths[0]].decode("utf-8")
    assert "trw_submit_feedback" in body, "the skill must drive the canonical tool"
    assert "disable-model-invocation: true" not in body, "must stay model-invocable"


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
        f"trw-feedback at {skill_md} must be model-invocable — found disable-model-invocation: true in frontmatter"
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
        _assert_valid_feedback_skill(_DATA_DIR / "copilot" / "plugin" / "skills" / "trw-feedback" / "SKILL.md")


class TestFeedbackSkillModelInvocable:
    """The field bug was a disabled flag — lock model-invocability for every
    bundled copy, not just the freshly installed ones."""

    @pytest.mark.parametrize("source", _BUNDLED_FEEDBACK_SOURCES, ids=lambda p: str(p.parent.parent.name))
    def test_bundled_source_is_model_invocable(self, source: Path) -> None:
        assert source.exists(), f"bundled trw-feedback SKILL.md missing at {source}"
        _assert_valid_feedback_skill(source)
