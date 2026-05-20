from __future__ import annotations

from pathlib import Path

from trw_mcp.tools.skill_discovery import discover_meta_skills


def _write_skill(root: Path, slug: str, frontmatter: str, body: str = "Skill body.") -> Path:
    skill_dir = root / slug
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return skill_path


def test_discovery_ranks_eligible_skills_with_reasons_and_risk_warnings(tmp_path: Path) -> None:
    review = _write_skill(
        tmp_path,
        "review",
        """name: review
description: Review changed Python code
risk_level: high
requires_verification: true
argument_hint: file path
""",
    )
    docs = _write_skill(
        tmp_path,
        "docs",
        """name: docs
description: Update documentation
risk_level: low
""",
    )

    result = discover_meta_skills([docs, review], query="python code review")

    assert result.executed is False
    assert [candidate.name for candidate in result.candidates] == ["review", "docs"]
    assert result.candidates[0].path == str(review)
    assert any("query matched" in reason for reason in result.candidates[0].reasons)
    assert "risk level high" in result.candidates[0].risk_warnings
    assert "requires verification" in result.candidates[0].risk_warnings
    assert result.candidates[0].argument_hint == "file path"


def test_discovery_excludes_private_and_meta_disabled_skills_by_default(tmp_path: Path) -> None:
    public = _write_skill(
        tmp_path,
        "public",
        """name: public
description: Public skill
user_invocable: true
meta_discovery: true
""",
    )
    _write_skill(
        tmp_path,
        "private",
        """name: private
description: Private helper
user_invocable: false
meta_discovery: true
""",
    )
    _write_skill(
        tmp_path,
        "hidden",
        """name: hidden
description: Hidden helper
user_invocable: true
meta_discovery: false
""",
    )

    result = discover_meta_skills([public, tmp_path / "private" / "SKILL.md", tmp_path / "hidden" / "SKILL.md"], query="helper")

    assert result.executed is False
    assert [candidate.name for candidate in result.candidates] == ["public"]


def test_discovery_is_read_only_and_reports_invalid_manifests(tmp_path: Path) -> None:
    invalid = _write_skill(
        tmp_path,
        "invalid",
        """name: invalid
description: Invalid skill
unknown: nope
""",
        body="This body mentions executing, but discovery must not execute it.",
    )

    result = discover_meta_skills([invalid], query="invalid", mode="strict")

    assert result.executed is False
    assert result.candidates == ()
    assert result.warnings[0].path == str(invalid)
    assert result.warnings[0].field == "unknown"
