"""Repository-neutral security-skill contracts across packaged projections."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATHS = (
    ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills" / "trw-security-check" / "SKILL.md",
    ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "codex" / "skills" / "trw-security-check" / "SKILL.md",
    ROOT / ".claude" / "skills" / "trw-security-check" / "SKILL.md",
    ROOT / ".agents" / "skills" / "trw-security-check" / "SKILL.md",
)


def test_security_variants_are_repository_neutral_and_resolution_aware() -> None:
    for path in PATHS:
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
