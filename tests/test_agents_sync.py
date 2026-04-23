"""Agent-directory parity test (PRD-QUAL-073 FR13, Route B).

``trw-mcp/src/trw_mcp/data/agents/`` is the source of truth. Bundled files
carry ``{tool:trw_X}`` placeholders so they render correctly across client
profiles; ``.claude/agents/`` is the dev-repo-local expansion (bare tool
names, matching what ``trw_mcp.prompts.messaging._expand_tool_placeholders``
would produce with ``profile=None``).

This test asserts every ``.claude/agents/*.md`` equals the marker-expansion
of its bundled counterpart, byte for byte (SHA-256).

Regenerate drift via ``scripts/sync-agents.py``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DIR = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents"
CLAUDE_DIR = REPO_ROOT / ".claude" / "agents"

_TOOL_MARKER_RE = re.compile(r"\{tool:(trw_\w+)\}")


def _expand_markers(text: str) -> str:
    """Mirror of ``_expand_tool_placeholders(..., profile=None)`` behaviour."""
    return _TOOL_MARKER_RE.sub(lambda m: m.group(1), text)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _agent_names() -> list[str]:
    return sorted(p.name for p in BUNDLED_DIR.glob("*.md"))


_AGENT_PARAMS = [pytest.param(name, id=name) for name in _agent_names()]


@pytest.mark.parametrize("agent_name", _AGENT_PARAMS)
def test_parity_after_marker_expansion(agent_name: str) -> None:
    """``.claude/agents/X`` equals marker-expansion of the bundled copy."""
    src = BUNDLED_DIR / agent_name
    dst = CLAUDE_DIR / agent_name
    assert src.is_file(), f"bundled source missing: {src}"
    assert dst.is_file(), f".claude/agents copy missing: {dst} (run scripts/sync-agents.py)"

    expected = _expand_markers(src.read_text(encoding="utf-8")).encode("utf-8")
    actual = dst.read_bytes()
    assert _sha256(actual) == _sha256(expected), (
        f"{agent_name}: .claude/agents/ drifts from bundled source after marker "
        "expansion. Run scripts/sync-agents.py to regenerate."
    )


def test_counts_match() -> None:
    """The two dirs have the same set of agent filenames."""
    bundled = {p.name for p in BUNDLED_DIR.glob("*.md")}
    claude = {p.name for p in CLAUDE_DIR.glob("*.md")}
    assert bundled == claude, (
        f"filename set drift:\n"
        f"  bundled-only: {sorted(bundled - claude)}\n"
        f"  claude-only:  {sorted(claude - bundled)}"
    )
