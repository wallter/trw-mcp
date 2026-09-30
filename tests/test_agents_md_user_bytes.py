"""The two AGENTS.md writers keep the user's bytes outside TRW's region, and agree, for CRLF files and trailing blank lines.

E2E-INC-051 follow-up: ``read_text`` translated CRLF to LF, the append paths ``rstrip``-ed the user's trailing blank lines,
and the LF block in a CRLF file made the marker-region writer (init/update) and the sync writer take turns rewriting.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_USER_CRLF = b"# Team rules\r\n\r\nAlways use tabs.\r\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=30)
    (root / ".trw").mkdir()
    (root / ".trw" / "config.yaml").write_text("target_platforms:\n- claude-code\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    return root


def _sync(root: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    execute_claude_md_sync("root", None, get_config(), FileStateReader(), None, "claude-code", force=True)


def _backups(root: Path) -> list[str]:
    return sorted(p.name for p in (root / ".trw" / "backups" / "instructions").glob("*"))


def test_a_crlf_file_stays_crlf_through_init_update_and_sync_and_reaches_a_fixed_point(project: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    agents = project / "AGENTS.md"
    agents.write_bytes(_USER_CRLF)

    assert not init_project(project, ide="claude-code").get("errors")
    first = agents.read_bytes()
    assert first.startswith(_USER_CRLF), "the user's CRLF lines were rewritten"
    assert b"\n" not in first.replace(b"\r\n", b""), "a bare LF entered a CRLF file"

    _sync(project)
    update_project(project)
    settled = agents.read_bytes()
    seen = _backups(project)
    for _ in range(2):
        _sync(project)
        update_project(project)

    assert agents.read_bytes() == settled, "a repeat sync/update changed the file"
    assert _backups(project) == seen, "a repeat sync/update wrote a backup"
    assert settled.startswith(_USER_CRLF) and b"\n" not in settled.replace(b"\r\n", b"")


def test_the_users_trailing_blank_lines_survive_a_markerless_append(project: Path) -> None:
    from trw_mcp.bootstrap import init_project

    user = b"# Mine\nkeep\n\n\n\n"
    (project / "AGENTS.md").write_bytes(user)

    init_project(project, ide="claude-code")
    after_init = (project / "AGENTS.md").read_bytes()
    _sync(project)

    assert after_init.startswith(user), "trailing blank lines were collapsed"
    assert (project / "AGENTS.md").read_bytes() == after_init


def test_text_after_the_block_and_its_line_endings_survive_a_sync_in_a_crlf_file(project: Path) -> None:
    from trw_mcp.bootstrap import init_project

    (project / "AGENTS.md").write_bytes(_USER_CRLF)
    init_project(project, ide="claude-code")
    agents = project / "AGENTS.md"
    agents.write_bytes(agents.read_bytes() + b"\r\n<!-- note -->\r\nlast line, no newline")
    before = agents.read_bytes()

    _sync(project)

    assert agents.read_bytes() == before
