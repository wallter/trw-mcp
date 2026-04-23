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
    assert effort in _VALID_EFFORTS, (
        f"{agent_path.name}: effort={effort!r} not in {sorted(_VALID_EFFORTS)}"
    )


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
    assert isinstance(desc, str) and desc.strip(), (
        f"{agent_path.name}: missing or empty ``description`` field"
    )
    assert "use when" in desc.lower(), (
        f"{agent_path.name}: description lacks ``Use when...`` trigger\n"
        f"description: {desc!r}"
    )


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_name_matches_filename(agent_path: Path) -> None:
    """FR11: ``name`` frontmatter equals file stem."""
    fm = _parse_frontmatter(agent_path)
    name = fm.get("name")
    assert name == agent_path.stem, (
        f"{agent_path.name}: name={name!r} does not match filename stem {agent_path.stem!r}"
    )


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_no_prescriptive_line_starts(agent_path: Path) -> None:
    """FR09, FR11: zero ``^(MUST|CRITICAL|RIGID):`` in the body."""
    body = _agent_body(agent_path)
    matches = _PRESCRIPTIVE_LINE_START_RE.findall(body)
    assert not matches, (
        f"{agent_path.name}: prescriptive line-starts remain {matches!r}; "
        "soften per OPUS-4-7-BEST-PRACTICES.md §4"
    )


def test_auditors_reference_shared_doc() -> None:
    """FR06: both auditor files cite ``audit-framework.md`` in the first 60 lines."""
    for name in ("trw-auditor.md", "trw-adversarial-auditor.md"):
        path = AGENTS_DIR / name
        assert path.exists(), f"missing {name}"
        first_60 = "\n".join(path.read_text(encoding="utf-8").splitlines()[:60])
        assert "audit-framework.md" in first_60, (
            f"{name}: does not reference audit-framework.md in first 60 lines"
        )


def test_agents_dir_has_expected_count() -> None:
    """Sanity: 12 agents are present so parametrize didn't silently collapse."""
    assert len(_agent_files()) == 12, (
        f"expected 12 agents in {AGENTS_DIR}, found {len(_agent_files())}"
    )
