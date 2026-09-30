"""E2E-HINT-RELEVANCE and HOOK-CWD-SIBLING-HINTS.

A pre-edit hint says nothing rather than something unrelated to the file, and the Cursor and
Copilot hint hooks hand their Python child the project root they resolved.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.tools import _learnings_collector as collector

_DATA = Path(collector.__file__).resolve().parents[1] / "data"


def _row(rid: str, summary: str, **extra: object) -> dict[str, object]:
    return {"id": rid, "summary": summary, "status": "active", "impact": 0.5, "tags": [], **extra}


class _FakeRecall:
    """Answers the anchored lookup with anchored rows and every text query with the same generic hits."""

    def __init__(self, anchored: list[dict[str, object]], text: list[dict[str, object]]) -> None:
        self.anchored, self.text = anchored, text

    def __call__(self, query: str, **kw: object) -> list[dict[str, object]]:
        return self.anchored if kw.get("anchor_file") else self.text


def _collect(monkeypatch: pytest.MonkeyPatch, recall: _FakeRecall, **kw: object) -> list[str]:
    import trw_mcp.state.learning_injection as injection

    monkeypatch.setattr(injection, "recall_learnings", recall)
    rows = collector.collect_learnings(["/abs/pkg/loader.py", "loader.py"], single_page=True, **kw)  # type: ignore[arg-type]
    return [r.id for r in rows]


def test_an_unrelated_text_hit_is_dropped_when_the_file_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    text = [_row("generic", "Always use structlog for logging"), _row("named", "loader.py reads config twice")]
    got = _collect(monkeypatch, _FakeRecall([], text), anchor_file=None, must_name="/abs/pkg/loader.py")
    assert got == ["named"]


def test_without_mentions_every_text_hit_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    text = [_row("generic", "Always use structlog for logging")]
    assert _collect(monkeypatch, _FakeRecall([], text)) == ["generic"]


def test_an_anchored_lesson_needs_no_mention(monkeypatch: pytest.MonkeyPatch) -> None:
    anchored = [_row("anchored", "Never cache the parse result")]
    got = _collect(monkeypatch, _FakeRecall(anchored, []), anchor_file="pkg/loader.py", must_name="/abs/pkg/loader.py")
    assert got == ["anchored"]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (_row("a", "see pkg/loader.py for details"), True),
        (_row("b", "the loader.py module"), True),
        (_row("c", "reloader.py is unrelated"), False),
        (_row("d", "x", detail="edit loader.py first"), True),
        (_row("e", "x", tags=["loader.py"]), True),
        (_row("f", "x", anchors=[{"file": "pkg/loader.py"}]), True),
        (_row("g", "config loading notes"), False),
    ],
)
def test_a_mention_is_a_whole_path_or_file_name_token(row: dict[str, object], expected: bool) -> None:
    assert collector._names_any(row, ("pkg/loader.py", "loader.py")) is expected


@pytest.mark.parametrize(
    ("hook", "env"),
    [
        ("hooks/cursor/trw-before-edit-hint.sh", "CURSOR_PROJECT_DIR"),
        ("copilot/hooks/trw-copilot-distill-hint.sh", "COPILOT_PROJECT_DIR"),
    ],
)
def test_sibling_hint_hooks_pass_the_project_root_to_their_python_child(hook: str, env: str, tmp_path: Path) -> None:
    """Run from a subdirectory, the child must still be told the root the hook resolved."""
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    (project / ".trw").mkdir()
    (project / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    (project / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    log = tmp_path / "child.log"
    fake = project / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text(f'#!/bin/sh\necho "${{TRW_PROJECT_ROOT:-unset}}" >> {log}\n', encoding="utf-8")
    fake.chmod(0o755)
    payload = f'{{"tool_name":"Edit","tool_input":{{"file_path":"{project}/src/a.py"}}}}'

    subprocess.run(
        ["sh", str(_DATA / hook)],
        input=payload,
        text=True,
        capture_output=True,
        timeout=30,
        cwd=project / "src",
        env={"PATH": "/usr/bin:/bin", env: str(project), "HOME": str(tmp_path)},
    )

    assert log.read_text(encoding="utf-8").strip() == str(project)
