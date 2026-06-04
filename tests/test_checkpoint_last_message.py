"""Coverage for ``_read_last_checkpoint_message`` (PRD-CORE-165 FR-02).

Pre-compaction recovery must surface the REAL last checkpoint message (what the
next session reads to resume), not a hardcoded generic literal. These tests
verify the last-record extraction plus the safe fallback on missing / empty /
malformed ``checkpoints.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.tools.checkpoint import _read_last_checkpoint_message

_FALLBACK = "pre-compaction safety checkpoint"


def _write_checkpoints(run_dir: Path, *messages: str) -> None:
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"message": m, "timestamp": f"2026-05-29T00:0{i}:00Z"}) for i, m in enumerate(messages)]
    (meta / "checkpoints.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_returns_last_checkpoint_message(tmp_path: Path) -> None:
    _write_checkpoints(tmp_path, "first milestone", "second milestone", "latest progress")
    assert _read_last_checkpoint_message(tmp_path) == "latest progress"


def test_single_checkpoint_returned(tmp_path: Path) -> None:
    _write_checkpoints(tmp_path, "only one")
    assert _read_last_checkpoint_message(tmp_path) == "only one"


def test_missing_file_returns_fallback(tmp_path: Path) -> None:
    assert _read_last_checkpoint_message(tmp_path) == _FALLBACK


def test_empty_file_returns_fallback(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir(parents=True)
    (tmp_path / "meta" / "checkpoints.jsonl").write_text("\n   \n", encoding="utf-8")
    assert _read_last_checkpoint_message(tmp_path) == _FALLBACK


def test_blank_last_message_returns_fallback(tmp_path: Path) -> None:
    _write_checkpoints(tmp_path, "real message", "")
    assert _read_last_checkpoint_message(tmp_path) == _FALLBACK


def test_malformed_jsonl_returns_fallback(tmp_path: Path) -> None:
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)
    (meta / "checkpoints.jsonl").write_text('{"message": "good"}\n{not valid json\n', encoding="utf-8")
    assert _read_last_checkpoint_message(tmp_path) == _FALLBACK


def test_non_utf8_bytes_does_not_raise(tmp_path: Path) -> None:
    """Non-UTF-8 bytes in checkpoints.jsonl must not raise UnicodeDecodeError.

    Before the fix, ``checkpoints_path.read_text()`` ran OUTSIDE the try/except
    and a non-UTF-8 file crashed pre-compact recovery. With
    encoding='utf-8', errors='replace' the read succeeds; the undecodable bytes
    become the replacement char, so the (still valid) JSON record is surfaced
    with the replaced text rather than crashing. The key assertion is no raise.
    """
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)
    # Bare UTF-8 continuation bytes with no lead byte -> undecodable as strict UTF-8.
    (meta / "checkpoints.jsonl").write_bytes(b'{"message": "\x80\x81bad"}\n')
    result = _read_last_checkpoint_message(tmp_path)  # must not raise
    # Replacement happened (no raw bytes survived) and recovery degraded gracefully.
    assert "�" in result
    assert result.endswith("bad")


def test_undecodable_json_structure_returns_fallback(tmp_path: Path) -> None:
    """When replacement corrupts the JSON structure itself, degrade to fallback.

    Bad bytes inside the JSON syntax (not just a string value) make json.loads
    fail after replacement, so the except branch returns the safe fallback.
    """
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)
    # Bad bytes where a structural token is expected -> invalid JSON post-replace.
    (meta / "checkpoints.jsonl").write_bytes(b'{\x80\x81"message": "x"}\n')
    assert _read_last_checkpoint_message(tmp_path) == _FALLBACK


def test_non_utf8_bytes_after_valid_line_still_recovers_valid(tmp_path: Path) -> None:
    """errors='replace' keeps a valid LAST line readable even if earlier bytes are bad.

    Proves the fix is non-destructive: the last well-formed JSON record is still
    surfaced; only the undecodable bytes (on other lines) are replaced.
    """
    meta = tmp_path / "meta"
    meta.mkdir(parents=True)
    content = b'{"message": "\x80corrupt"}\n' + b'{"message": "recovered last"}\n'
    (meta / "checkpoints.jsonl").write_bytes(content)
    assert _read_last_checkpoint_message(tmp_path) == "recovered last"
