"""Regression tests for the UserPromptSubmit hook payload contract."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Public-mirror guard: this test asserts a MONOREPO invariant (it cross-checks
# the repo-root .claude/hooks/ copy against the bundled hook — a layout absent
# from the standalone trw-mcp PyPI/GitHub mirror). Skip cleanly there; the
# monorepo CI still enforces it.
if not (Path(__file__).resolve().parents[2] / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

from tests._auto_recall_hook_harness import (
    _HOOK_PATHS,
    _LIB_PATHS,
    _MATCHING_PROMPT,
    _MATCHING_SUMMARY,
    _ROOT,
    _SETTINGS_PATHS,
    _copy_hook_to_temp,
    _hook_log,
    _make_path_without_jq,
    _run_hook,
)

# The run-ownership fixtures (a project that always carries a NEWER foreign run)
# live beside the other hook-ownership tests. ``tests/hooks/`` is deliberately
# not a package — its own tests import the harness bare — so the directory is
# put on the path explicitly rather than reached through an import cycle.
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

from _ownership_harness import (  # noqa: I001 - must follow the sys.path line above
    DELIVER_COMPLETE,
    FILE_MODIFIED,
    build_project,
    run_hook as run_ownership_hook,
    write_hook_env,
)


# --------------------------------------------------------------------------
# Payload + distribution contract
# --------------------------------------------------------------------------


def test_user_prompt_submit_hook_reads_prompt_field() -> None:
    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")

        assert ".prompt // empty" in content
        assert "json.load(sys.stdin)" in content
        assert ".message // empty" not in content


def test_user_prompt_submit_hook_copies_stay_in_sync() -> None:
    """FR10: every deployed copy of the hook and its library is byte-identical."""
    # Arity-independent: adding or retiring a distribution copy must not require
    # editing this assertion, but the guard must never degrade into a
    # single-copy no-op, hence the explicit >= 2 floor.
    for paths, label in ((_HOOK_PATHS, "user-prompt-submit.sh"), (_LIB_PATHS, "lib-trw.sh")):
        assert len(paths) >= 2, "parity guard needs at least two copies to compare"
        contents = {path.read_text(encoding="utf-8") for path in paths}
        assert len(contents) == 1, f"{label} copies diverged: {[p.as_posix() for p in paths]}"


def test_user_prompt_submit_hook_timeout_is_500ms() -> None:
    for settings_path in _SETTINGS_PATHS:
        settings = settings_path.read_text(encoding="utf-8")
        assert '"timeout": 500' in settings

    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")
        assert "TIMEOUT_NS = 500_000_000" in content
        assert "MAX_KEYWORDS = 16" in content


def test_scorer_has_no_magic_scan_cap_or_prompt_denominator() -> None:
    """FR01/FR07: the two defects are absent from every copy of the hook."""
    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")
        assert "score = matches / len(keywords)" not in content
        assert "MAX_SCAN_FILES = 500" not in content
        assert "auto_recall_scan_cap" in content
        assert "event=AutoRecall" in content
        assert "decision=deadline" in content or 'decision = "deadline"' in content


# --------------------------------------------------------------------------
# FR03 / FR04 — phase no longer suppresses the recall limb
# --------------------------------------------------------------------------


def test_done_phase_still_injects_learnings(tmp_path: Path) -> None:
    """FR03: a delivered run must not retire the recall mechanism.

    ``infer_phase`` is a monotone ladder that returns ``done`` from the first
    ``trw_deliver_complete`` until a new run exists, so the pre-change early
    exit meant the mechanism switched itself off the first time a project
    shipped. The phase-GUIDANCE limb keeps its silence (PRD-CORE-095 FR06).
    """
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt=_MATCHING_PROMPT,
            phase="done",
            learnings=[{"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY}],
        )

        assert "TRW RECALL:" in result.stdout
        assert "[L-active-1]" in result.stdout
        assert "TRW [" not in result.stdout, "the phase-guidance limb must stay silent at done"


def test_same_phase_still_injects_learnings(tmp_path: Path) -> None:
    """FR04: the dominant suppressor — 79 of 86 live executions ended here.

    Every prompt after the first in a phase hit this exit, so auto-recall never
    ran for the overwhelming majority of prompts. The guidance limb keeps the
    suppression; the recall limb never reads it.
    """
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt=_MATCHING_PROMPT,
            phase="implement",
            cached_phase="implement",
            learnings=[{"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY}],
        )

        assert "TRW RECALL:" in result.stdout
        assert "[L-active-1]" in result.stdout
        assert "TRW [" not in result.stdout, "the phase-guidance limb must stay suppressed"


def test_phase_guidance_still_suppressed_and_logged_as_cached(tmp_path: Path) -> None:
    """Regression: with nothing to recall, an unchanged phase is still `cached`."""
    result = _run_hook(
        tmp_path,
        _HOOK_PATHS[1],
        prompt="entirely unrelated sourdough baking question",
        phase="implement",
        cached_phase="implement",
        learnings=[{"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY}],
    )

    assert result.stdout == ""
    assert "UserPromptSubmit|implement|cached" in _hook_log(result)


# --------------------------------------------------------------------------
# FR05 — one diagnostic record per prompt
# --------------------------------------------------------------------------


def test_emits_one_diagnostic_line_when_nothing_fires(tmp_path: Path) -> None:
    """FR05: silence must be distinguishable from absence.

    Before this, a below-threshold prompt and an empty store produced the same
    log line, which is exactly how an unreachable threshold survived unnoticed.
    """
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt="wal reset corruption in the memory database",
            phase="implement",
            # Suppress the phase-guidance limb so stdout carries recall or nothing.
            cached_phase="implement",
            learnings=[
                {
                    "learning_id": "L-partial",
                    "status": "active",
                    "summary": "memory pressure during compaction is unrelated",
                }
            ],
            env_overrides={"TRW_AUTO_RECALL_MIN_SCORE": "0.99"},
        )

        assert result.stdout == "", "nothing above threshold must reach the model"
        log = _hook_log(result)
        records = [line for line in log.splitlines() if "event=AutoRecall" in line]
        assert len(records) == 1, f"expected exactly one AutoRecall record, got {records}"
        fields = dict(token.split("=", 1) for token in records[0].split("|")[3].split() if "=" in token)
        assert set(fields) == {
            "event",
            "keywords",
            "scanned",
            "top_score",
            "top_id",
            "threshold",
            "injected",
            "decision",
            "elapsed_ms",
        }
        assert fields["threshold"] == "0.990"
        assert fields["injected"] == "0"
        assert "top_score=" in records[0]
        # The record also reaches stderr for interactive debugging.
        assert "event=AutoRecall" in result.stderr


def test_log_hook_execution_three_argument_form_is_byte_identical(tmp_path: Path) -> None:
    """FR05/RISK-006: extending log_hook_execution must not move any other hook's line.

    Drives the REAL lib-trw.sh, not the test stub.
    """
    for index, lib_path in enumerate(_LIB_PATHS):
        project_root = tmp_path / f"lib-{index}"
        (project_root / ".trw" / "context").mkdir(parents=True, exist_ok=True)
        driver = project_root / "driver.sh"
        driver.write_text(
            f'. "{lib_path}"\n'
            "init_hook_timer\n"
            'log_hook_execution "SessionStart" "startup" "0"\n'
            'log_hook_execution "UserPromptSubmit" "implement" "emitted" "event=AutoRecall injected=1"\n',
            encoding="utf-8",
        )

        subprocess.run(
            ["sh", str(driver)],
            capture_output=True,
            text=True,
            cwd=project_root,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_root)},
            check=False,
        )

        lines = (project_root / ".trw" / "context" / "hook-executions.log").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2, lines
        assert re.fullmatch(r"\S+ event=SessionStart matcher=startup exit=0 duration=-?\d+s", lines[0]), (
            f"three-argument line format changed: {lines[0]!r}"
        )
        assert lines[1].endswith(" event=AutoRecall injected=1"), lines[1]


# --------------------------------------------------------------------------
# FR11 — phase resolves from the session's OWN run
# --------------------------------------------------------------------------


def _emitted_phase(root: Path) -> str:
    log = (root / ".trw" / "context" / "hook-executions.log").read_text(encoding="utf-8")
    matchers = [
        token.split("=", 1)[1]
        for line in log.splitlines()
        if "event=UserPromptSubmit" in line
        for token in line.split()
        if token.startswith("matcher=")
    ]
    assert matchers, f"hook logged no UserPromptSubmit execution: {log!r}"
    return matchers[-1]


@pytest.mark.parametrize(
    "hook_dir",
    [
        pytest.param(_ROOT / "src" / "trw_mcp" / "data" / "hooks", id="bundled"),
        pytest.param(_ROOT.parent / ".claude" / "hooks", id="mirror"),
    ],
)
def test_phase_resolves_from_owned_run_not_recency(hook_dir: Path, tmp_path: Path) -> None:
    """FR11: a parallel instance's delivery must not pin THIS session's phase.

    The foreign run sorts strictly newer AND has already delivered, so recency
    would report `done`; the owned run has only modified files, so ownership
    reports `implement`. The two values are always different, which is what
    makes this assertion non-vacuous.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(FILE_MODIFIED, DELIVER_COMPLETE),
    )
    write_hook_env(root)

    result = run_ownership_hook(
        hook_dir / "user-prompt-submit.sh",
        root,
        payload={"prompt": "structlog event keyword", "session_id": "unused"},
    )

    assert result.returncode == 0, result.stderr
    assert _emitted_phase(root) == "implement", "phase came from the FOREIGN run"
    assert "TRW [IMPLEMENT]" in result.stdout


@pytest.mark.parametrize(
    "hook_dir",
    [
        pytest.param(_ROOT / "src" / "trw_mcp" / "data" / "hooks", id="bundled"),
        pytest.param(_ROOT.parent / ".claude" / "hooks", id="mirror"),
    ],
)
def test_unpinned_session_keeps_the_recency_fallback(hook_dir: Path, tmp_path: Path) -> None:
    """FR11 anti-false-negative: with no pin, behaviour is exactly today's.

    Hardening ownership must not silently disable the phase ladder for a session
    that genuinely owns no run — it falls back to newest-wins, as before.
    """
    root, _own, _foreign = build_project(
        tmp_path,
        own_pin=False,
        own_event_lines=(FILE_MODIFIED,),
        foreign_event_lines=(FILE_MODIFIED, DELIVER_COMPLETE),
    )
    write_hook_env(root)

    result = run_ownership_hook(
        hook_dir / "user-prompt-submit.sh",
        root,
        payload={"prompt": "structlog event keyword", "session_id": "unused"},
    )

    assert result.returncode == 0, result.stderr
    assert _emitted_phase(root) == "done", "the recency fallback was dropped"


def test_phase_ladder_has_exactly_one_implementation() -> None:
    """FR11: the private `_pcs_infer_phase` copy is gone (wiring pattern P10)."""
    hook_dirs = (
        _ROOT / "src" / "trw_mcp" / "data" / "hooks",
        _ROOT.parent / ".claude" / "hooks",
    )
    for hook_dir in hook_dirs:
        scripts = sorted(hook_dir.glob("*.sh"))
        assert scripts, f"hook enumeration broke for {hook_dir}"
        for script in scripts:
            assert "_pcs_infer_phase" not in script.read_text(encoding="utf-8"), script
        definitions = [
            script
            for script in scripts
            if re.search(r"^phase_from_events\(\)", script.read_text(encoding="utf-8"), re.MULTILINE)
        ]
        assert [p.name for p in definitions] == ["lib-trw.sh"], definitions


# --------------------------------------------------------------------------
# Pre-existing regression coverage
# --------------------------------------------------------------------------


def test_user_prompt_submit_hook_filters_to_active_learnings(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt=_MATCHING_PROMPT,
            phase="implement",
            cached_phase="plan",
            learnings=[
                {"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY},
                {"learning_id": "L-resolved-1", "status": "resolved", "summary": "Structlog event resolved note"},
            ],
        )

        assert "[L-active-1]" in result.stdout
        assert "[L-resolved-1]" not in result.stdout


def test_user_prompt_submit_hook_respects_min_score_env_override(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt="structlog keyword",
            phase="implement",
            cached_phase="plan",
            learnings=[{"learning_id": "L-active-1", "status": "active", "summary": "Structlog gotcha"}],
            env_overrides={"TRW_AUTO_RECALL_MIN_SCORE": "1.0"},
        )

        assert "[L-active-1]" not in result.stdout


def test_user_prompt_submit_hook_missing_prompt_is_silent(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt="",
            phase="implement",
            raw_input=json.dumps({}),
        )

        assert result.stdout == ""
        assert "UserPromptSubmit|implement|skipped" in _hook_log(result)


def test_user_prompt_submit_hook_malformed_json_is_silent(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt="",
            phase="implement",
            raw_input='{"prompt":"structlog event keyword"',
        )

        assert result.stdout == ""
        assert "UserPromptSubmit|implement|skipped" in _hook_log(result)


def test_user_prompt_submit_hook_uses_yaml_learning_ids_for_output_and_dedup(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name,
            hook_path,
            prompt=_MATCHING_PROMPT,
            phase="implement",
            cached_phase="plan",
            learnings=[
                {
                    "learning_id": "L-real-id",
                    "status": "active",
                    "summary": _MATCHING_SUMMARY,
                    "file_stem": "2026-04-10-structlog-gotcha",
                }
            ],
        )

        assert "[L-real-id]" in result.stdout
        assert "2026-04-10-structlog-gotcha" not in result.stdout
        injected_ids = (result.project_root / ".trw" / "context" / "injected_learning_ids.txt").read_text(
            encoding="utf-8"
        )
        assert injected_ids.strip() == "L-real-id"


def test_user_prompt_submit_hook_matches_wrapped_summary_text(tmp_path: Path) -> None:
    """A folded multi-line summary is still read whole — and now unquoted."""
    for hook_path in _HOOK_PATHS:
        project_root, hook, entries_dir = _copy_hook_to_temp(
            tmp_path / hook_path.parent.name / "wrapped-summary", hook_path
        )
        (entries_dir / "wrapped-summary.yaml").write_text(
            'id: "L-wrapped"\n'
            "status: active\n"
            "summary: 'mypy cache staleness produces phantom unused-ignore errors —\n"
            "  clear .mypy_cache before trusting results'\n",
            encoding="utf-8",
        )
        (project_root / ".trw" / "context" / "last_ups_phase").write_text("plan", encoding="utf-8")

        result = subprocess.run(
            ["sh", str(hook)],
            input=json.dumps({"prompt": "trusting results before clearing the mypy cache"}),
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

        assert "[L-wrapped]" in result.stdout
        assert "clear .mypy_cache before trusting results" in result.stdout
        assert "TRW RECALL: [L-wrapped] mypy cache" in result.stdout, "YAML quotes leaked into context"


def test_user_prompt_submit_hook_reads_config_yaml_runtime(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        result = _run_hook(
            tmp_path / hook_path.parent.name / "config-yaml",
            hook_path,
            prompt=_MATCHING_PROMPT,
            phase="implement",
            learnings=[{"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY}],
            config_yaml="auto_recall_enabled: false\n",
        )

        assert "[L-active-1]" not in result.stdout
        assert "event=AutoRecall" not in _hook_log(result), "a disabled limb must not score"


def test_user_prompt_submit_hook_config_yaml_pin_survives_the_new_default(tmp_path: Path) -> None:
    """Migration: a project pinning the old 0.7 keeps 0.7 after the upgrade."""
    result = _run_hook(
        tmp_path,
        _HOOK_PATHS[1],
        prompt=_MATCHING_PROMPT,
        phase="implement",
        learnings=[{"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY}],
        config_yaml="auto_recall_min_score: 0.7\n",
    )

    assert "threshold=0.700" in _hook_log(result)


def test_user_prompt_submit_hook_without_jq_uses_python_fallback(tmp_path: Path) -> None:
    """NFR04: identical output on a PATH with no jq."""
    path_without_jq = _make_path_without_jq(tmp_path)
    for hook_path in _HOOK_PATHS:
        learnings = [{"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY}]
        with_jq = _run_hook(
            tmp_path / hook_path.parent.name / "with-jq",
            hook_path,
            prompt=_MATCHING_PROMPT,
            phase="implement",
            cached_phase="plan",
            learnings=learnings,
        )
        without_jq = _run_hook(
            tmp_path / hook_path.parent.name / "no-jq",
            hook_path,
            prompt=_MATCHING_PROMPT,
            phase="implement",
            cached_phase="plan",
            learnings=learnings,
            path_override=path_without_jq,
        )

        assert "[L-active-1]" in without_jq.stdout
        assert without_jq.stdout == with_jq.stdout
