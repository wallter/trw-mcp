"""PRD-FIX-085 FR04: JSONL rotation parity for recall_tracking,
propensity, and deferred-deliver logs.

Pre-fix: surface_tracking.jsonl rotates at 10 MB; recall_tracking,
propensity, and deferred-deliver did not. Observed 52 MB / 9 MB / 25 MB
respectively on the dev repo with no rotation.

Post-fix: all four call rotate_jsonl(max_bytes=10*1024*1024).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _seed_jsonl_over_threshold(path: Path, target_mb: float = 11) -> int:
    """Write enough JSONL lines to exceed the rotation threshold."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = '{"x": "' + "y" * 1024 + '"}\n'  # ~1 KB per line
    needed = int(target_mb * 1024)  # number of 1 KB lines
    with path.open("w", encoding="utf-8") as f:
        for _ in range(needed):
            f.write(payload)
    return path.stat().st_size


def test_recall_tracking_rotates_when_oversized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_recall() rotates recall_tracking.jsonl when over 10 MB."""
    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "recall_tracking.jsonl"
    pre_size = _seed_jsonl_over_threshold(log_path)
    assert pre_size > 10 * 1024 * 1024

    monkeypatch.setattr("trw_mcp.state.recall_tracking.resolve_trw_dir", lambda: trw_dir)

    from trw_mcp.state.recall_tracking import record_recall

    assert record_recall("L-rotate-probe", query="probe") is True

    # File rotated: original moved to .1, new file is small.
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists(), "recall_tracking.jsonl.1 should exist after rotation"
    assert log_path.stat().st_size < pre_size, "fresh recall_tracking.jsonl is small"


def test_recall_tracking_no_rotation_when_under_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_recall() does NOT rotate when under 10 MB."""
    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "recall_tracking.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text('{"existing": "row"}\n', encoding="utf-8")

    monkeypatch.setattr("trw_mcp.state.recall_tracking.resolve_trw_dir", lambda: trw_dir)

    from trw_mcp.state.recall_tracking import record_recall

    assert record_recall("L-no-rotate", query="probe") is True
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert not rotated.exists()


def test_propensity_already_rotates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """propensity_log already had rotation pre-fix; verify it still works."""
    log_path = tmp_path / ".trw" / "logs" / "propensity.jsonl"
    pre_size = _seed_jsonl_over_threshold(log_path)
    assert pre_size > 10 * 1024 * 1024

    from trw_mcp.state.propensity_log import _rotate_jsonl

    _rotate_jsonl(log_path)
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists()


def test_deferred_deliver_log_rotates_when_oversized(tmp_path: Path) -> None:
    """_log_deferred_result() rotates deferred-deliver.jsonl when over 10 MB."""
    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    pre_size = _seed_jsonl_over_threshold(log_path)
    assert pre_size > 10 * 1024 * 1024

    from trw_mcp.tools._deferred_delivery import _log_deferred_result

    _log_deferred_result(trw_dir, {"foo": "bar"}, errors=[])

    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists(), "deferred-deliver.jsonl.1 should exist after rotation"
    assert log_path.stat().st_size < pre_size
