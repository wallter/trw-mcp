"""Bundled skills are installed verbatim into user projects — keep them portable.

``bootstrap/_init_project_skills.py::_install_skills`` copies every directory
under ``data/skills/`` into the target repository's ``.claude/skills/`` byte for
byte. There is no templating step and no per-project rewrite, so any command a
SKILL.md spells out is a command TRW hands to a user working in some other
repository, in some other language.

``trw-code-search/SKILL.md`` shipped this repo's own dev invocations —
``../.venv/bin/python -m pytest tests/test_code_chunking.py`` and two siblings —
under a "Verification" heading. A user running TRW on a Go or TypeScript project
was told to run trw-mcp's Python test suite against their own tree. It is a
correctness failure, not a cosmetic one: the guidance is unrunnable there, and
following it produces a verification claim about files the user does not have.

The guard is on the *class*, not on the one file: any bundled skill that names a
repo-local interpreter path, a trw-mcp source/test path, or a hardcoded
``pytest``/``mypy``/``ruff`` target trips it. Describe what to verify; let the
target project's own manifest supply the command.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"

#: Every bundled tree whose SKILL.md files are copied into a user project.
#: ``data/skills`` is the Claude Code / generic set; the rest are per-client
#: mirrors installed by their own bootstrap modules.
_SKILL_ROOTS: tuple[str, ...] = (
    "skills",
    "codex/skills",
    "cursor/skills",
    "opencode/skills",
)

#: Patterns that can only be true of *this* repository. Each is paired with the
#: reason it cannot survive a copy into an arbitrary user project.
_NON_PORTABLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\.venv/bin/"),
        "a repo-local interpreter path — the user's project has no such venv",
    ),
    (
        re.compile(r"\bsrc/trw_mcp/"),
        "a trw-mcp source path — not present in the user's repository",
    ),
    (
        re.compile(r"\btests/test_\w+\.py\b"),
        "a trw-mcp test file — the user's tests are elsewhere, in another language",
    ),
    (
        re.compile(r"\bpytest\s+tests/"),
        "a hardcoded pytest invocation — the project's own runner may not be pytest",
    ),
    (
        re.compile(r"\bmypy\s+--strict\s+src/"),
        "a hardcoded mypy target — the project may not be Python at all",
    ),
)


def _bundled_skill_files() -> list[Path]:
    found: list[Path] = []
    for root in _SKILL_ROOTS:
        base = _DATA_DIR / root
        if not base.is_dir():
            continue
        found.extend(sorted(base.glob("*/SKILL.md")))
    return found


@pytest.mark.unit
def test_skill_corpus_is_non_empty() -> None:
    """Non-vacuity floor: the guard below must have something to scan."""
    skills = _bundled_skill_files()
    assert len(skills) >= 25, (
        f"bundled skill corpus not found (saw {len(skills)}) — the portability guard would be vacuous"
    )


@pytest.mark.unit
def test_no_bundled_skill_ships_repo_local_commands() -> None:
    """No bundled skill may hand a user this repository's own dev commands."""
    offenders: dict[str, list[str]] = {}
    for skill_md in _bundled_skill_files():
        text = skill_md.read_text(encoding="utf-8")
        hits: list[str] = []
        for pattern, reason in _NON_PORTABLE_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append(f"{reason}: {match.group(0)!r}")
        if hits:
            offenders[f"{skill_md.parent.parent.name}/{skill_md.parent.name}"] = hits
    assert not offenders, (
        "bundled SKILL.md files ship commands that only work inside the trw-framework monorepo; "
        f"they are copied verbatim into every user's .claude/skills/: {offenders}"
    )


@pytest.mark.unit
def test_code_search_skill_still_tells_the_agent_what_to_verify() -> None:
    """Making the skill portable must not gut it.

    The fix is "describe what to run, not this repo's exact invocation" — so the
    Verification section has to survive with actionable content, otherwise the
    cheapest way to pass the guard above is to delete the guidance.
    """
    text = (_DATA_DIR / "skills" / "trw-code-search" / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("## Verification", 1)
    assert len(section) == 2, "trw-code-search lost its Verification section"
    body = section[1]
    assert len(body.split()) >= 60, "Verification section was gutted rather than made portable"
    # It must still point at the three behaviors the skill's own safety contract
    # promises, and at the project's own toolchain as the source of commands.
    # This used to assert `dependency_missing` — the structured failure the
    # semantic-mode fallback returned. 2.0.0 removed the mode (UF-031: the branch
    # could not return a result), so that literal now pins a behaviour the tool
    # does not have. The replacement asserts a check the agent can still RUN.
    assert "trw_code_index_update" in body, "the re-index check was dropped"
    for manifest in ("Makefile", "pyproject.toml", "package.json", "go.mod", "Cargo.toml"):
        assert manifest in body, f"the project-neutral command source no longer mentions {manifest}"
