"""E2E-CODEX-INIT-ARTIFACTS item 3 (INC-016): a second ``init --ide`` records its client in target_platforms.

``init-project --ide claude-code`` then ``init-project --ide codex`` on one project left
``target_platforms: ["claude-code"]``: init writes ``.trw/config.yaml`` only when it is missing, so the second
client was never recorded and later updates did not maintain its files. Init now augments an existing
list the way ``update-project`` does -- append-only, never narrowing it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]


def _platforms(project: Path) -> list[str]:
    return list(yaml.safe_load((project / ".trw" / "config.yaml").read_text(encoding="utf-8"))["target_platforms"])


def test_a_second_init_adds_its_client_and_keeps_the_first(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    assert _platforms(tmp_path) == ["claude-code"]

    assert not init_project(tmp_path, ide="codex")["errors"]

    assert _platforms(tmp_path) == ["claude-code", "codex"]


def test_a_user_list_is_never_narrowed_by_a_second_init(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    config = tmp_path / ".trw" / "config.yaml"
    text = config.read_text(encoding="utf-8")
    assert '- "claude-code"' in text  # the list as init writes it
    config.write_text(text.replace('- "claude-code"', '- "claude-code"\n- "cursor-ide"'), encoding="utf-8")
    assert _platforms(tmp_path) == ["claude-code", "cursor-ide"]

    assert not init_project(tmp_path, ide="codex")["errors"]

    assert _platforms(tmp_path) == ["claude-code", "cursor-ide", "codex"]
