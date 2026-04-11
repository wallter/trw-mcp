"""Regression tests for the PreCompact hook lifecycle contract."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HOOK_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "pre-compact.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "pre-compact.sh",
    _ROOT.parent / "trw-eval" / "trw-mcp-local" / "src" / "trw_mcp" / "data" / "hooks" / "pre-compact.sh",
)


def _copy_hook_to_temp(tmp_path: Path, source_hook: Path) -> tuple[Path, Path]:
    source_path = source_hook.as_posix()
    if "/trw-eval/trw-mcp-local/" in source_path:
        hook_label = "vendored"
    elif "/src/trw_mcp/data/hooks/" in source_path:
        hook_label = "bundled"
    else:
        hook_label = "dev"
    project_root = tmp_path / hook_label
    hooks_dir = project_root / ".claude" / "hooks"
    context_dir = project_root / ".trw" / "context"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hooks_dir / "pre-compact.sh"
    hook_path.write_text(source_hook.read_text(encoding="utf-8"), encoding="utf-8")
    hook_path.chmod(0o755)

    lib_hook = hooks_dir / "lib-trw.sh"
    lib_hook.write_text(
        """#!/bin/sh
init_hook_timer() { :; }
get_repo_root() { printf '%s' "$TRW_PROJECT_ROOT"; }
find_active_run() { return 1; }
log_hook_execution() { printf '%s|%s|%s\\n' "$1" "$2" "$3" >> "$TRW_HOOK_LOG"; }
""",
        encoding="utf-8",
    )
    return project_root, hook_path


def test_pre_compact_hook_copies_stay_in_sync() -> None:
    contents = [hook_path.read_text(encoding="utf-8") for hook_path in _HOOK_PATHS]
    assert contents[0] == contents[1] == contents[2]


def test_pre_compact_hook_clears_injected_learning_ids(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, temp_hook = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        injected_file = project_root / ".trw" / "context" / "injected_learning_ids.txt"
        injected_file.write_text("L-one\nL-two\n", encoding="utf-8")

        result = subprocess.run(
            ["sh", str(temp_hook)],
            input=json.dumps({"source": "context.compact"}),
            text=True,
            capture_output=True,
            cwd=project_root,
            env={
                **os.environ,
                "TRW_PROJECT_ROOT": str(project_root),
                "TRW_HOOK_LOG": str(project_root / "hook.log"),
            },
            check=False,
        )

        assert result.returncode == 0
        assert injected_file.read_text(encoding="utf-8") == ""
