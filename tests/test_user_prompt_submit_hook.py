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
    _ROOT.parent / "trw-eval" / "trw-mcp-local" / "src" / "trw_mcp" / "data" / "hooks" / "user-prompt-submit.sh",
)
_SETTINGS_PATHS = (
    _ROOT.parent / ".claude" / "settings.json",
    _ROOT / "src" / "trw_mcp" / "data" / "settings.json",
    _ROOT.parent / "trw-eval" / "trw-mcp-local" / "src" / "trw_mcp" / "data" / "settings.json",
)


def _copy_hook_to_temp(
    tmp_path: Path,
    source_hook: Path,
) -> tuple[Path, Path, Path]:
    source_path = source_hook.as_posix()
    if "/trw-eval/trw-mcp-local/" in source_path:
        hook_label = "vendored"
    elif "/src/trw_mcp/data/hooks/" in source_path:
        hook_label = "bundled"
    else:
        hook_label = "dev"
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
    file_stem: str | None = None,
) -> None:
    stem = file_stem or learning_id
    (entries_dir / f"{stem}.yaml").write_text(
        f'id: "{learning_id}"\nstatus: {status}\nsummary: "{summary}"\n',
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
    raw_input: str | None = None,
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
        input=raw_input if raw_input is not None else json.dumps({"prompt": prompt}),
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
    contents = [hook_path.read_text(encoding="utf-8") for hook_path in _HOOK_PATHS]
    assert contents[0] == contents[1] == contents[2]


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

        assert "[L-active-1]" not in result.stdout


def test_user_prompt_submit_hook_filters_to_active_learnings(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, entries_dir = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        _write_learning(entries_dir, "L-active-1", status="active", summary="Structlog event keyword gotcha")
        _write_learning(entries_dir, "L-resolved-1", status="resolved", summary="Structlog event resolved note")
        (project_root / ".trw" / "context" / "last_ups_phase").write_text("plan", encoding="utf-8")

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

        assert "[L-active-1]" in result.stdout
        assert "[L-resolved-1]" not in result.stdout


def test_user_prompt_submit_hook_respects_min_score_env_override(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, entries_dir = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        _write_learning(entries_dir, "L-active-1", status="active", summary="Structlog gotcha")
        (project_root / ".trw" / "context" / "last_ups_phase").write_text("plan", encoding="utf-8")

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

        assert "[L-active-1]" not in result.stdout


def test_user_prompt_submit_hook_timeout_is_500ms() -> None:
    for settings_path in _SETTINGS_PATHS:
        settings = settings_path.read_text(encoding="utf-8")
        assert '"timeout": 500' in settings

    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")
        assert "TIMEOUT_NS = 500_000_000" in content
        assert "MAX_KEYWORDS = 16" in content


def test_user_prompt_submit_hook_cached_phase_is_silent(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, entries_dir = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        _write_learning(entries_dir, "L-active-1", status="active", summary="Structlog event keyword gotcha")
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

        assert result.stdout == ""
        hook_log = (project_root / "hook.log").read_text(encoding="utf-8")
        assert "UserPromptSubmit|implement|cached" in hook_log


def test_user_prompt_submit_hook_missing_prompt_is_silent(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, _ = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)

        result = subprocess.run(
            ["sh", str(project_root / ".claude" / "hooks" / "user-prompt-submit.sh")],
            input=json.dumps({}),
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

        assert result.stdout == ""
        hook_log = (project_root / "hook.log").read_text(encoding="utf-8")
        assert "UserPromptSubmit|implement|skipped" in hook_log


def test_user_prompt_submit_hook_uses_yaml_learning_ids_for_output_and_dedup(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, _, entries_dir = _copy_hook_to_temp(tmp_path / hook_path.parent.name, hook_path)
        _write_learning(
            entries_dir,
            "L-real-id",
            status="active",
            summary="Structlog event keyword gotcha",
            file_stem="2026-04-10-structlog-gotcha",
        )
        (project_root / ".trw" / "context" / "last_ups_phase").write_text("plan", encoding="utf-8")

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

        assert "[L-real-id]" in result.stdout
        assert "2026-04-10-structlog-gotcha" not in result.stdout
        injected_ids = (project_root / ".trw" / "context" / "injected_learning_ids.txt").read_text(encoding="utf-8")
        assert injected_ids.strip() == "L-real-id"
