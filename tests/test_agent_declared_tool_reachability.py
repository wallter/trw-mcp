"""Every packaged or generated agent must only advertise callable tools/CLIs."""

from __future__ import annotations

import re

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT
from trw_mcp.channels.claude_code._explorer_subagent import get_explorer_agent_content

ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
AGENTS = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "agents"


def _frontmatter(content: str) -> tuple[str, str]:
    parts = content.split("---", 2)
    assert len(parts) == 3 and not parts[0].strip(), "agent definition needs YAML frontmatter"
    return parts[1], parts[2]


def _tool_list(frontmatter: str, key: str) -> set[str]:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*\n((?:[ \t]+- [^\n]+\n?)*)", frontmatter)
    if match is None:
        return set()
    return {item.strip() for item in re.findall(r"(?m)^\s+-\s+([^\n]+)", match.group(1))}


def test_agent_text_only_names_granted_tools_and_runnable_clis() -> None:
    """Catch a future definition that tells an agent to call a denied tool/CLI."""
    definitions = [(path.name, path.read_text(encoding="utf-8")) for path in sorted(AGENTS.glob("*.md"))]
    definitions.append(("generated trw-distill-explorer", get_explorer_agent_content()))

    for name, content in definitions:
        frontmatter, body = _frontmatter(content)
        grants = _tool_list(frontmatter, "tools")
        disallowed = _tool_list(frontmatter, "disallowedTools")

        mentioned_mcp_tools = {f"mcp__trw__{tool}" for tool in re.findall(r"\{tool:(trw_[a-z0-9_]+)\}", body)}
        mentioned_mcp_tools.update(re.findall(r"mcp__trw__trw_[a-z0-9_]+", body))
        for tool in mentioned_mcp_tools:
            assert tool in grants and tool not in disallowed, f"{name} names unavailable tool {tool}"

        mentioned_clis = set(re.findall(r"\b(trw-(?:distill|mcp))\s+[a-z][a-z0-9-]*", body))
        if mentioned_clis:
            assert "Bash" in grants and "Bash" not in disallowed, f"{name} names CLI commands but does not grant Bash"
