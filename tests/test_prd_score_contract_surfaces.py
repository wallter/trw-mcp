"""Semantic contracts for packaged PRD-readiness guidance."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._layout import requires_monorepo

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
REPO_ROOT = DATA.parents[3]

READINESS_OWNERS = (
    DATA / "skills/trw-prd-ready/SKILL.md",
    DATA / "skills/trw-prd-groom/SKILL.md",
    DATA / "skills/trw-exec-plan/SKILL.md",
    DATA / "agents/trw-prd-groomer.md",
    DATA / "agents/trw-lead.md",
)

READINESS_ADAPTERS = (DATA / "opencode/commands/trw-prd-ready.md",)

AUDIT_VARIANTS = (DATA / "skills/trw-audit/SKILL.md",)

RETIRED_PRD_NEW_BUNDLE_SURFACES = (
    DATA / "skills/trw-prd-new/SKILL.md",
    DATA / "copilot/plugin/skills/trw-prd-new/SKILL.md",
)

RETIRED_PRD_NEW_REPO_MIRRORS = (
    REPO_ROOT / ".agents/skills/trw-prd-new/SKILL.md",
    REPO_ROOT / ".claude/skills/trw-prd-new/SKILL.md",
    REPO_ROOT / ".github/skills/trw-prd-new/SKILL.md",
)

CURSOR_COMMAND = DATA / "cursor_ide/commands/trw-prd-ready.md"

FORBIDDEN_NUMERIC_GATE = re.compile(
    r"\b0\.85\b|"
    r"(?:total_score|score)\s*(?:>=|<=|<|>)\s*(?:85|65|45)|"
    r"(?:completeness_score|completeness)\s*(?:>=|<=|<|>)",
    re.IGNORECASE,
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing packaged readiness surface: {path}"
    return path.read_text(encoding="utf-8")


def test_prd_surfaces_do_not_hardcode_deprecated_readiness_gates() -> None:
    """No packaged consumer may substitute a fixed score for the risk-scaled result."""
    for path in (*READINESS_OWNERS, *READINESS_ADAPTERS, *AUDIT_VARIANTS, CURSOR_COMMAND):
        match = FORBIDDEN_NUMERIC_GATE.search(_read(path))
        assert match is None, f"{path} contains deprecated readiness gate: {match.group(0) if match else ''}"


def test_readiness_owners_use_the_full_risk_scaled_result() -> None:
    """Gate owners retain full validation and distinguish diagnostic tier from legacy approval."""
    required = ("validation_partial", "valid", "quality_tier", "approved", "total_score")
    for path in READINESS_OWNERS:
        content = _read(path)
        for field in required:
            assert field in content, f"{path} omits readiness field {field}"


def test_audit_records_weak_spec_quality_without_score_aborting() -> None:
    """Adversarial audit remains usable when the specification itself is weak."""
    for path in AUDIT_VARIANTS:
        content = _read(path)
        for field in ("validation_partial", "valid", "quality_tier", "total_score"):
            assert field in content, f"{path} omits diagnostic field {field}"
        assert "Do not abort an adversarial audit solely" in content


def test_retired_prd_new_alias_is_not_shipped_or_installed() -> None:
    """The one PRD workflow is trw-prd-ready; no compatibility skill remains."""
    for path in RETIRED_PRD_NEW_BUNDLE_SURFACES:
        assert not path.exists(), f"retired compatibility skill still exists: {path}"

    from trw_mcp.bootstrap._client_skills import skill_names

    for client in ("codex", "copilot", "opencode"):
        assert "trw-prd-new" not in skill_names(client)


@requires_monorepo
@pytest.mark.parametrize("path", RETIRED_PRD_NEW_REPO_MIRRORS, ids=lambda path: path.parts[-3])
def test_retired_prd_new_alias_is_absent_from_repo_client_mirrors(path: Path) -> None:
    """The unbundled client mirrors must not retain an alias-only entry point."""
    assert not path.exists(), f"retired compatibility skill still exists: {path}"


def test_client_mirrors_preserve_semantics_and_lifecycle_vocabulary() -> None:
    """Cursor does not invent a READY status of its own.

    The copilot direct/plugin lead-mirror equality this also asserted is gone
    with the mirrors: ``data/copilot/agents`` and ``data/copilot/plugin/agents``
    were shipped bytes no installer read — ``generate_copilot_agents`` writes
    from the inline ``_COPILOT_AGENT_TEMPLATES`` dict.
    """
    cursor = _read(CURSOR_COMMAND)
    assert "Sets status to READY" not in cursor
    assert "lifecycle status" in cursor


def test_opencode_adapters_forward_to_installed_gate_owner() -> None:
    """Static delegation guards; installer resolution is tested in bootstrap tests.

    OpenCode no longer forks trw-prd-ready/SKILL.md (PRD-CORE-291-FR04) -- it
    renders the one canonical body (frontmatter-only reduction via
    ``render_skill_md``), so the comparison basis here is that rendering, not
    a deleted static file. The canonical body legitimately uses ``### Phase N``
    headers (every client shares the same multi-phase pipeline now), so the
    old "no Phase headers" guard -- a property of the deleted flat-numbered
    fork -- no longer applies to ``skill``; it still holds for the opencode
    *command* file, which never had phase headers of its own.
    """
    from trw_mcp.bootstrap._client_skills import render_skill_md

    (command_path,) = READINESS_ADAPTERS
    command = _read(command_path)
    canonical = _read(DATA / "skills/trw-prd-ready/SKILL.md")
    skill = render_skill_md(canonical, "opencode")
    for phase in ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        assert f"{phase}-contract.md" in skill
    # The fork's exact phrasing ("not authorize author self-review") is gone;
    # the canonical body carries the same concept in different words.
    assert "no inline author self-review fallback" in skill
    assert "stop at the current gate" in skill
    assert ".opencode/skills/trw-prd-ready/SKILL.md" in command
    assert "original `$ARGUMENTS`" in command
    for content in (skill, command):
        assert "--embedded-plan" in content
    assert "## Phase" not in command
