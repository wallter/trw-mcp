"""Agent frontmatter must not pin an output-token cap.

``max_tokens`` is a Claude **Messages API** request parameter. It is not part of
the sub-agent frontmatter schema (``name``, ``description``, ``tools``,
``disallowedTools``, ``model``, ``permissionMode``, ``maxTurns``, ``skills``,
``mcpServers``, ``hooks``, ``memory``, ``background``, ``effort``, ``isolation``,
``color``, ``initialPrompt``), so a value declared there is inert: it does not
raise the ceiling, does not lower it, and cannot silently truncate output either.

The original policy (FR07) enforced a rounding/headroom rule on that inert field.
Rounding an ignored number to the nearest 500 protects nothing, so the guard is
now the only rule that carries a real consequence: the key must stay absent, so
nobody re-adds it believing it caps anything. Output length is governed by the
model, and turn count by ``maxTurns`` — which *is* in the schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
MIRROR_DIRS: tuple[Path, ...] = (PACKAGE_ROOT / "src/trw_mcp/data/agents",)
if (REPO_ROOT / "release-packages.yaml").is_file():
    MIRROR_DIRS = (*MIRROR_DIRS, REPO_ROOT / ".claude/agents")
for directory in MIRROR_DIRS:
    assert list(directory.glob("*.md")), f"no agents found in required surface {directory}"


def _parse_frontmatter(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    body = text[3:].lstrip("\n")
    end = body.find("\n---")
    if end == -1:
        return None
    try:
        parsed = yaml.safe_load(body[:end])
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _agent_files() -> list[Path]:
    return [path for mirror in MIRROR_DIRS for path in sorted(mirror.glob("*.md"))]


def test_agent_files_discovered() -> None:
    """Guard against a silently empty scan if a mirror moves."""
    assert _agent_files(), f"no agent files found under {[str(p) for p in MIRROR_DIRS]}"


#: PRD-CORE-291-FR05: the bundled agent set after the traceability-checker ->
#: auditor, tester -> implementer, and requirement-writer -> prd-groomer
#: merges. Exactly these 8 names, never a superset or subset.
_EXPECTED_BUNDLED_AGENT_NAMES = frozenset(
    {
        "trw-adversarial-auditor",
        "trw-auditor",
        "trw-implementer",
        "trw-lead",
        "trw-prd-groomer",
        "trw-requirement-reviewer",
        "trw-researcher",
        "trw-reviewer",
    }
)

_REMOVED_AGENT_NAMES = ("trw-traceability-checker", "trw-tester", "trw-requirement-writer")

_CANONICAL_AGENTS_DIR = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "agents"


def test_bundled_agent_set_is_exactly_eight_post_merge() -> None:
    """PRD-CORE-291-FR05: 3 agents merged away, exactly 8 remain."""
    stems = {path.stem for path in _CANONICAL_AGENTS_DIR.glob("*.md")}
    assert stems == _EXPECTED_BUNDLED_AGENT_NAMES, (
        f"expected exactly {sorted(_EXPECTED_BUNDLED_AGENT_NAMES)} under {_CANONICAL_AGENTS_DIR}, found {sorted(stems)}"
    )
    for removed in _REMOVED_AGENT_NAMES:
        assert removed not in stems, f"{removed} was merged away by PRD-CORE-291-FR05 and must not reappear"


@pytest.mark.parametrize(
    "path",
    sorted([*_CANONICAL_AGENTS_DIR.glob("*.md"), *(_CANONICAL_AGENTS_DIR.parent / "skills").rglob("*.md")]),
    ids=lambda p: f"{p.parent.name}/{p.stem}",
)
def test_no_bundled_agent_invokes_a_removed_agent_name(path: Path) -> None:
    """PRD-CORE-291-FR05: no bundled agent or skill may name a merged-away role as a live invocation target."""
    text = path.read_text(encoding="utf-8")
    for removed in _REMOVED_AGENT_NAMES:
        assert removed not in text, f"{path}: still invokes removed agent name {removed!r}"


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_no_agent_pins_max_tokens(path: Path) -> None:
    fm = _parse_frontmatter(path)
    assert fm is not None, f"could not parse frontmatter: {path}"
    assert "max_tokens" not in fm, (
        f"{path}: `max_tokens` is a Messages API parameter, not a sub-agent frontmatter "
        f"field — it is ignored here. Use `maxTurns` to bound the agent's work."
    )
