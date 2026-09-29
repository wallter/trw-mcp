"""``init-project`` and ``instructions sync`` write the same ``.trw/INSTRUCTIONS.md`` for every client.

Found live on a canary: a fresh project's first ``instructions sync`` rewrote the file because
the cursor-cli installer rendered its own body (a private heading plus the light section) while sync
rendered the full one. Every writer now calls ``render_instructions_body``. The sync here is the real
one (``execute_claude_md_sync`` under the project's own config, as ``trw-mcp instructions sync`` runs it).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.models.config import _reset_config, get_config
from trw_mcp.state.claude_md import execute_claude_md_sync
from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH
from trw_mcp.state.persistence import FileStateReader

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]


def _sync_like_the_cli(project: Path) -> None:
    cwd = os.getcwd()
    os.chdir(project)
    _reset_config()
    try:
        execute_claude_md_sync("root", None, get_config(), FileStateReader(), None, "auto")  # type: ignore[arg-type]
    finally:
        os.chdir(cwd)
        _reset_config()


# ``None`` is a plain ``trw-mcp init-project``: it runs every detected installer in turn, and the LAST one
# to write the shared file used to leave a body that sync then replaced (the canary finding). opencode and
# codex own dedicated carriers and write no shared file, so they are not listed on their own.
@pytest.mark.parametrize("ide", [None, "claude-code", "cursor-cli", "grok"])
def test_init_then_sync_leaves_the_instructions_file_byte_identical(tmp_path: Path, ide: str | None) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    init_project(tmp_path, ide=ide)
    after_init = (tmp_path / INSTRUCTIONS_RELPATH).read_bytes()

    _sync_like_the_cli(tmp_path)

    assert (tmp_path / INSTRUCTIONS_RELPATH).read_bytes() == after_init
