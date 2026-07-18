"""Security-audit semantics must not drift by client projection."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERIC = ROOT / "trw-mcp/src/trw_mcp/data/skills/trw-security-check/SKILL.md"
CLIENT_VARIANTS = (
    ROOT / "trw-mcp/src/trw_mcp/data/copilot/skills/trw-security-check/SKILL.md",
    ROOT / "trw-mcp/src/trw_mcp/data/copilot/plugin/skills/trw-security-check/SKILL.md",
    ROOT / ".cursor/skills/trw-security-check/SKILL.md",
    ROOT / ".github/skills/trw-security-check/SKILL.md",
)


def test_security_skill_client_variants_match_generic_contract() -> None:
    expected = GENERIC.read_bytes()
    for path in CLIENT_VARIANTS:
        assert path.read_bytes() == expected, path
