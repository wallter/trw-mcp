"""HINT-DELIVERY-CANARY: the ``hint_delivery`` row of ``trw-mcp doctor``.

Reports what the pre-edit hint's PreToolUse hook actually RECORDED (the
``.trw/context/cc03-hints/*.json`` files written by
``trw_mcp.channels.claude_code._hook_helpers.write_hint_file``) over a bounded
window, plus the age of the post-commit sidecar receipt. Every case goes
through the public doctor entry point, ``_doctor_core`` /
``_check_hint_delivery``, per FAST-RULES UPDATE 2 (wired, not just
implemented).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.server._subcommands_doctor import _CHECKS, CheckResult, _check_hint_delivery, _doctor_core

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("stub_cli_version_probes")]

_HINTS_DIR = Path(".trw") / "context" / "cc03-hints"
_RECEIPT_PATH = Path(".trw") / "runtime" / "post-commit-receipt.json"

#: A monotonically increasing mtime counter so "newest by mtime" ordering is
#: deterministic regardless of filesystem mtime-resolution granularity.
_mtime_counter = itertools.count()


def _write_record(repo: Path, name: str, *, tier: str, distill_status: str, ts: datetime | None = None) -> None:
    hints_dir = repo / _HINTS_DIR
    hints_dir.mkdir(parents=True, exist_ok=True)
    ts = ts or datetime.now(timezone.utc)
    record = {
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "file_path": f"{name}.py",
        "tier": tier,
        "hint_emitted": tier != "T0",
        "tokens_emitted": 0,
        "distill_status": distill_status,
        "tool_use_id": name,
        "outcome_captured": False,
        "was_edited": None,
        "edit_survived": None,
        "test_outcome": "unknown",
        "hint_acknowledged": None,
    }
    path = hints_dir / f"{name}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    mtime = next(_mtime_counter)
    os.utime(path, (mtime, mtime))


def _write_records(repo: Path, count: int, *, tier: str, distill_status: str) -> None:
    for i in range(count):
        _write_record(repo, f"rec-{tier}-{distill_status}-{i}", tier=tier, distill_status=distill_status)


def _no_distill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "trw_distill" else object())


def _has_distill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())


def test_row_is_appended_after_hint_hub_downrank() -> None:
    names = [name for name, _ in _CHECKS]
    assert ("hint_delivery", "_check_hint_delivery") in _CHECKS
    assert names.index("hint_delivery") > names.index("hint_hub_downrank")


# ── PASS: too few records ────────────────────────────────────────────────────


def test_pass_when_fewer_than_twenty_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 5, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"
    assert "not enough recent edits to judge" in result.message


def test_pass_when_no_hints_directory_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"
    assert "not enough recent edits to judge" in result.message


# ── WARN: fallback-dominated window ──────────────────────────────────────────


def test_warn_on_fallback_dominated_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 15, tier="T0", distill_status="exception_fallback")
    _write_records(tmp_path, 10, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "WARN"
    assert "fallback share" in result.message
    assert "exception_fallback" in result.message
    # Named remedy per the brief's mapping.
    assert "version-skewed interpreter or daemon" in result.message


def test_pass_when_fallback_share_below_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 5, tier="T0", distill_status="exception_fallback")
    _write_records(tmp_path, 20, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"


def test_timeout_fallback_names_its_own_remedy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 15, tier="T0", distill_status="timeout_fallback")
    _write_records(tmp_path, 10, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "WARN"
    assert "2.4s budget" in result.message


# ── WARN: T2=0 only when trw-distill is installed ────────────────────────────


def test_t2_zero_does_not_warn_when_distill_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 25, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"


def test_t2_zero_warns_when_distill_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 25, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "WARN"
    assert "T2=0" in result.message


def test_no_warn_when_distill_installed_and_t2_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    _write_records(tmp_path, 5, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"


# ── knobs ─────────────────────────────────────────────────────────────────


def test_window_knob_bounds_how_many_records_are_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny window sees only the newest N files, so a large fallback pile outside the
    window does not tip the window that is actually inspected."""
    _no_distill(monkeypatch)
    _write_records(tmp_path, 100, tier="T0", distill_status="exception_fallback")
    # Touch the delivered records last so they are the mtime-newest.
    _write_records(tmp_path, 20, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig(hint_delivery_canary_window=20))

    assert result.status == "PASS"
    assert "window=20" in result.message
    assert "hint_delivery_canary_window=20" in result.message


def test_fallback_share_warn_knob_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 5, tier="T0", distill_status="exception_fallback")
    _write_records(tmp_path, 20, tier="T0", distill_status="delivered")

    # 5/25 = 0.2 fallback share; default threshold 0.5 would PASS, a low
    # threshold must WARN.
    result = _check_hint_delivery(tmp_path, TRWConfig(hint_delivery_fallback_warn_share=0.1))

    assert result.status == "WARN"
    assert "hint_delivery_fallback_warn_share=0.1" in result.message


# ── receipt age ───────────────────────────────────────────────────────────


def _write_receipt(repo: Path, *, ran_at: datetime, sidecar_files: int = 0, sidecar_files_planned: int = 2) -> None:
    receipt_path = repo / _RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(
            {
                "ran_at": ran_at.isoformat().replace("+00:00", "Z"),
                "sidecar_files": sidecar_files,
                "sidecar_files_planned": sidecar_files_planned,
                "sidecar_skipped_reason": "",
                "head_sha": "deadbeef",
            }
        ),
        encoding="utf-8",
    )


def test_receipt_absent_is_reported_without_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 25, tier="T0", distill_status="delivered")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"
    assert "no post-commit sidecar receipt" in result.message


def test_receipt_age_warns_when_stale_and_distill_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc) - timedelta(days=10))

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "WARN"
    assert "sidecar_files=0/2" in result.message
    assert "trw-distill self-improve refresh-sidecars --repo ." in result.message


def test_a_rebuild_request_receipt_reports_its_outcome_not_a_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8.2 S2b receipts carry sidecar_rebuild; the per-file share is not_measured with a reason naming it."""
    from trw_mcp.server._doctor_hint_delivery import hint_recorded_metrics

    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    receipt_path = tmp_path / _RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    ran_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt_path.write_text(json.dumps({"ran_at": ran_at, "sidecar_rebuild": "min_interval"}), encoding="utf-8")

    result = _check_hint_delivery(tmp_path, TRWConfig())
    metrics = hint_recorded_metrics(tmp_path, 200)

    assert "sidecar_rebuild=min_interval" in result.message
    assert "sidecar_files" not in result.message
    assert "post_commit_receipt.sidecar_files_share" not in metrics.metrics
    assert "sidecar_rebuild=min_interval" in metrics.not_measured["post_commit_receipt.sidecar_files_share"]
    assert "post_commit_receipt.age_days" in metrics.metrics


def test_receipt_age_does_not_warn_when_distill_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 25, tier="T0", distill_status="delivered")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc) - timedelta(days=10))

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"


def test_receipt_age_knob_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc) - timedelta(days=3))

    # 3 days old: default 7-day threshold PASSes, a 1-day threshold must WARN.
    result = _check_hint_delivery(tmp_path, TRWConfig(hint_delivery_receipt_stale_days=1))

    assert result.status == "WARN"
    assert "hint_delivery_receipt_stale_days=1" in result.message


# ── unreadable record ────────────────────────────────────────────────────


def test_unreadable_record_is_counted_not_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 24, tier="T0", distill_status="delivered")
    hints_dir = tmp_path / _HINTS_DIR
    (hints_dir / "corrupt.json").write_text("{not json", encoding="utf-8")

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status == "PASS"
    assert "unreadable" in result.message
    assert "window=25" in result.message


# ── never writes ─────────────────────────────────────────────────────────


def test_row_never_writes_anything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 25, tier="T0", distill_status="delivered")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc))
    hints_dir = tmp_path / _HINTS_DIR
    receipt_dir = tmp_path / _RECEIPT_PATH.parent
    before_hints = {p: p.stat().st_mtime for p in hints_dir.glob("*.json")}
    before_receipt = (receipt_dir / _RECEIPT_PATH.name).read_text(encoding="utf-8")

    _check_hint_delivery(tmp_path, TRWConfig())

    after_hints = {p: p.stat().st_mtime for p in hints_dir.glob("*.json")}
    after_receipt = (receipt_dir / _RECEIPT_PATH.name).read_text(encoding="utf-8")
    assert before_hints == after_hints
    assert before_receipt == after_receipt
    assert set(hints_dir.glob("*.json")) == {hints_dir / p.name for p in before_hints}


# ── through the full doctor run ──────────────────────────────────────────


def test_full_doctor_run_surfaces_the_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 15, tier="T0", distill_status="exception_fallback")
    _write_records(tmp_path, 10, tier="T0", distill_status="delivered")

    results = _doctor_core(tmp_path, TRWConfig())

    rows = {r.name: r for r in cast("list[CheckResult]", results)}
    assert "hint_delivery" in rows
    assert rows["hint_delivery"].status == "WARN"


# ── merge-review KIs: malformed telemetry never FAILs; receipt age never asserts a missing sidecar ──


def test_naive_mixed_timestamps_and_invalid_utf8_never_fail_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    _write_record(tmp_path, "naive", tier="T2", distill_status="delivered", ts=datetime(2026, 9, 26, 12, 0))  # noqa: DTZ001 -- deliberately timezone-free input under test
    (tmp_path / _HINTS_DIR / "garbled.json").write_bytes(b"\xff\xfe not utf-8")
    _write_receipt(tmp_path, ran_at=datetime(2026, 9, 26, 12, 0))  # noqa: DTZ001 -- deliberately timezone-free ran_at under test

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert result.status in {"PASS", "WARN"}
    assert "unreadable=1" in result.message


def test_old_successful_receipt_does_not_claim_the_sidecar_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc) - timedelta(days=10), sidecar_files=2)

    result = _check_hint_delivery(tmp_path, TRWConfig())

    assert "not built at HEAD" not in result.message
    assert "has not run recently" in result.message
