"""The opencode carrier steers file reads away from bash (DoD-5 OPENCODE-BASH-ASK-ENDS-RUN).

The installed ``opencode.json`` keeps ``bash: ask`` (it is opencode's only external guard
against destructive shell commands). A headless ``opencode run`` rejects that prompt and
ends the run, so an agent that followed the framework pointer with ``bash cat`` got no
answer. The carrier now says to read files with the read tool. These tests render a real
opencode install into ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path


def _install(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="opencode")
    assert not result["errors"], result["errors"]


def test_carrier_tells_the_agent_to_read_with_the_read_tool(tmp_path: Path) -> None:
    _install(tmp_path)

    carrier = (tmp_path / ".opencode" / "INSTRUCTIONS.md").read_text(encoding="utf-8")

    assert "bash: ask" in carrier
    assert "read tool" in carrier
    assert "read, grep and glob tools" in carrier
    assert "not `cat`/`ls`/`rg` in bash" in carrier


def test_the_shell_ask_guard_itself_is_unchanged(tmp_path: Path) -> None:
    _install(tmp_path)

    permission = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))["permission"]

    assert permission["bash"] == "ask"
