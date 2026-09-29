"""``hint_recorded_metrics`` (CANARY-SOAK-SCHEMA ``trw.canary_soak/1``).

The shared, pure metrics view both the ``hint_delivery`` doctor row and a
future distill-soak producer read from ``.trw/context/cc03-hints/*.json`` and
``.trw/runtime/post-commit-receipt.json``; the dotted key names it pins to are
listed in the ``HintRecordedMetrics`` docstring.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.server._doctor_hint_delivery import HintRecordedMetrics, hint_recorded_metrics

pytestmark = pytest.mark.unit

_HINTS_DIR = Path(".trw") / "context" / "cc03-hints"
_RECEIPT_PATH = Path(".trw") / "runtime" / "post-commit-receipt.json"

#: A monotonically increasing mtime counter so "newest by mtime" ordering is
#: deterministic regardless of filesystem mtime-resolution granularity.
_mtime_counter = itertools.count()


def _write_record(
    repo: Path,
    name: str,
    *,
    tier: str,
    distill_status: str,
    ts: datetime | None = None,
    duration_ms: float | None = None,
) -> None:
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
        "duration_ms": duration_ms,
        "sidecar_commits_behind": None,
        "target_changed_since_sidecar": None,
    }
    path = hints_dir / f"{name}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    mtime = next(_mtime_counter)
    os.utime(path, (mtime, mtime))


def _write_records(repo: Path, count: int, *, tier: str, distill_status: str, duration_ms: float | None = None) -> None:
    for i in range(count):
        _write_record(
            repo, f"rec-{tier}-{distill_status}-{i}", tier=tier, distill_status=distill_status, duration_ms=duration_ms
        )


def _write_receipt(repo: Path, *, ran_at: datetime, sidecar_files: int = 1, sidecar_files_planned: int = 2) -> None:
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


def _no_distill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "trw_distill" else object())


def _has_distill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())


# ── keys match the schema ────────────────────────────────────────────────


def test_keys_match_the_canary_soak_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 15, tier="T0", distill_status="delivered")
    _write_records(tmp_path, 5, tier="T2", distill_status="hint_available")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc))

    result = hint_recorded_metrics(tmp_path, window=200)

    assert isinstance(result, HintRecordedMetrics)
    assert result.metrics["distill_installed"] is True
    for key in (
        "hint_recorded.window_n",
        "hint_recorded.t0_share",
        "hint_recorded.t1_share",
        "hint_recorded.t2_share",
        "hint_recorded.fallback_share",
        "hint_recorded.ts_min",
        "hint_recorded.ts_max",
        "post_commit_receipt.age_days",
        "post_commit_receipt.sidecar_files_share",
    ):
        assert key in result.metrics, key
    assert "hint_recorded.status.delivered_count" in result.metrics
    assert "hint_recorded.status.hint_available_count" in result.metrics
    assert result.metrics["hint_recorded.window_n"] == 20
    assert result.metrics["hint_recorded.t0_share"] == pytest.approx(0.75)
    assert result.metrics["hint_recorded.t2_share"] == pytest.approx(0.25)


def test_duration_keys_not_measured_when_no_record_carries_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A window of records with no ``duration_ms`` never fabricates a p50/p95 of 0."""
    _no_distill(monkeypatch)
    _write_records(tmp_path, 25, tier="T0", distill_status="delivered")

    result = hint_recorded_metrics(tmp_path, window=200)

    assert result.not_measured["hint.duration_p50_ms"] == "no window record carries a numeric duration_ms"
    assert result.not_measured["hint.duration_p95_ms"] == "no window record carries a numeric duration_ms"
    assert "hint.duration_p50_ms" not in result.metrics
    assert "hint.duration_p95_ms" not in result.metrics


def test_duration_percentiles_computed_when_records_carry_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """8.2 S3: duration_ms is measured in-process by the hook; the metrics view
    reports p50/p95 over just the records that carry it."""
    _no_distill(monkeypatch)
    for i in range(10):
        _write_record(tmp_path, f"dur-{i}", tier="T2", distill_status="hint_available", duration_ms=float(100 + i * 10))

    result = hint_recorded_metrics(tmp_path, window=200)

    assert "hint.duration_p50_ms" in result.metrics
    assert "hint.duration_p95_ms" in result.metrics
    assert result.metrics["hint.duration_p50_ms"] == pytest.approx(140.0)
    assert result.metrics["hint.duration_p95_ms"] >= result.metrics["hint.duration_p50_ms"]
    assert "hint.duration_p50_ms" not in result.not_measured
    assert "hint.duration_p95_ms" not in result.not_measured


def test_duration_percentiles_skip_records_missing_it_never_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mixed window (some records pre-date duration_ms) computes the percentile
    over the measured subset only -- an absent value is never treated as 0."""
    _no_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T0", distill_status="delivered")  # duration_ms=None
    _write_record(tmp_path, "dur-only", tier="T2", distill_status="hint_available", duration_ms=42.0)

    result = hint_recorded_metrics(tmp_path, window=200)

    assert result.metrics["hint.duration_p50_ms"] == pytest.approx(42.0)
    assert result.metrics["hint.duration_p95_ms"] == pytest.approx(42.0)


# ── every share has its _n ───────────────────────────────────────────────


def test_every_share_has_its_window_n_companion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 10, tier="T0", distill_status="delivered")
    _write_records(tmp_path, 10, tier="T1", distill_status="delivered")

    result = hint_recorded_metrics(tmp_path, window=200)

    share_keys = [k for k in result.metrics if k.endswith("_share") and k.startswith("hint_recorded.")]
    assert share_keys, "expected at least one hint_recorded.*_share key"
    for key in share_keys:
        assert "hint_recorded.window_n" in result.metrics, f"{key} has no window_n denominator"


# ── empty or missing dir gives not_measured, never 0 ─────────────────────


def test_missing_hints_dir_reports_not_measured_never_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)

    result = hint_recorded_metrics(tmp_path, window=200)

    for key in (
        "hint_recorded.window_n",
        "hint_recorded.t0_share",
        "hint_recorded.t1_share",
        "hint_recorded.t2_share",
        "hint_recorded.fallback_share",
        "hint_recorded.ts_min",
        "hint_recorded.ts_max",
    ):
        assert key not in result.metrics, f"{key} must not be fabricated as 0/absent"
        assert key in result.not_measured
        assert result.not_measured[key]


def test_empty_hints_dir_reports_not_measured_never_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    (tmp_path / _HINTS_DIR).mkdir(parents=True)

    result = hint_recorded_metrics(tmp_path, window=200)

    assert "hint_recorded.window_n" not in result.metrics
    assert result.not_measured["hint_recorded.window_n"]
    assert "hint_recorded.t0_share" not in result.metrics
    assert result.not_measured["hint_recorded.t0_share"]


def test_missing_receipt_reports_not_measured_never_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 25, tier="T0", distill_status="delivered")

    result = hint_recorded_metrics(tmp_path, window=200)

    assert "post_commit_receipt.age_days" not in result.metrics
    assert "post_commit_receipt.sidecar_files_share" not in result.metrics
    assert result.not_measured["post_commit_receipt.age_days"]
    assert result.not_measured["post_commit_receipt.sidecar_files_share"]


def test_unparseable_timestamps_report_not_measured_never_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    hints_dir = tmp_path / _HINTS_DIR
    hints_dir.mkdir(parents=True)
    for i in range(25):
        path = hints_dir / f"rec-{i}.json"
        path.write_text(
            json.dumps({"ts": "not-a-timestamp", "tier": "T0", "distill_status": "delivered"}), encoding="utf-8"
        )
        os.utime(path, (i, i))

    result = hint_recorded_metrics(tmp_path, window=200)

    assert result.metrics["hint_recorded.window_n"] == 25
    assert "hint_recorded.ts_min" not in result.metrics
    assert "hint_recorded.ts_max" not in result.metrics
    assert result.not_measured["hint_recorded.ts_min"]
    assert result.not_measured["hint_recorded.ts_max"]


# ── an unreadable record is counted ──────────────────────────────────────


def test_unreadable_record_is_counted_in_window_n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_distill(monkeypatch)
    _write_records(tmp_path, 24, tier="T0", distill_status="delivered")
    hints_dir = tmp_path / _HINTS_DIR
    (hints_dir / "corrupt.json").write_text("{not json", encoding="utf-8")

    result = hint_recorded_metrics(tmp_path, window=200)

    assert result.metrics["hint_recorded.window_n"] == 25
    assert result.metrics["hint_recorded.status.unreadable_count"] == 1


def test_unreadable_receipt_reports_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    receipt_path = tmp_path / _RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_bytes(b"\xff\xfe not valid json or utf-8")

    result = hint_recorded_metrics(tmp_path, window=200)

    assert "post_commit_receipt.age_days" not in result.metrics
    assert result.not_measured["post_commit_receipt.age_days"]


# ── KI1: shares never fabricate 0 when every record is unreadable ────────


def test_all_unreadable_window_reports_shares_not_measured_but_counts_window_n(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """readable n = 0: shares must be not_measured, never 0.0; window_n still counts the files."""
    _no_distill(monkeypatch)
    hints_dir = tmp_path / _HINTS_DIR
    hints_dir.mkdir(parents=True)
    for i in range(25):
        (hints_dir / f"corrupt-{i}.json").write_text("{not json", encoding="utf-8")

    result = hint_recorded_metrics(tmp_path, window=200)

    assert result.metrics["hint_recorded.window_n"] == 25
    assert result.metrics["hint_recorded.status.unreadable_count"] == 25
    for key in (
        "hint_recorded.t0_share",
        "hint_recorded.t1_share",
        "hint_recorded.t2_share",
        "hint_recorded.fallback_share",
    ):
        assert key not in result.metrics, f"{key} must not be fabricated as 0.0"
        assert result.not_measured[key] == "no readable hint records in window"


def test_partially_unreadable_window_still_computes_shares(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """At least one readable record: shares ARE measured (over window_n, not readable_n alone)."""
    _no_distill(monkeypatch)
    _write_records(tmp_path, 24, tier="T0", distill_status="delivered")
    (tmp_path / _HINTS_DIR / "corrupt.json").write_text("{not json", encoding="utf-8")

    result = hint_recorded_metrics(tmp_path, window=200)

    assert result.metrics["hint_recorded.window_n"] == 25
    assert "hint_recorded.t0_share" in result.metrics
    assert result.metrics["hint_recorded.t0_share"] == pytest.approx(24 / 25)


# ── KI2: sidecar_files_share never fabricates 0 for a bad receipt field ───


@pytest.mark.parametrize(
    "sidecar_files,sidecar_files_planned",
    [
        ("?", 2),
        (1, "?"),
        (-1, 2),
        (1, -1),
        (1, 0),
    ],
)
def test_bad_sidecar_fields_report_share_not_measured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sidecar_files: object,
    sidecar_files_planned: object,
) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    receipt_path = tmp_path / _RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "sidecar_files": sidecar_files,
                "sidecar_files_planned": sidecar_files_planned,
                "sidecar_skipped_reason": "",
                "head_sha": "deadbeef",
            }
        ),
        encoding="utf-8",
    )

    result = hint_recorded_metrics(tmp_path, window=200)

    assert "post_commit_receipt.sidecar_files_share" not in result.metrics
    reason = result.not_measured["post_commit_receipt.sidecar_files_share"]
    assert "sidecar_files" in reason
    # age_days is independent of the sidecar_files/_planned fields and stays measured.
    assert "post_commit_receipt.age_days" in result.metrics


def test_valid_sidecar_fields_compute_the_share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc), sidecar_files=1, sidecar_files_planned=2)

    result = hint_recorded_metrics(tmp_path, window=200)

    assert result.metrics["post_commit_receipt.sidecar_files_share"] == pytest.approx(0.5)
    assert "post_commit_receipt.sidecar_files_share" not in result.not_measured


# ── it never writes ──────────────────────────────────────────────────────


def test_hint_recorded_metrics_never_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _has_distill(monkeypatch)
    _write_records(tmp_path, 20, tier="T2", distill_status="delivered")
    _write_receipt(tmp_path, ran_at=datetime.now(timezone.utc) - timedelta(days=1))
    hints_dir = tmp_path / _HINTS_DIR
    receipt_dir = tmp_path / _RECEIPT_PATH.parent
    before_hints = {p: p.stat().st_mtime for p in hints_dir.glob("*.json")}
    before_receipt = (receipt_dir / _RECEIPT_PATH.name).read_text(encoding="utf-8")
    before_files = set(hints_dir.glob("*.json")) | set(receipt_dir.glob("*"))

    hint_recorded_metrics(tmp_path, window=200)

    after_hints = {p: p.stat().st_mtime for p in hints_dir.glob("*.json")}
    after_receipt = (receipt_dir / _RECEIPT_PATH.name).read_text(encoding="utf-8")
    after_files = set(hints_dir.glob("*.json")) | set(receipt_dir.glob("*"))
    assert before_hints == after_hints
    assert before_receipt == after_receipt
    assert before_files == after_files
