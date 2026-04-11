"""Regression tests for SessionStart hook state clearing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HOOK_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "session-start.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "session-start.sh",
    _ROOT.parent / "trw-eval" / "trw-mcp-local" / "src" / "trw_mcp" / "data" / "hooks" / "session-start.sh",
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

    hook_path = hooks_dir / "session-start.sh"
    hook_path.write_text(source_hook.read_text(encoding="utf-8"), encoding="utf-8")
    hook_path.chmod(0o755)

    lib_hook = hooks_dir / "lib-trw.sh"
    lib_hook.write_text(
        """#!/bin/sh
init_hook_timer() { :; }
get_repo_root() { printf '%s' "$TRW_PROJECT_ROOT"; }
""",
        encoding="utf-8",
    )
    lib_hook.chmod(0o755)
    return project_root, hook_path


def test_session_start_hook_copies_stay_in_sync() -> None:
    contents = [hook_path.read_text(encoding="utf-8") for hook_path in _HOOK_PATHS]
    assert contents[0] == contents[1] == contents[2]


def test_session_start_hook_clears_phase_and_injected_state_for_all_sources(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        for source in ("startup", "resume", "compact", "clear"):
            project_root, local_hook = _copy_hook_to_temp(tmp_path / hook_path.parent.name / source, hook_path)
            context_dir = project_root / ".trw" / "context"
            (context_dir / "last_ups_phase").write_text("implement", encoding="utf-8")
            (context_dir / "injected_learning_ids.txt").write_text("L-1\nL-2\n", encoding="utf-8")

            result = subprocess.run(
                ["sh", str(local_hook)],
                input=json.dumps({"source": source}),
                text=True,
                capture_output=True,
                cwd=project_root,
                env={
                    **os.environ,
                    "TRW_PROJECT_ROOT": str(project_root),
                },
                check=False,
            )

            assert result.returncode == 0
            assert not (context_dir / "last_ups_phase").exists()
            assert (context_dir / "injected_learning_ids.txt").read_text(encoding="utf-8") == ""
