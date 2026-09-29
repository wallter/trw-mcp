"""PRD-CORE-333: the UserPromptSubmit read's dedup record is a checkout write, and never follows a symlink.

``_auto_recall_hook`` appends the ids it injected to ``.trw/context/injected_learning_ids.txt``.
That file is inside the checkout, which is untrusted content, so the append goes through
``_checkout_write`` (the census in ``test_no_raw_checkout_writes_census.py``). A planted
symlink is refused; the recall itself still reaches the prompt.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state import _auto_recall_hook as hook

_ROW = hook.Candidate("L-dedup01", "active", "wal reset corruption recovery path", ())


def _run(root: Path, injected: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = hook.main(
        [str(root), "wal reset corruption recovery", str(injected), "3", "100", "0.35", "10"],
        read_rows=lambda _root, _cap: [_ROW],
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_dedup_record_is_appended_under_the_project_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    injected = tmp_path / ".trw" / "context" / "injected_learning_ids.txt"

    code, out, _err = _run(tmp_path, injected, capsys)
    again, second, _ = _run(tmp_path, injected, capsys)

    assert code == again == 0
    assert "[L-dedup01]" in out
    assert injected.read_text(encoding="utf-8") == "L-dedup01\n"
    assert second == "", "the recorded id is not injected twice"


@pytest.mark.skipif(os.name != "posix", reason="symlink semantics")
def test_a_symlinked_context_dir_is_never_written_through(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "repo"
    (root / ".trw").mkdir(parents=True)
    (root / ".trw" / "context").symlink_to(outside, target_is_directory=True)

    code, out, err = _run(root, root / ".trw" / "context" / "injected_learning_ids.txt", capsys)

    assert code == 0
    assert "[L-dedup01]" in out, "a refused dedup record still leaves the recall in the prompt"
    assert "auto_recall_dedup_write_refused" in err
    assert list(outside.iterdir()) == [], "the append followed the planted symlink"
