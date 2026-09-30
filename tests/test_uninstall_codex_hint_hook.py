"""INC-012 follow-up (swarm-e2e re-verification): no codex hook survives uninstall calling a deleted script.

With the CC-03 pre-edit hint on, ``init --ide codex`` adds a ``PreToolUse`` group (matcher ``apply_patch``)
to ``.codex/hooks.json`` that runs ``.claude/hooks/pre-tool-distill-hint.sh``. Uninstall deleted the script,
kept the group (so codex failed every patch), and still printed "Cleaned ... (removed TRW entries)". Now the
group goes too; a TRW hook the user edited is kept by design, but named on stdout and the run exits 1.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

_HINT = "pre-tool-distill-hint.sh"


def _ns(project: Path) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide=None
    )


def _codex_project_with_hint(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    assert not init_project(tmp_path, ide="codex")["errors"]
    hooks = tmp_path / ".codex" / "hooks.json"
    assert _HINT in hooks.read_text(encoding="utf-8"), "precondition: the CC-03 hint group is registered"
    return tmp_path


def test_uninstall_withdraws_the_codex_pre_edit_hint_group(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = _codex_project_with_hint(tmp_path)
    hooks = project / ".codex" / "hooks.json"

    _run_uninstall(_ns(project))

    if hooks.exists():
        assert _HINT not in hooks.read_text(encoding="utf-8"), hooks.read_text(encoding="utf-8")
    assert not (project / ".claude" / "hooks" / _HINT).exists()


def test_an_edited_trw_hook_that_stays_is_reported_and_fails_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The truthful "Cleaned" line: a TRW hook the user edited is kept, so the run says so and exits 1."""
    project = _codex_project_with_hint(tmp_path)
    hooks = project / ".codex" / "hooks.json"
    data = json.loads(hooks.read_text(encoding="utf-8"))
    for group in data["hooks"]["PreToolUse"]:
        for hook in group.get("hooks", []):
            if _HINT in str(hook.get("command", "")):
                hook["timeout"] = 99  # the user retimed TRW's hook: no longer what TRW generates
    hooks.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exited:
        _run_uninstall(_ns(project))

    assert exited.value.code == 1
    out = capsys.readouterr().out
    line = next((ln for ln in out.splitlines() if ".codex/hooks.json" in ln and "Kept" in ln), None)
    assert line is not None and "you edited" in line, out
    assert "(removed TRW entries)" not in "\n".join(ln for ln in out.splitlines() if ".codex/hooks.json" in ln)
