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


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_no_agent_pins_max_tokens(path: Path) -> None:
    fm = _parse_frontmatter(path)
    assert fm is not None, f"could not parse frontmatter: {path}"
    assert "max_tokens" not in fm, (
        f"{path}: `max_tokens` is a Messages API parameter, not a sub-agent frontmatter "
        f"field — it is ignored here. Use `maxTurns` to bound the agent's work."
    )
