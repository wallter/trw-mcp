"""TRW-private check-then-delete races (FS-LINT triage P1).

Each case forces the interleaving (a writer acts between the check and the delete),
asserts that the interleaving actually ran, and asserts the late writer's bytes survive.
"""

from __future__ import annotations

import argparse
import gzip
import os
import sys
import time
from pathlib import Path

import pytest

from trw_mcp.cli.channel_doctor import run_channel_doctor
from trw_mcp.telemetry.retention import rotate_and_compress

pytestmark = pytest.mark.integration


def test_segment_extended_while_compressing_is_kept_not_unlinked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("active\n", encoding="utf-8")
    segment = tmp_path / "events.jsonl.1"
    segment.write_bytes(b'{"seq":1}\n')
    old = time.time() - 3600
    os.utime(segment, (old, old))
    real_open = gzip.open
    appended: list[bool] = []

    def _open_then_extend(path: object, mode: str = "rb", *a: object, **k: object) -> object:
        handle = real_open(path, mode, *a, **k)  # type: ignore[arg-type]
        if mode.startswith("w") and not appended:
            with segment.open("ab") as late_writer:  # a writer that still held the segment open
                late_writer.write(b'{"seq":2}\n')
            appended.append(True)
        return handle

    monkeypatch.setattr("trw_mcp.telemetry.retention.gzip.open", _open_then_extend)
    result = rotate_and_compress(log, max_bytes=10_000_000, min_age_seconds=60.0)

    assert appended, "the late write never ran; the test proves nothing"
    assert result["compressed"] == []
    assert {"segment": "events.jsonl.1", "reason": "changed_during_compress"} in result["skipped"]
    assert segment.read_bytes() == b'{"seq":1}\n{"seq":2}\n', "the late writer's bytes were lost"

    # The next run, with no writer, recompresses the whole segment over the stale archive.
    monkeypatch.undo()
    os.utime(segment, (old, old))
    again = rotate_and_compress(log, max_bytes=10_000_000, min_age_seconds=60.0)
    assert again["compressed"] == ["events.jsonl.1.gz"]
    assert not segment.exists()
    with gzip.open(tmp_path / "events.jsonl.1.gz", "rb") as handle:
        assert handle.read() == b'{"seq":1}\n{"seq":2}\n'


def test_channel_lock_touched_after_it_was_judged_stale_is_not_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    channels = tmp_path / ".trw" / "channels"
    channels.mkdir(parents=True)
    lock = channels / "orphan.lock"
    lock.write_text("held", encoding="utf-8")
    old = time.time() - 25 * 3600
    os.utime(lock, (old, old))
    real_stat = Path.stat
    touched: list[bool] = []

    def _stat_then_refresh(self: Path, *a: object, **k: object) -> os.stat_result:
        result = real_stat(self, *a, **k)  # type: ignore[arg-type]
        # Only the stat _run_clean itself makes counts: Python versions differ in whether
        # rglob/resolve stat the lock first, and an early refresh would make the lock fresh
        # BEFORE the staleness verdict, so the test would pass without the fix.
        if self.name == "orphan.lock" and not touched and sys._getframe(1).f_code.co_name == "_run_clean":
            os.utime(lock, None)  # a channel refreshes its lock right after we judged it stale
            touched.append(True)
        return result

    monkeypatch.setattr(Path, "stat", _stat_then_refresh)
    args = argparse.Namespace(
        project_dir=str(tmp_path), channel_doctor_command="clean", max_age_hours=24, dry_run=False
    )
    run_channel_doctor(args)

    assert touched, "the refresh never ran; the test proves nothing"
    assert lock.exists(), "a lock refreshed after the staleness check was deleted"
    assert "Removed" not in capsys.readouterr().out
