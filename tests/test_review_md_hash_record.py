"""Codex known issues on W1-REVIEW-MD-RECOGNIZER-TIGHT: the REVIEW.md hash record is read and written safely.

KI-1: a FIFO (or any non-regular file) planted as the record stalled uninstall on the read; it now proves nothing,
so the REVIEW.md is kept. KI-2: a text-mode write translated newlines on Windows, so the recorded hash never
matched the bytes on disk; the file is now written with no newline translation.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_a_fifo_as_the_record_is_not_read_and_proves_nothing(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._review_md import _REVIEW_TEMPLATE, _review_hash_path, is_generated_review_md

    trw_dir = tmp_path / ".trw"
    record = _review_hash_path(trw_dir)
    record.parent.mkdir(parents=True)
    os.mkfifo(record)
    answer: list[bool] = []
    worker = threading.Thread(
        target=lambda: answer.append(
            is_generated_review_md(_REVIEW_TEMPLATE.replace("{learning_entries}", "x"), trw_dir)
        ),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=5)

    assert answer == [False], "reading the record blocked on a FIFO (uninstall would stall)"


def test_review_md_is_written_without_newline_translation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorded hash is of the exact bytes on disk, on every platform."""
    from trw_mcp.state.claude_md import _sync

    seen: list[Any] = []
    real = os.fdopen

    def spy(fd: int, *args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("newline"))
        return real(fd, *args, **kwargs)

    monkeypatch.setattr(_sync.os, "fdopen", spy)
    (tmp_path / ".trw").mkdir()
    _sync.generate_review_md(tmp_path / ".trw", repo_root=tmp_path)

    assert seen == [""]
