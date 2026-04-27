"""Frontmatter hygiene tests for TRW agents (PRD-QUAL-073 FR11).

Enforces three properties on every ``.claude/agents/*.md`` file:

1. ``effort`` is present and is one of {``low``, ``medium``, ``high``}.
2. ``description`` is a non-empty string containing ``"use when"``
   (case-insensitive).
3. ``name`` equals the file stem.

Also enforces (FR09): no line-start ``^(MUST|CRITICAL|RIGID):`` imperatives
survive in any agent body.

And (FR06): both auditor agent files reference ``audit-framework.md`` in
their first 60 lines.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

_VALID_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high"})
_PRESCRIPTIVE_LINE_START_RE = re.compile(r"^(MUST|CRITICAL|RIGID):", re.MULTILINE)

# FR09 scoping: MUST / CRITICAL / RIGID are blanket-banned at line start.
# NEVER / ALWAYS at line start are intentionally NOT in this regex — the PRD
# acceptance criterion permits them when individually justifiable as safety
# rules (e.g., "NEVER modify code files" in read-only auditors). Automated
# blanket-ban would produce false positives on legitimate safety invariants.
# Surviving ^NEVER / ^ALWAYS line starts are tracked by
# test_count_never_always_survivors below — the count is logged for reviewer
# awareness but does not fail CI.
_NEVER_ALWAYS_LINE_START_RE = re.compile(r"^(NEVER|ALWAYS)\b", re.MULTILINE)


def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


def _parse_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        pytest.fail(f"{path.name}: missing YAML frontmatter (no leading '---')")
    body = text[3:].lstrip("\n")
    end = body.find("\n---")
    if end == -1:
        pytest.fail(f"{path.name}: frontmatter has no closing '---' fence")
    parsed = yaml.safe_load(body[:end])
    if not isinstance(parsed, dict):
        pytest.fail(f"{path.name}: frontmatter is not a YAML mapping")
    return parsed


def _agent_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text
    body = text[3:].lstrip("\n")
    end = body.find("\n---")
    if end == -1:
        return body
    return body[end + 4 :]


# Parametrize over actual agent files so failures name the offender.
_AGENT_PARAMS = [pytest.param(p, id=p.name) for p in _agent_files()]


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_effort_present(agent_path: Path) -> None:
    """FR01-03, FR11: every agent carries ``effort: low|medium|high``."""
    fm = _parse_frontmatter(agent_path)
    effort = fm.get("effort")
    assert effort is not None, f"{agent_path.name}: missing ``effort`` frontmatter field"
    assert effort in _VALID_EFFORTS, f"{agent_path.name}: effort={effort!r} not in {sorted(_VALID_EFFORTS)}"


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_effort_enum_valid(agent_path: Path) -> None:
    """FR11: redundant enum guard for clear failure messages."""
    fm = _parse_frontmatter(agent_path)
    effort = fm.get("effort", "")
    assert isinstance(effort, str), f"{agent_path.name}: effort must be a string"
    assert effort.lower() in _VALID_EFFORTS, (
        f"{agent_path.name}: effort={effort!r} invalid; use one of {sorted(_VALID_EFFORTS)}"
    )


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_description_use_when(agent_path: Path) -> None:
    """FR04-05, FR11: every description contains ``"use when"`` (case-insensitive)."""
    fm = _parse_frontmatter(agent_path)
    desc = fm.get("description", "")
    assert isinstance(desc, str) and desc.strip(), f"{agent_path.name}: missing or empty ``description`` field"
    assert "use when" in desc.lower(), (
        f"{agent_path.name}: description lacks ``Use when...`` trigger\ndescription: {desc!r}"
    )


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_name_matches_filename(agent_path: Path) -> None:
    """FR11: ``name`` frontmatter equals file stem."""
    fm = _parse_frontmatter(agent_path)
    name = fm.get("name")
    assert name == agent_path.stem, f"{agent_path.name}: name={name!r} does not match filename stem {agent_path.stem!r}"


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_no_prescriptive_line_starts(agent_path: Path) -> None:
    """FR09, FR11: zero ``^(MUST|CRITICAL|RIGID):`` in the body."""
    body = _agent_body(agent_path)
    matches = _PRESCRIPTIVE_LINE_START_RE.findall(body)
    assert not matches, (
        f"{agent_path.name}: prescriptive line-starts remain {matches!r}; soften per OPUS-4-7-BEST-PRACTICES.md §4"
    )


def test_never_always_survivors_budget() -> None:
    """FR09 companion: ^NEVER / ^ALWAYS count must not grow unbounded.

    The PRD permits these when individually justifiable as safety rules
    (e.g., ``NEVER modify code files`` in read-only auditors). This test
    enforces a soft ceiling on the total count across all agent files so
    that future additions get scrutinized. If the count legitimately needs
    to grow, update ``_NEVER_ALWAYS_CEILING`` and document the new survivors
    in the commit message so reviewers can judge each addition on merit.
    """
    total = 0
    per_file: dict[str, int] = {}
    for agent_path in _agent_files():
        body = _agent_body(agent_path)
        matches = _NEVER_ALWAYS_LINE_START_RE.findall(body)
        if matches:
            per_file[agent_path.name] = len(matches)
            total += len(matches)

    # Initial ceiling captured 2026-04-24 during sprint-100 post-audit fix.
    # Current survivors: 0 (all "NEVER modify..." statements appear as bullet
    # items, not line-starts). Ceiling is generous headroom — any future
    # unbulleted ^NEVER/^ALWAYS line-start additions must be individually
    # justifiable as safety rules per PRD-QUAL-073 FR09, and raising this
    # cap requires explicit reviewer sign-off.
    _NEVER_ALWAYS_CEILING = 10
    assert total <= _NEVER_ALWAYS_CEILING, (
        f"{total} ^NEVER/^ALWAYS line starts across agents exceed ceiling "
        f"{_NEVER_ALWAYS_CEILING}. Per-file counts: {per_file!r}. Each new "
        "occurrence must be individually justifiable as a safety rule; raise "
        "the ceiling only with explicit reviewer sign-off."
    )


def test_auditors_reference_shared_doc() -> None:
    """FR06: both auditor files cite ``audit-framework.md`` in the first 30 body lines.

    PRD FR06 acceptance: the ``Read docs/documentation/audit-framework.md`` directive
    must land in the first 30 body lines (i.e. post-frontmatter). We measure by
    extracting the body via ``_agent_body`` and checking the first 30 lines of it —
    not the first N lines of the raw file, which would conflate frontmatter length
    with body position and let the reference silently drift.
    """
    for name in ("trw-auditor.md", "trw-adversarial-auditor.md"):
        path = AGENTS_DIR / name
        assert path.exists(), f"missing {name}"
        body = _agent_body(path)
        first_30_body = "\n".join(body.splitlines()[:30])
        assert "audit-framework.md" in first_30_body, (
            f"{name}: does not reference audit-framework.md in first 30 body lines (per PRD-QUAL-073 FR06 acceptance)"
        )


def test_agents_dir_has_expected_count() -> None:
    """Sanity: 12 agents are present so parametrize didn't silently collapse."""
    assert len(_agent_files()) == 12, f"expected 12 agents in {AGENTS_DIR}, found {len(_agent_files())}"
