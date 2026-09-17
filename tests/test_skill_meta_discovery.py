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

    result = discover_meta_skills(
        [public, tmp_path / "private" / "SKILL.md", tmp_path / "hidden" / "SKILL.md"], query="helper"
    )

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


# ---------------------------------------------------------------------------
# PRD-FIX-141-FR10 — per-host frontmatter schema and stopword-free ranking
# ---------------------------------------------------------------------------


_CLAUDE_CODE_SKILL = """name: audit
description: Audit the repository
context: fresh
agent: trw-auditor
category: quality
disable-model-invocation: true
argument-hint:
"""


def test_strict_mode_accepts_the_active_hosts_own_frontmatter(tmp_path: Path) -> None:
    """Strict mode rejected 11 of 25 BUNDLED skills for keys the host defines."""
    skill = _write_skill(tmp_path, "audit", _CLAUDE_CODE_SKILL.rstrip("\n"))

    result = discover_meta_skills([skill], query="audit", mode="strict", host="claude-code")

    assert [issue.field for issue in result.warnings] == []
    assert [candidate.name for candidate in result.candidates] == ["audit"]


def test_a_blank_argument_hint_is_normalized_not_rejected(tmp_path: Path) -> None:
    """Compat mode always coerced it; strict rejecting the same file was a disagreement."""
    skill = _write_skill(tmp_path, "audit", _CLAUDE_CODE_SKILL.rstrip("\n"))

    result = discover_meta_skills([skill], query="audit", mode="strict", host="claude-code")

    assert result.candidates[0].argument_hint is None


def test_a_key_outside_every_host_schema_still_errors_in_strict_mode(tmp_path: Path) -> None:
    """Non-vacuity: the schema was widened per host, not switched off.

    A typo in a TRW field must still be caught — that is what strict mode is
    for, and accepting every unknown key would have silently made it a no-op.
    """
    skill = _write_skill(tmp_path, "typo", "name: typo\ndescription: A skill\nrisk_levl: high")

    result = discover_meta_skills([skill], query="typo", mode="strict", host="claude-code")

    assert "risk_levl" in {issue.field for issue in result.warnings}
    assert result.candidates == ()


def test_an_unknown_host_accepts_no_extra_keys(tmp_path: Path) -> None:
    """A host with no declared schema keeps the pre-FR10 behaviour exactly."""
    skill = _write_skill(tmp_path, "audit", _CLAUDE_CODE_SKILL.rstrip("\n"))

    result = discover_meta_skills([skill], query="audit", mode="strict", host="some-other-host")

    assert "context" in {issue.field for issue in result.warnings}


def test_a_foreign_host_key_is_a_warning_in_compat_mode(tmp_path: Path) -> None:
    """Compat mode's severity is unchanged for keys no host schema covers."""
    skill = _write_skill(tmp_path, "typo", "name: typo\ndescription: A skill\nrisk_levl: high")

    result = discover_meta_skills([skill], query="typo", mode="compat", host="claude-code")

    assert {issue.severity for issue in result.warnings} == {"warning"}
    assert [candidate.name for candidate in result.candidates] == ["typo"]


def test_stopwords_neither_score_nor_explain(tmp_path: Path) -> None:
    """``query matched: and, with`` was reported as a RANKING REASON."""
    skill = _write_skill(
        tmp_path,
        "docs",
        "name: docs\ndescription: Update the documentation and publish it with care",
    )

    result = discover_meta_skills([skill], query="and with the a of", mode="compat")

    assert result.candidates[0].score == 0.0
    assert result.candidates[0].reasons == ()


def test_a_real_term_beside_stopwords_still_scores(tmp_path: Path) -> None:
    """Non-vacuity: stopword removal must not mute the meaningful tokens."""
    skill = _write_skill(
        tmp_path,
        "docs",
        "name: docs\ndescription: Update the documentation and publish it with care",
    )

    result = discover_meta_skills([skill], query="update the documentation", mode="compat")

    assert result.candidates[0].score >= 2.0
    assert "documentation" in result.candidates[0].reasons[0]
    assert " the" not in result.candidates[0].reasons[0]


def test_every_bundled_skill_validates_clean_under_strict_mode() -> None:
    """The bundle is the population the defect was measured on (learning L-ODuU)."""
    from trw_mcp.models.skill_manifest import validate_skill_markdown

    bundled = Path(__import__("trw_mcp").__file__).parent / "data" / "skills"
    offenders = {
        path.parent.name
        for path in sorted(bundled.glob("*/SKILL.md"))
        if validate_skill_markdown(
            path.read_text(encoding="utf-8"), path=path, mode="strict", host="claude-code"
        ).errors
    }

    assert offenders == set()
