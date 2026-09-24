"""The shell sees the hook switches exactly as ``TRWConfig`` resolves them.

``lib-trw.sh`` used to grep ``.trw/config.yaml`` for ``hooks_enabled`` and
``learning_recall_enabled``. That read neither the machine layer nor any
``TRW_*`` override, and missed a flow mapping or a quoted key, so the shell and
the server could disagree about whether an arm was ablated. The shell now reads
only ``.trw/runtime/hook-flags``, which ``TRWConfig`` writes (lead ruling
2026-09-23). Each case here resolves both switches in Python and in the sourced
lib, and the answers must match.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from trw_mcp.models.config import get_config, reload_config
from trw_mcp.state._hook_flags import hook_flags_path, write_hook_flags

pytestmark = pytest.mark.unit

LIB = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh"
_PROBE = f'. "{LIB}"; echo hooks=on; if trw_learnings_injection_allowed; then echo recall=on; else echo recall=off; fi'


def _shell(root: Path) -> dict[str, bool]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_") and "HOOKS_ENABLED" not in k}
    env |= {"CLAUDE_PROJECT_DIR": str(root), "HOME": str(root / "home")}
    out = subprocess.run(["/bin/sh", "-c", _PROBE], env=env, cwd=root, capture_output=True, text=True).stdout
    return {"hooks_enabled": "hooks=on" in out, "learning_recall_enabled": "recall=on" in out}


CASES = {
    "block-style": ({"project": "hooks_enabled: false\nlearning_recall_enabled: false\n"}, {}),
    "flow-style": ({"project": "{hooks_enabled: false, learning_recall_enabled: false}\n"}, {}),
    "quoted-keys": ({"project": "\"hooks_enabled\": 'false'\n'learning_recall_enabled': \"no\"\n"}, {}),
    "machine-layer": ({"machine": "hooks_enabled: false\nlearning_recall_enabled: false\n"}, {}),
    "project-over-machine": ({"machine": "hooks_enabled: false\n", "project": "hooks_enabled: true\n"}, {}),
    "env-over-project": (
        {"project": "hooks_enabled: true\nlearning_recall_enabled: true\n"},
        {"TRW_HOOKS_ENABLED": "false", "TRW_LEARNING_RECALL_ENABLED": "false"},
    ),
    "defaults": ({}, {}),
}


@pytest.mark.parametrize("case", CASES)
def test_python_and_shell_resolve_both_switches_identically(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files, env = CASES[case]
    trw_dir = tmp_path / ".trw"
    (tmp_path / "home" / ".trw").mkdir(parents=True)
    trw_dir.mkdir()
    if "project" in files:
        (trw_dir / "config.yaml").write_text(files["project"], encoding="utf-8")
    if "machine" in files:
        (tmp_path / "home" / ".trw" / "config.yaml").write_text(files["machine"], encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    for key in ("TRW_HOOKS_ENABLED", "TRW_LEARNING_RECALL_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reload_config()
    config = get_config()

    write_hook_flags(trw_dir)

    python = {"hooks_enabled": config.hooks_enabled, "learning_recall_enabled": config.learning_recall_enabled}
    assert _shell(tmp_path) == python
    reload_config()


def test_a_missing_file_means_both_switches_on(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("hooks_enabled: false\n", encoding="utf-8")

    assert _shell(tmp_path) == {"hooks_enabled": True, "learning_recall_enabled": True}


def test_the_cli_publishes_the_file_for_the_current_project(tmp_path: Path) -> None:
    """trw-eval runs ``trw-mcp hook-flags`` in the container after it writes an overlay."""
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("{hooks_enabled: false}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_")}
    env |= {"TRW_PROJECT_ROOT": str(tmp_path), "HOME": str(tmp_path / "home")}
    code = "import sys; from trw_mcp.server._cli import main; sys.argv = ['trw-mcp', 'hook-flags']; main()"

    proc = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    assert hook_flags_path(tmp_path / ".trw").read_text(encoding="utf-8") == (
        "hooks_enabled=false\nlearning_recall_enabled=true\n"
    )
