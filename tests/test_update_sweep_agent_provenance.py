"""update-project's stale-agent sweep advises rm only for an agent with provenance (review round 3 P1)."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    assert not init_project(root, ide="claude-code")["errors"]
    return root


def test_a_user_authored_trw_agent_is_left_alone_with_no_rm_advice(tmp_path: Path) -> None:
    root = _project(tmp_path)
    mine = root / ".claude" / "agents" / "trw-custom.md"
    mine.write_text("# my agent\n", encoding="utf-8")

    result = update_project(root, ide="claude-code")

    assert mine.read_text(encoding="utf-8") == "# my agent\n"
    advice = [w for w in result["warnings"] if "trw-custom" in w]
    assert not [w for w in advice if "rm " in w], advice
    assert not [e for e in result["errors"] if "trw-custom" in e]


def test_the_cli_output_for_a_user_authored_agent_has_no_rm_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._subcommands import _run_update_project

    root = _project(tmp_path)
    (root / ".claude" / "agents" / "trw-custom.md").write_text("# my agent\n", encoding="utf-8")
    args = argparse.Namespace(
        target_dir=str(root), pip_install=False, dry_run=False, ide="claude-code", log_json=False, debug=False,
        verbose=0, quiet=False, reprovision=None,
    )  # fmt: skip

    with pytest.raises(SystemExit):
        _run_update_project(args)

    out = capsys.readouterr().out
    assert not [line for line in out.splitlines() if "trw-custom" in line and "rm " in line], out


def test_a_withdrawn_agent_the_manifest_never_recorded_is_still_swept_by_name(tmp_path: Path) -> None:
    """A truly retired name (TRW's own list) keeps its existing handling: this is not a blanket skip."""
    root = _project(tmp_path)
    old = root / ".claude" / "agents" / "trw-tester.md"
    old.write_text("# old tester\n", encoding="utf-8")

    result = update_project(root, ide="claude-code")

    assert any("trw-tester" in str(p) for p in (*result.get("retired", []), *result["warnings"], *result["preserved"]))
