"""INC-079: ``trw-mcp instructions check`` is a read-only drift check; the instructions family logs quietly."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._stdio_harness import pinned_server_env


def _args(**over: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "instructions_command": "check",
        "scope": "root",
        "target_dir": None,
        "client": "auto",
        "as_json": True,
    }
    base.update(over)
    return argparse.Namespace(**base)


@pytest.mark.parametrize(
    ("rendered", "expected_exit", "would_change"),
    [
        ({"status": "dry_run", "diffs": []}, 0, False),
        ({"status": "dry_run", "diffs": [{"file": "AGENTS.md", "diff": ""}]}, 0, False),
        ({"status": "dry_run", "diffs": [{"file": "AGENTS.md", "diff": "@@ -1 +1 @@\n-a\n+b\n"}]}, 1, True),
        ({"status": "dry_run", "diffs": [], "refusals": [{"file": "AGENTS.md", "reason": "content_loss"}]}, 1, True),
    ],
)
def test_check_exits_1_only_when_something_would_change(
    rendered: dict[str, Any],
    expected_exit: int,
    would_change: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from trw_mcp.tools import _instructions_cli

    calls: list[dict[str, bool]] = []

    def fake_render(_args: argparse.Namespace, *, dry_run: bool, force: bool) -> dict[str, Any]:
        calls.append({"dry_run": dry_run, "force": force})
        return dict(rendered)

    monkeypatch.setattr(_instructions_cli, "_render", fake_render)
    with pytest.raises(SystemExit) as exited:
        _instructions_cli.run_instructions(_args())
    assert exited.value.code == expected_exit
    assert calls == [{"dry_run": True, "force": False}]  # check can never write
    assert f'"would_change": {"true" if would_change else "false"}' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rendered", "expected_exit", "would_change"),
    [
        ({"status": "dry_run", "diffs": []}, 0, False),
        ({"status": "dry_run", "diffs": [{"file": "AGENTS.md", "diff": "@@ -1 +1 @@\n-a\n+b\n"}]}, 1, True),
        ({"status": "dry_run", "diffs": [], "refusals": [{"file": "AGENTS.md", "reason": "content_loss"}]}, 1, True),
    ],
)
def test_sync_dry_run_has_check_parity(
    rendered: dict[str, Any],
    expected_exit: int,
    would_change: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """INC-126 (b): sync --dry-run had no would_change and exited 0 when it would change files; check had both."""
    from trw_mcp.tools import _instructions_cli

    monkeypatch.setattr(_instructions_cli, "_render", lambda _a, *, dry_run, force: dict(rendered))
    with pytest.raises(SystemExit) as exited:
        _instructions_cli.run_instructions(_args(instructions_command="sync", dry_run=True, force=False))
    assert exited.value.code == expected_exit
    assert f'"would_change": {"true" if would_change else "false"}' in capsys.readouterr().out


def test_instructions_is_a_self_reporting_command() -> None:
    from trw_mcp.server._cli import _SELF_REPORTING_COMMANDS

    assert "instructions" in _SELF_REPORTING_COMMANDS  # daemon-off sync prints no raw JSON error lines


def _cli(project: Path, home: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    extra = {"HOME": str(home), "MEMORY_DAEMON_AUTOSTART": "false", "TRW_EMBEDDINGS_ENABLED": "false"}
    return subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", *argv],
        cwd=project,
        env=pinned_server_env({**os.environ, **extra}),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def test_check_on_a_real_project_reports_drift_and_writes_nothing(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / ".git").mkdir(parents=True)
    init = _cli(project, tmp_path, "init-project", ".")
    assert init.returncode == 0, init.stderr[-2000:]
    agents = project / "AGENTS.md"
    text = agents.read_text(encoding="utf-8")
    start = text.index("<!-- trw:start -->")
    agents.write_text(
        text[: start + len("<!-- trw:start -->")] + "\nhand-edited\n" + text[text.index("<!-- trw:end -->") :]
    )
    before = _tree_digest(project)

    done = _cli(project, tmp_path, "instructions", "check", "--json")

    assert _tree_digest(project) == before  # read-only
    assert done.returncode == 1, done.stdout + done.stderr[-2000:]
    assert '"would_change": true' in done.stdout
    assert not [line for line in done.stderr.splitlines() if line.lstrip().startswith("{")], done.stderr
