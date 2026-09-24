"""Regression coverage for PRD-QUAL-059 workflow guidance hardening."""

from __future__ import annotations

from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).parent
_REPO_ROOT = _TESTS_DIR.parent.parent

# Public-mirror guard: this test asserts a MONOREPO invariant (repo-root
# scripts/ + .claude/ layout) absent from the standalone trw-mcp PyPI/GitHub
# mirror. Skip cleanly there; the monorepo CI still enforces it.
if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

_PKG_DATA = _TESTS_DIR.parent / "src" / "trw_mcp" / "data"

#: codex/copilot/opencode no longer fork these skills on disk
#: (PRD-CORE-291-FR04) -- they render the canonical body -- so their entries
#: are (client, skill_name) pairs checked against the RENDERED text below,
#: instead of a path into a deleted fork.
_RENDERED_GUIDANCE_EXPECTATIONS: dict[tuple[str, str], list[str]] = {
    ("codex", "trw-prd-ready"): [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    ("opencode", "trw-prd-ready"): [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    ("codex", "trw-exec-plan"): [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    ("codex", "trw-audit"): ["implementation-readiness", "control points", "testability", "migration", "score-gaming"],
    ("copilot", "trw-audit"): [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
}

_GUIDANCE_EXPECTATIONS = {
    _PKG_DATA / "skills" / "trw-prd-ready" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "skills" / "trw-exec-plan" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "agents" / "trw-prd-groomer.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _PKG_DATA / "agents" / "trw-lead.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "skills" / "trw-prd-ready" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "skills" / "trw-exec-plan" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".github" / "skills" / "trw-audit" / "SKILL.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "agents" / "trw-prd-groomer.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
    _REPO_ROOT / ".claude" / "agents" / "trw-lead.md": [
        "implementation-readiness",
        "control points",
        "testability",
        "migration",
        "score-gaming",
    ],
}


@pytest.mark.unit
@pytest.mark.parametrize(("path", "required_snippets"), _GUIDANCE_EXPECTATIONS.items(), ids=lambda item: str(item))
def test_prd_workflow_guidance_mentions_hardened_readiness_semantics(
    path: Path,
    required_snippets: list[str],
) -> None:
    content = path.read_text(encoding="utf-8").lower()
    for snippet in required_snippets:
        assert snippet in content, f"{path} is missing '{snippet}'"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("client", "skill_name", "required_snippets"),
    [(c, n, s) for (c, n), s in _RENDERED_GUIDANCE_EXPECTATIONS.items()],
    ids=lambda v: str(v) if isinstance(v, str) else None,
)
def test_rendered_client_guidance_mentions_hardened_readiness_semantics(
    client: str,
    skill_name: str,
    required_snippets: list[str],
) -> None:
    from trw_mcp.bootstrap._client_skills import render_skill_md

    canonical = (_PKG_DATA / "skills" / skill_name / "SKILL.md").read_text(encoding="utf-8")
    content = render_skill_md(canonical, client).lower()
    for snippet in required_snippets:
        assert snippet in content, f"{client}/{skill_name} (rendered) is missing '{snippet}'"
