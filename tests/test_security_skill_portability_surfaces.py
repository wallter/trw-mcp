"""Repository-neutral security-skill contracts across packaged projections."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
_CANONICAL = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "skills" / "trw-security-check" / "SKILL.md"
PATHS = (
    _CANONICAL,
    pytest.param(ROOT / ".claude" / "skills" / "trw-security-check" / "SKILL.md", marks=requires_monorepo),
    pytest.param(ROOT / ".agents" / "skills" / "trw-security-check" / "SKILL.md", marks=requires_monorepo),
)


@pytest.mark.parametrize("path", PATHS)
def test_security_variants_are_repository_neutral_and_resolution_aware(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    for phrase in (
        "target repository",
        "trust boundaries",
        "lockfiles, resolver constraints",
        "security-relevant manifests and configuration",
        "conditional example",
    ):
        assert phrase in content, f"{path}: missing {phrase!r}"
    assert "audit of the TRW codebase" not in content
    assert "Flag any dependencies without version pins" not in content
    assert "not documentation or configs" not in content


def test_codex_rendering_is_repository_neutral_and_resolution_aware() -> None:
    """Codex no longer forks this skill; it renders the canonical body (PRD-CORE-291-FR04)."""
    from trw_mcp.bootstrap._client_skills import render_skill_md

    content = render_skill_md(_CANONICAL.read_text(encoding="utf-8"), "codex")
    for phrase in (
        "target repository",
        "trust boundaries",
        "lockfiles, resolver constraints",
        "security-relevant manifests and configuration",
        "conditional example",
    ):
        assert phrase in content, f"codex rendering: missing {phrase!r}"
    assert "audit of the TRW codebase" not in content
    assert "Flag any dependencies without version pins" not in content
    assert "not documentation or configs" not in content
