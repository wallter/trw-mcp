"""E2E-UPS-ROBUST: a repeated prompt logs decision=dedup, and a prompt past ARG_MAX still reaches the scorer."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from tests._auto_recall_hook_harness import _run_hook, diagnostic
from tests.test_user_prompt_submit_hook import _HOOK_PATHS, _MATCHING_PROMPT, _MATCHING_SUMMARY
from trw_mcp.state import _auto_recall_hook as hook

_ROW = hook.Candidate("L-dedup01", "active", "wal reset corruption recovery path", ())


def _decision(root: Path, injected: Path, prompt: str, capsys: pytest.CaptureFixture[str]) -> str:
    hook.main(
        [str(root), prompt, str(injected), "3", "100", "0.35", "10"],
        read_rows=lambda _root, _cap: [_ROW],
    )
    return next(t for t in capsys.readouterr().err.split() if t.startswith("decision=")).split("=", 1)[1]


def test_a_repeat_of_an_injected_lesson_logs_dedup_not_no_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    injected = tmp_path / ".trw" / "context" / "injected_learning_ids.txt"
    prompt = "wal reset corruption recovery"

    assert _decision(tmp_path, injected, prompt, capsys) == "fired"
    assert _decision(tmp_path, injected, prompt, capsys) == "dedup"
    assert _decision(tmp_path, injected, "sourdough baking question", capsys) == "no_match"


def test_a_prompt_of_dash_is_read_from_stdin(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = hook.main(
        [str(tmp_path), "-", str(tmp_path / "ids.txt"), "3", "100", "0.35", "10"],
        read_rows=lambda _root, _cap: [_ROW],
        stdin=io.StringIO("wal reset corruption recovery"),
    )

    assert code == 0
    assert "[L-dedup01]" in capsys.readouterr().out


@pytest.mark.parametrize("hook_path", _HOOK_PATHS, ids=lambda p: p.parent.parent.name)
def test_a_megabyte_prompt_still_recalls_through_the_real_hook(tmp_path: Path, hook_path: Path) -> None:
    """RED before: the prompt rode argv, the exec failed with 'Argument list too long', and recall silently never ran."""
    prompt = _MATCHING_PROMPT + " " + ("filler " * 220_000)

    result = _run_hook(
        tmp_path,
        hook_path,
        prompt=prompt,
        phase="implement",
        learnings=[{"learning_id": "L-active-1", "status": "active", "summary": _MATCHING_SUMMARY}],
    )

    assert "[L-active-1]" in result.stdout
    assert diagnostic(result.project_root)["decision"] == "fired"
