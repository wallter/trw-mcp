"""Tests for _conflict.py — SHA-256 conflict detection + atomic write.

PRD-DIST-2400 FR06, FR07.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from trw_mcp.channels._conflict import (
    RenderLog,
    RenderLogEntry,
    detect_human_edit,
    reconcile,
    write_atomic,
)
from trw_mcp.channels._manifest_models import HumanEditDetection, MarkersConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _make_markers(start: str = "<!-- start -->", end: str = "<!-- end -->") -> MarkersConfig:
    return MarkersConfig(start=start, end=end)


# ---------------------------------------------------------------------------
# RenderLogEntry — basic construction
# ---------------------------------------------------------------------------


def test_render_log_entry_to_dict(tmp_path: Path) -> None:
    entry = RenderLogEntry(
        channel_id="ch1",
        target_path=tmp_path / "file.md",
        sha="abc",
        ts="2026-05-28T00:00:00.000Z",
        bytes_written=100,
    )
    d = entry.to_dict()
    assert d["channel_id"] == "ch1"
    assert d["sha"] == "abc"
    assert d["bytes_written"] == 100


def test_render_log_entry_from_dict(tmp_path: Path) -> None:
    d: dict[str, object] = {
        "channel_id": "ch1",
        "target_path": str(tmp_path / "file.md"),
        "sha": "def",
        "ts": "2026-05-28T00:00:00.000Z",
        "bytes_written": 200,
    }
    entry = RenderLogEntry.from_dict(d)
    assert entry.channel_id == "ch1"
    assert entry.sha == "def"
    assert entry.bytes_written == 200


# ---------------------------------------------------------------------------
# RenderLog — append + last_for
# ---------------------------------------------------------------------------


def test_render_log_append_and_read_back(tmp_path: Path) -> None:
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)
    entry = RenderLogEntry(
        channel_id="cc-01",
        target_path=tmp_path / "CLAUDE.md",
        sha="sha1",
        ts="2026-05-28T00:00:00.000Z",
        bytes_written=50,
    )
    rl.append(entry)
    result = rl.last_for("cc-01", tmp_path / "CLAUDE.md")
    assert result is not None
    assert result.sha == "sha1"


def test_render_log_last_for_returns_last_entry(tmp_path: Path) -> None:
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)
    target = tmp_path / "CLAUDE.md"
    for i, sha in enumerate(["sha1", "sha2", "sha3"]):
        rl.append(
            RenderLogEntry(
                channel_id="ch",
                target_path=target,
                sha=sha,
                ts="2026-05-28T00:00:00.000Z",
                bytes_written=i,
            )
        )
    result = rl.last_for("ch", target)
    assert result is not None
    assert result.sha == "sha3"


def test_render_log_last_for_missing_channel_returns_none(tmp_path: Path) -> None:
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)
    rl.append(
        RenderLogEntry(
            channel_id="other",
            target_path=tmp_path / "x.md",
            sha="sha",
            ts="2026-05-28T00:00:00.000Z",
            bytes_written=0,
        )
    )
    result = rl.last_for("cc-01", tmp_path / "x.md")
    assert result is None


def test_render_log_last_for_no_file_returns_none(tmp_path: Path) -> None:
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)
    result = rl.last_for("ch", tmp_path / "file.md")
    assert result is None


def test_render_log_append_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """append() must not raise even if writing fails."""
    log_path = tmp_path / "no-write" / "render-log.jsonl"
    rl = RenderLog(log_path)

    original_mkdir = Path.mkdir

    def _fail_mkdir(self: Path, **kwargs: object) -> None:
        raise PermissionError("no access")

    monkeypatch.setattr(Path, "mkdir", _fail_mkdir)
    # Should not raise
    rl.append(
        RenderLogEntry(
            channel_id="ch",
            target_path=tmp_path / "f.md",
            sha="sha",
            ts="ts",
            bytes_written=0,
        )
    )


# ---------------------------------------------------------------------------
# detect_human_edit — NONE mode
# ---------------------------------------------------------------------------


def test_detect_human_edit_none_mode_always_false(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    target.write_text("content", encoding="utf-8")
    result = detect_human_edit(
        mode=HumanEditDetection.NONE,
        target_path=target,
        expected_sha="anything",
    )
    assert result is False


def test_detect_human_edit_no_baseline_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    target.write_text("content", encoding="utf-8")
    for mode in HumanEditDetection:
        if mode is HumanEditDetection.NONE:
            continue
        result = detect_human_edit(
            mode=mode,
            target_path=target,
            expected_sha=None,
            markers=_make_markers() if mode in (
                HumanEditDetection.SHA256_SEGMENT,
                HumanEditDetection.MARKER_BOUNDARY,
            ) else None,
        )
        assert result is False, f"mode={mode} should return False when expected_sha is None"


# ---------------------------------------------------------------------------
# detect_human_edit — RENDER_LOG mode
# ---------------------------------------------------------------------------


def test_detect_human_edit_render_log_no_edit(tmp_path: Path) -> None:
    content = "hello world\n"
    target = tmp_path / "file.md"
    target.write_bytes(content.encode("utf-8"))
    expected = _sha256(content.encode("utf-8"))
    result = detect_human_edit(
        mode=HumanEditDetection.RENDER_LOG,
        target_path=target,
        expected_sha=expected,
    )
    assert result is False


def test_detect_human_edit_render_log_with_edit(tmp_path: Path) -> None:
    target = tmp_path / "file.md"
    target.write_text("original content", encoding="utf-8")
    stale_sha = _sha256("old content")
    result = detect_human_edit(
        mode=HumanEditDetection.RENDER_LOG,
        target_path=target,
        expected_sha=stale_sha,
    )
    assert result is True


# ---------------------------------------------------------------------------
# detect_human_edit — SHA256_SEGMENT mode
# ---------------------------------------------------------------------------


def test_detect_human_edit_sha256_segment_no_edit(tmp_path: Path) -> None:
    markers = _make_markers("<!-- start -->", "<!-- end -->")
    interior = "generated content"
    content = f"preamble\n<!-- start -->{interior}<!-- end -->\npostamble"
    target = tmp_path / "file.md"
    target.write_text(content, encoding="utf-8")
    expected = _sha256(interior)
    result = detect_human_edit(
        mode=HumanEditDetection.SHA256_SEGMENT,
        target_path=target,
        expected_sha=expected,
        markers=markers,
    )
    assert result is False


def test_detect_human_edit_sha256_segment_with_edit(tmp_path: Path) -> None:
    markers = _make_markers("<!-- start -->", "<!-- end -->")
    content = "pre\n<!-- start -->**edited by human**<!-- end -->\npost"
    target = tmp_path / "file.md"
    target.write_text(content, encoding="utf-8")
    expected_of_original = _sha256("original generated content")
    result = detect_human_edit(
        mode=HumanEditDetection.SHA256_SEGMENT,
        target_path=target,
        expected_sha=expected_of_original,
        markers=markers,
    )
    assert result is True


def test_detect_human_edit_sha256_segment_no_markers(tmp_path: Path) -> None:
    """If markers not found in file, returns False (no segment = no baseline)."""
    markers = _make_markers("<!-- start -->", "<!-- end -->")
    target = tmp_path / "file.md"
    target.write_text("no markers here at all", encoding="utf-8")
    result = detect_human_edit(
        mode=HumanEditDetection.SHA256_SEGMENT,
        target_path=target,
        expected_sha="anything",
        markers=markers,
    )
    assert result is False


# ---------------------------------------------------------------------------
# detect_human_edit — MARKER_BOUNDARY mode
# ---------------------------------------------------------------------------


def test_detect_human_edit_marker_boundary_empty_interior(tmp_path: Path) -> None:
    markers = _make_markers("<!-- start -->", "<!-- end -->")
    target = tmp_path / "file.md"
    target.write_text("before\n<!-- start --><!-- end -->\nafter", encoding="utf-8")
    result = detect_human_edit(
        mode=HumanEditDetection.MARKER_BOUNDARY,
        target_path=target,
        expected_sha="sha",
        markers=markers,
    )
    assert result is False


def test_detect_human_edit_marker_boundary_has_content(tmp_path: Path) -> None:
    markers = _make_markers("<!-- start -->", "<!-- end -->")
    target = tmp_path / "file.md"
    target.write_text("before\n<!-- start -->some content<!-- end -->\nafter", encoding="utf-8")
    result = detect_human_edit(
        mode=HumanEditDetection.MARKER_BOUNDARY,
        target_path=target,
        expected_sha="sha",
        markers=markers,
    )
    assert result is True


# ---------------------------------------------------------------------------
# detect_human_edit — KEY_NAMESPACE mode
# ---------------------------------------------------------------------------


def test_detect_human_edit_key_namespace_no_edit(tmp_path: Path) -> None:
    data = {"servers": {"trw": {"url": "http://localhost:8100", "type": "http"}}}
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    subtree = data["servers"]["trw"]
    expected = _sha256(json.dumps(subtree, sort_keys=True))
    result = detect_human_edit(
        mode=HumanEditDetection.KEY_NAMESPACE,
        target_path=target,
        expected_sha=expected,
    )
    assert result is False


def test_detect_human_edit_key_namespace_with_edit(tmp_path: Path) -> None:
    data = {"servers": {"trw": {"url": "http://localhost:9999"}}}
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    stale_sha = _sha256(json.dumps({"url": "http://localhost:8100"}, sort_keys=True))
    result = detect_human_edit(
        mode=HumanEditDetection.KEY_NAMESPACE,
        target_path=target,
        expected_sha=stale_sha,
    )
    assert result is True


# ---------------------------------------------------------------------------
# write_atomic — happy path
# ---------------------------------------------------------------------------


def test_write_atomic_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "output.md"
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)

    entry = write_atomic(target, "hello world", channel_id="ch1", render_log=rl)
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "hello world"
    assert entry.sha == _sha256("hello world")
    assert entry.bytes_written == len("hello world".encode("utf-8"))


def test_write_atomic_appends_to_log(tmp_path: Path) -> None:
    target = tmp_path / "output.md"
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)

    write_atomic(target, "content", channel_id="ch1", render_log=rl)
    result = rl.last_for("ch1", target)
    assert result is not None
    assert result.channel_id == "ch1"


def test_write_atomic_log_before_rename_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log entry must be written BEFORE os.rename is called."""
    target = tmp_path / "out.md"
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)

    log_written_before_rename = {"value": False}
    original_rename = os.rename

    def _tracked_rename(src: object, dst: object) -> None:
        # Check if log was already appended
        if log_path.exists():
            log_written_before_rename["value"] = True
        original_rename(src, dst)

    monkeypatch.setattr(os, "rename", _tracked_rename)
    write_atomic(target, "test content", channel_id="ch1", render_log=rl)
    assert log_written_before_rename["value"] is True


def test_write_atomic_crash_safety(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.rename crashes after log append, reconcile() must fix the log.

    After the crash:
    - Log has NEW sha entry
    - File on disk has OLD content

    reconcile() must append an entry with the ACTUAL file sha so the
    next detect_human_edit() finds a matching baseline.
    """
    target = tmp_path / "out.md"
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)

    # Write initial good state
    write_atomic(target, "original content", channel_id="ch1", render_log=rl)
    original_sha = _sha256("original content")

    # Now simulate a crash: log gets new sha, but rename is skipped
    original_rename = os.rename

    def _crash_rename(src: object, dst: object) -> None:
        # Delete temp file instead of renaming — simulates crash
        try:
            os.unlink(str(src))
        except OSError:
            pass
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "rename", _crash_rename)
    new_content = "new content after crash"
    with pytest.raises(OSError, match="simulated crash"):
        write_atomic(target, new_content, channel_id="ch1", render_log=rl)

    monkeypatch.setattr(os, "rename", original_rename)

    # State: log has SHA of "new content", file has "original content"
    last_entry = rl.last_for("ch1", target)
    assert last_entry is not None
    assert last_entry.sha == _sha256(new_content)  # log has wrong sha
    assert target.read_text(encoding="utf-8") == "original content"  # file unchanged

    # reconcile() should fix this
    reconcile(channel_id="ch1", target_path=target, render_log=rl)

    # Now last_for should return the ACTUAL sha
    fixed_entry = rl.last_for("ch1", target)
    assert fixed_entry is not None
    assert fixed_entry.sha == original_sha


# ---------------------------------------------------------------------------
# reconcile — SHA mismatch
# ---------------------------------------------------------------------------


def test_reconcile_no_log_entry_is_noop(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("content", encoding="utf-8")
    rl = RenderLog(tmp_path / "render-log.jsonl")
    # Should not raise — no entry to reconcile
    reconcile(channel_id="ch1", target_path=target, render_log=rl)


def test_reconcile_matching_sha_is_noop(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    content = "matching content"
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)

    write_atomic(target, content, channel_id="ch1", render_log=rl)
    lines_before = log_path.read_text().splitlines()

    reconcile(channel_id="ch1", target_path=target, render_log=rl)
    lines_after = log_path.read_text().splitlines()
    # No new lines should be added for matching sha
    assert len(lines_after) == len(lines_before)


def test_reconcile_missing_target_is_noop(tmp_path: Path) -> None:
    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)
    target = tmp_path / "nonexistent.md"
    rl.append(
        RenderLogEntry(
            channel_id="ch1",
            target_path=target,
            sha="some-sha",
            ts="ts",
            bytes_written=0,
        )
    )
    # Should not raise
    reconcile(channel_id="ch1", target_path=target, render_log=rl)


def test_reconcile_resets_mismatched_log_entry(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("actual file content", encoding="utf-8")
    actual_sha = _sha256("actual file content")

    log_path = tmp_path / "render-log.jsonl"
    rl = RenderLog(log_path)
    # Log has a WRONG sha
    rl.append(
        RenderLogEntry(
            channel_id="ch1",
            target_path=target,
            sha="wrong-sha-entirely",
            ts="ts",
            bytes_written=0,
        )
    )

    reconcile(channel_id="ch1", target_path=target, render_log=rl)

    # last_for should now return the actual sha
    result = rl.last_for("ch1", target)
    assert result is not None
    assert result.sha == actual_sha
