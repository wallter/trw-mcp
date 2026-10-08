"""Report sub_eJ3qWhIOKC4P0cpE: npx skills add's Grok link must survive update."""

import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._template_claude_md import _recorded_targets


@pytest.mark.usefixtures("no_memory_daemon")
def test_recorded_grok_with_npx_user_skill_link_updates(tmp_path: Path) -> None:
    root = tmp_path / "project"
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    installed = init_project(root, ide="grok")
    assert not installed["errors"], installed["errors"]
    assert _recorded_targets(root) == ["grok"]
    skill = root / ".agents/skills/foo/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# User skill\nKeep my bytes.\n", encoding="utf-8")
    link = root / ".grok/skills/foo"
    link.parent.mkdir(parents=True)
    link.symlink_to("../../.agents/skills/foo")

    result = update_project(root)

    assert not result["errors"], result["errors"]
    assert link.is_symlink()
    assert link.readlink() == Path("../../.agents/skills/foo")
    assert skill.read_bytes() == b"# User skill\nKeep my bytes.\n"
