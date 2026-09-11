"""Security-audit semantics must not drift by client projection."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
GENERIC = PACKAGE_ROOT / "src/trw_mcp/data/skills/trw-security-check/SKILL.md"
CLIENT_VARIANTS = (
    PACKAGE_ROOT / "src/trw_mcp/data/copilot/skills/trw-security-check/SKILL.md",
    PACKAGE_ROOT / "src/trw_mcp/data/copilot/plugin/skills/trw-security-check/SKILL.md",
    pytest.param(ROOT / ".cursor/skills/trw-security-check/SKILL.md", marks=requires_monorepo),
    pytest.param(ROOT / ".github/skills/trw-security-check/SKILL.md", marks=requires_monorepo),
)


@pytest.mark.parametrize("path", CLIENT_VARIANTS)
def test_security_skill_client_variants_match_generic_contract(path: Path) -> None:
    expected = GENERIC.read_bytes()
    assert path.read_bytes() == expected, path
