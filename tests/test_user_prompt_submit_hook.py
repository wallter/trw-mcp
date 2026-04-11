"""Regression tests for the UserPromptSubmit hook payload contract."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_HOOK_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "user-prompt-submit.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "user-prompt-submit.sh",
)


def _copy_hook_to_temp(
    tmp_path: Path,
    source_hook: Path,
) -> tuple[Path, Path, Path]:
    hook_label = "bundled" if "/src/trw_mcp/data/hooks/" in source_hook.as_posix() else "dev"
    project_root = tmp_path / hook_label
    hooks_dir = project_root / ".claude" / "hooks"
    entries_dir = project_root / ".trw" / "learnings" / "entries"
    context_dir = project_root / ".trw" / "context"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    entries_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hooks_dir / "user-prompt-submit.sh"
    hook_path.write_text(source_hook.read_text(encoding="utf-8"), encoding="utf-8")
    hook_path.chmod(0o755)

    lib_hook = hooks_dir / "lib-trw.sh"
    lib_hook.write_text(
        """#!/bin/sh
init_hook_timer() { :; }
infer_phase() { printf '%s' "${TRW_TEST_PHASE:-implement}"; }
get_repo_root() { printf '%s' "$TRW_PROJECT_ROOT"; }
log_hook_execution() { printf '%s|%s|%s\\n' "$1" "$2" "$3" >> "$TRW_HOOK_LOG"; }
""",
        encoding="utf-8",
    )
    return project_root, hook_path, entries_dir


def _write_learning(
    entries_dir: Path,
    learning_id: str,
    *,
    status: str,
    summary: str,
) -> None:
    (entries_dir / f"{learning_id}.yaml").write_text(
        f'status: {status}\nsummary: "{summary}"\n',
        encoding="utf-8",
    )


def _run_hook(
    tmp_path: Path,
    source_hook: Path,
    *,
    prompt: str,
    phase: str,
    cached_phase: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    project_root, hook_path, entries_dir = _copy_hook_to_temp(tmp_path, source_hook)
    if cached_phase is not None:
        (project_root / ".trw" / "context" / "last_ups_phase").write_text(cached_phase, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": phase,
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
        }
    )
    if env_overrides:
        env.update(env_overrides)

    return subprocess.run(
        ["sh", str(hook_path)],
        input=json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env=env,
        check=False,
    )


def test_user_prompt_submit_hook_reads_prompt_field() -> None:
    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")

        assert ".prompt // empty" in content
        assert '"prompt"[[:space:]]*:[[:space:]]*"[^"]*"' in content
        assert ".message // empty" not in content
        assert '"message"[[:space:]]*:[[:space:]]*"[^"]*"' not in content


def test_user_prompt_submit_hook_copies_stay_in_sync() -> None:
    dev_hook, bundled_hook = _HOOK_PATHS
    assert dev_hook.read_text(encoding="utf-8") == bundled_hook.read_text(encoding="utf-8")


def test_user_prompt_submit_hook_done_phase_is_silent(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, entries_dir = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        _write_learning(entries_dir, "active-1", status="active", summary="Structlog event keyword gotcha")

        result = subprocess.run(
            ["sh", str(project_root / ".claude" / "hooks" / "user-prompt-submit.sh")],
            input=json.dumps({"prompt": "structlog event keyword"}),
            text=True,
            capture_output=True,
            cwd=project_root,
            env={
                **os.environ,
                "TRW_PROJECT_ROOT": str(project_root),
                "TRW_TEST_PHASE": "done",
                "TRW_HOOK_LOG": str(project_root / "hook.log"),
            },
            check=False,
        )

        assert result.stdout == ""


def test_user_prompt_submit_hook_filters_to_active_learnings(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, entries_dir = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        _write_learning(entries_dir, "active-1", status="active", summary="Structlog event keyword gotcha")
        _write_learning(entries_dir, "resolved-1", status="resolved", summary="Structlog event resolved note")
        (project_root / ".trw" / "context" / "last_ups_phase").write_text("implement", encoding="utf-8")

        result = subprocess.run(
            ["sh", str(project_root / ".claude" / "hooks" / "user-prompt-submit.sh")],
            input=json.dumps({"prompt": "structlog event keyword"}),
            text=True,
            capture_output=True,
            cwd=project_root,
            env={
                **os.environ,
                "TRW_PROJECT_ROOT": str(project_root),
                "TRW_TEST_PHASE": "implement",
                "TRW_HOOK_LOG": str(project_root / "hook.log"),
            },
            check=False,
        )

        assert "L-active-1" in result.stdout
        assert "L-resolved-1" not in result.stdout


def test_user_prompt_submit_hook_respects_min_score_env_override(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, entries_dir = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        _write_learning(entries_dir, "active-1", status="active", summary="Structlog gotcha")
        (project_root / ".trw" / "context" / "last_ups_phase").write_text("implement", encoding="utf-8")

        result = subprocess.run(
            ["sh", str(project_root / ".claude" / "hooks" / "user-prompt-submit.sh")],
            input=json.dumps({"prompt": "structlog keyword"}),
            text=True,
            capture_output=True,
            cwd=project_root,
            env={
                **os.environ,
                "TRW_PROJECT_ROOT": str(project_root),
                "TRW_TEST_PHASE": "implement",
                "TRW_HOOK_LOG": str(project_root / "hook.log"),
                "TRW_AUTO_RECALL_MIN_SCORE": "1.0",
            },
            check=False,
        )

        assert result.stdout == ""


def test_user_prompt_submit_hook_timeout_is_500ms() -> None:
    settings = (_ROOT / "src" / "trw_mcp" / "data" / "settings.json").read_text(encoding="utf-8")
    assert '"timeout": 500' in settings

    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")
        assert "TIMEOUT_NS = 500_000_000" in content
