"""Shared fixtures for the UserPromptSubmit auto-recall hook tests.

Both ``test_user_prompt_submit_hook.py`` (payload, phase and distribution
contract) and ``test_auto_recall_scoring.py`` (PRD-FIX-124 scoring, tunables and
deadline) drive the SAME real hook script as a subprocess. The fixture builders
live here so neither test module has to import the other, and so there is
exactly one definition of what a fixture project looks like.

Nothing in this module reimplements any part of the hook: it lays out a project,
writes YAML mirror entries, runs the shipped script, and reads back what it did.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
#: Every live copy of the hook / settings file (a formerly-vendored third
#: mirror was deleted wholesale in `a77650f238`; only these two remain).
#:
#: Deliberately NOT filtered with `if path.exists()`: every copy listed here
#: MUST be present, and skipping absent ones would turn a deleted hook into a
#: silent pass. If a third distribution copy is reintroduced, add it here.
_HOOK_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "user-prompt-submit.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "user-prompt-submit.sh",
)
_LIB_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "lib-trw.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh",
)
_SETTINGS_PATHS = (
    _ROOT.parent / ".claude" / "settings.json",
    _ROOT / "src" / "trw_mcp" / "data" / "settings.json",
)

#: A learning that a normal engineering prompt in these tests will match.
_MATCHING_SUMMARY = "Structlog event keyword gotcha"
_MATCHING_PROMPT = "structlog event keyword"


@dataclass(frozen=True)
class HookRun:
    """One real-subprocess hook invocation, plus the project it ran against."""

    stdout: str
    stderr: str
    returncode: int
    project_root: Path


def _copy_hook_to_temp(
    tmp_path: Path,
    source_hook: Path,
) -> tuple[Path, Path, Path]:
    source_path = source_hook.as_posix()
    hook_label = "bundled" if "/src/trw_mcp/data/hooks/" in source_path else "dev"
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

    # Stub library: the phase ladder and the log sink are replaced so these
    # tests observe the hook's OWN decisions. The fourth `detail` field is the
    # PRD-FIX-124-FR05 diagnostic; log_hook_execution's real four-argument
    # contract is asserted separately against the shipped lib-trw.sh.
    lib_hook = hooks_dir / "lib-trw.sh"
    lib_hook.write_text(
        """#!/bin/sh
init_hook_timer() { :; }
infer_phase() { printf '%s' "${TRW_TEST_PHASE:-implement}"; }
get_repo_root() { printf '%s' "$TRW_PROJECT_ROOT"; }
log_hook_execution() { printf '%s|%s|%s|%s\\n' "$1" "$2" "$3" "$4" >> "$TRW_HOOK_LOG"; }
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
    tags: list[str] | None = None,
    detail_text: str | None = None,
) -> None:
    """Write one YAML mirror entry.

    ``tags`` is emitted as a block sequence — the shape every real entry uses
    and the one PRD-FIX-124-FR02 teaches the hook's field reader to read.
    """
    stem = file_stem or learning_id
    body = f'id: "{learning_id}"\nstatus: {status}\nsummary: "{summary}"\n'
    if tags is not None:
        body += "tags:\n" + "".join(f"- {tag}\n" for tag in tags)
    if detail_text is not None:
        body += f'detail: "{detail_text}"\n'
    (entries_dir / f"{stem}.yaml").write_text(body, encoding="utf-8")


def _make_path_without_jq(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin-no-jq"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool_name in ("sh", "python3", "grep", "head", "sed", "tr", "cat", "dirname", "mkdir", "rm"):
        tool_path = shutil.which(tool_name)
        assert tool_path is not None, f"Required tool missing in test environment: {tool_name}"
        (bin_dir / tool_name).symlink_to(tool_path)
    return str(bin_dir)


def _run_hook(
    tmp_path: Path,
    source_hook: Path,
    *,
    prompt: str,
    phase: str,
    cached_phase: str | None = None,
    env_overrides: dict[str, str] | None = None,
    raw_input: str | None = None,
    learnings: list[dict[str, Any]] | None = None,
    config_yaml: str | None = None,
    path_override: str | None = None,
) -> HookRun:
    """Drive the REAL hook script against a fixture project (FR10)."""
    project_root, hook_path, entries_dir = _copy_hook_to_temp(tmp_path, source_hook)
    if cached_phase is not None:
        (project_root / ".trw" / "context" / "last_ups_phase").write_text(cached_phase, encoding="utf-8")
    for learning in learnings or []:
        _write_learning(entries_dir, **learning)
    if config_yaml is not None:
        (project_root / ".trw" / "config.yaml").write_text(config_yaml, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": phase,
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
        }
    )
    if path_override is not None:
        env["PATH"] = path_override
    if env_overrides:
        env.update(env_overrides)

    completed = subprocess.run(
        ["sh", str(hook_path)],
        input=raw_input if raw_input is not None else json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env=env,
        check=False,
    )
    return HookRun(completed.stdout, completed.stderr, completed.returncode, project_root)


def _hook_log(run: HookRun) -> str:
    return (run.project_root / "hook.log").read_text(encoding="utf-8")


def diagnostic(project_root: Path) -> dict[str, str]:
    """Parse the single FR05 ``event=AutoRecall`` record out of the stub hook log.

    Asserts there is exactly one: "one record per prompt" is the requirement, so
    a helper that silently took the last of several would hide a regression.
    """
    records = [
        line.split("|", 3)[3]
        for line in (project_root / "hook.log").read_text(encoding="utf-8").splitlines()
        if line.count("|") >= 3 and line.split("|", 3)[3].startswith("event=AutoRecall")
    ]
    assert len(records) == 1, f"expected exactly one AutoRecall record, got {records}"
    return dict(token.split("=", 1) for token in records[0].split() if "=" in token)
