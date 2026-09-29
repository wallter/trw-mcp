"""The ``hint_delivery`` row of ``trw-mcp doctor`` (HINT-DELIVERY-CANARY).

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling for the
eLOC gate. Reports what the pre-edit hint's own PreToolUse hook actually
RECORDED, not what a model observed: it reads the newest
``hint_delivery_canary_window`` records under
``.trw/context/cc03-hints/*.json`` (written by
``trw_mcp.channels.claude_code._hook_helpers.write_hint_file``) and the age of
``.trw/runtime/post-commit-receipt.json`` (written by
``trw_mcp.tools._post_commit``). Both are read-only; this row writes nothing.

``scripts/distill_delivery_census.py`` is the full ad-hoc census tool (not
importable here: ``scripts/`` is outside the ``trw_mcp`` package boundary), so
the record-loading here is a narrow, doctor-scoped rewrite of the same idea:
tolerate an unreadable record instead of crashing, and bound the read to a
typed window instead of the whole directory.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from trw_mcp.models.config import TRWConfig

__all__ = ["HintRecordedMetrics", "hint_delivery_row", "hint_recorded_metrics"]

_HINTS_REL_DIR = Path(".trw") / "context" / "cc03-hints"
_RECEIPT_REL_PATH = Path(".trw") / "runtime" / "post-commit-receipt.json"

#: distill_status values whose row-level remedy names a specific next step
#: (brief HINT-DELIVERY-CANARY step 2). A status not listed here gets no
#: remedy line -- only the counted mix.
_REMEDIES: dict[str, str] = {
    "exception_fallback": (
        "run trw-mcp doctor on the MCP/daemon rows; a version-skewed interpreter or daemon was the cause on 2026-09-26"
    ),
    "timeout_fallback": "the hint exceeded its 2.4s budget; check host load",
    "sidecar_missing": "the distill sidecar is not built at HEAD; trw-distill self-improve refresh-sidecars --repo . builds one",
    "stale": "the distill sidecar is not built at HEAD; trw-distill self-improve refresh-sidecars --repo . builds one",
}

_FALLBACK_STATUSES = ("exception_fallback", "timeout_fallback")

#: A receipt from 8.2 S2b on records a rebuild REQUEST, not per-file counts, so the share is undefined.
_REBUILD_RECEIPT_REASON = (
    "post-commit requests a detached whole-repo sidecar rebuild (sidecar_rebuild={}); "
    "there is no per-file refresh to take a share of"
)


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:  # trw-fail-silent-allow: an unparseable ts is excluded from the ts range, not a crash
        return None
    # A timezone-free stamp is read as UTC so naive and aware stamps compare (never a TypeError FAIL).
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _load_window(hints_dir: Path, window: int) -> tuple[list[dict[str, Any]], int]:
    """Newest *window* records by mtime, plus how many of them failed to parse.

    An unreadable file still occupies a window slot (it is counted, not
    dropped and not a crash) so the window size reported always matches the
    number of files actually inspected.
    """
    if not hints_dir.is_dir():
        return [], 0
    try:
        candidates = sorted(hints_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:  # trw-fail-silent-allow: a directory listing race (file removed mid-scan) yields "no records"
        return [], 0
    newest = candidates[:window]
    records: list[dict[str, Any]] = []
    unreadable = 0
    for path in newest:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):  # trw-fail-silent-allow: a partially written record is expected
            unreadable += 1
            continue
        if not isinstance(record, dict):
            unreadable += 1
            continue
        records.append(record)
    return records, unreadable


def _ts_range(records: list[dict[str, Any]]) -> str:
    stamps = sorted(ts for ts in (_parse_ts(r.get("ts")) for r in records) if ts is not None)
    if not stamps:
        return "no parseable timestamps"
    return f"{stamps[0].isoformat()} .. {stamps[-1].isoformat()}"


def _analyze_window(hints_dir: Path, window: int) -> tuple[list[dict[str, Any]], int, Counter[str], Counter[str]]:
    """Parse and count one hint-record window: shared by the doctor row and
    :func:`hint_recorded_metrics` so both read the *same* counts.

    Returns ``(records, unreadable, tier_counts, distill_status_counts)``; an
    unreadable record is folded into ``distill_status_counts["unreadable"]`` so
    a caller summing status counts always recovers the window total.
    """
    records, unreadable = _load_window(hints_dir, window)
    tier_counts: Counter[str] = Counter(str(r.get("tier", "unknown")) for r in records)
    status_counts: Counter[str] = Counter(str(r.get("distill_status", "unknown")) for r in records)
    if unreadable:
        status_counts["unreadable"] += unreadable
    return records, unreadable, tier_counts, status_counts


def _receipt_metrics(target: Path) -> tuple[float | None, str | None, float | None, str | None]:
    """``(age_days, age_reason, sidecar_files_share, share_reason)`` for the post-commit receipt.

    ``sidecar_files_share`` is ``sidecar_files / sidecar_files_planned``. Each
    value is ``None`` with its own reason (never a fabricated 0) when the
    receipt is absent, unreadable, malformed, has no parseable ``ran_at``, or
    (for the share) ``sidecar_files_planned`` is not a positive number.
    """
    receipt_path = target / _RECEIPT_REL_PATH
    if not receipt_path.is_file():
        reason = f"no post-commit sidecar receipt at {_RECEIPT_REL_PATH}"
        return None, reason, None, reason
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:  # trw-fail-silent-allow: an unreadable receipt is reported, not fatal
        reason = f"post-commit sidecar receipt at {_RECEIPT_REL_PATH} is unreadable ({exc})"
        return None, reason, None, reason
    if not isinstance(receipt, dict):
        reason = f"post-commit sidecar receipt at {_RECEIPT_REL_PATH} is not a JSON object"
        return None, reason, None, reason

    ran_at = _parse_ts(receipt.get("ran_at"))
    age_days: float | None = None
    age_reason: str | None = None
    if ran_at is None:
        age_reason = "post-commit sidecar receipt has no parseable ran_at"
    else:
        age_days = (datetime.now(timezone.utc) - ran_at).total_seconds() / 86_400

    if "sidecar_rebuild" in receipt:
        # 8.2 S2b: post-commit requests one detached whole-repo build instead of a per-file refresh.
        return age_days, age_reason, None, _REBUILD_RECEIPT_REASON.format(receipt.get("sidecar_rebuild") or "none")
    files = receipt.get("sidecar_files")
    planned = receipt.get("sidecar_files_planned")
    share, share_reason = _sidecar_files_share(files, planned)
    return age_days, age_reason, share, share_reason


def _duration_percentile(records: list[dict[str, Any]], pct: float) -> float | None:
    """Nearest-rank percentile of ``duration_ms`` over records that recorded one.

    ``duration_ms`` is ``None``/absent on a provisional or pre-8.2-S3 record, so
    those are excluded rather than treated as 0 -- the same "absence is not a
    measurement of absence" rule the rest of this module follows.
    """
    values = sorted(
        float(v) for r in records if isinstance(v := r.get("duration_ms"), (int, float)) and not isinstance(v, bool)
    )
    if not values:
        return None
    idx = min(len(values) - 1, max(0, round(pct / 100 * (len(values) - 1))))
    return values[idx]


def _as_non_negative_int(value: object) -> int | None:
    # bool is an int subclass; a receipt field is never meant to be a bool.
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _sidecar_files_share(files: object, planned: object) -> tuple[float | None, str | None]:
    """``(sidecar_files / sidecar_files_planned, None)``, or ``(None, reason)`` naming the bad field.

    Both fields must be non-negative ints and ``planned`` must be positive
    (a zero or negative planned count, or a non-numeric value like ``"?"``,
    makes the ratio undefined rather than 0).
    """
    files_n = _as_non_negative_int(files)
    if files_n is None:
        return None, f"post-commit sidecar receipt sidecar_files is not a non-negative int ({files!r})"
    planned_n = _as_non_negative_int(planned)
    if planned_n is None or planned_n == 0:
        return None, f"post-commit sidecar receipt sidecar_files_planned is not a positive int ({planned!r})"
    return files_n / planned_n, None


@dataclass(frozen=True)
class HintRecordedMetrics:
    """Flat, typed ``hint_recorded`` soak metrics (schema ``trw.canary_soak/1``).

    Keys: ``hint_recorded.window_n``, ``.t0_share``, ``.t1_share``, ``.t2_share``,
    ``.fallback_share``, ``.t2_fresh_share``, ``.t2_stale_share``,
    ``.stale_changed_target_count``, ``.ts_min``/``.ts_max`` (ISO strings),
    ``.status.<distill_status>_count``; ``sidecar.commits_behind_median``;
    ``hint.duration_p50_ms``/``p95_ms``. "hint_recorded" means what the hook
    recorded, not model-visible delivery.

    Both the ``hint_delivery`` doctor row and the future distill-soak producer
    call :func:`hint_recorded_metrics` for this shared view, instead of each
    re-parsing ``.trw/context/cc03-hints/*.json``: ``metrics`` uses the
    schema's exact dotted key names so a producer can splat it straight into
    its JSONL row's ``metrics`` object. ``not_measured`` carries the reason a
    key could not be computed -- never a fabricated ``0`` (FRAMEWORK
    CONFIDENCE: absence of a measurement is not a measurement of absence).
    """

    metrics: dict[str, float | int | str | bool] = field(default_factory=dict)
    not_measured: dict[str, str] = field(default_factory=dict)


_NO_RECORDS_REASON = f"no hint records found under {_HINTS_REL_DIR}"
_NO_READABLE_RECORDS_REASON = "no readable hint records in window"
_NO_TIMESTAMPS_REASON = "no parseable timestamps in window"
_SHARE_KEYS = (
    "hint_recorded.t0_share",
    "hint_recorded.t1_share",
    "hint_recorded.t2_share",
    "hint_recorded.fallback_share",
)


def hint_recorded_metrics(repo_root: Path, window: int) -> HintRecordedMetrics:
    """Read-only ``hint_recorded`` / ``post_commit_receipt`` metrics for one *window*.

    Reads ``.trw/context/cc03-hints/*.json`` (newest ``window`` by mtime) and
    ``.trw/runtime/post-commit-receipt.json`` under *repo_root*. Never writes
    anything and never raises on a missing or malformed file -- those become
    ``not_measured`` entries instead.
    """
    metrics: dict[str, float | int | str | bool] = {}
    not_measured: dict[str, str] = {}

    metrics["distill_installed"] = importlib.util.find_spec("trw_distill") is not None

    hints_dir = repo_root / _HINTS_REL_DIR
    records, unreadable, tier_counts, status_counts = _analyze_window(hints_dir, window)
    total = len(records) + unreadable

    readable = len(records)

    if total == 0:
        for key in (
            "hint_recorded.window_n",
            *_SHARE_KEYS,
            "hint_recorded.ts_min",
            "hint_recorded.ts_max",
        ):
            not_measured[key] = _NO_RECORDS_REASON
    else:
        metrics["hint_recorded.window_n"] = total
        for name, count in status_counts.items():
            metrics[f"hint_recorded.status.{name}_count"] = count

        if readable == 0:
            # window_n counts every file inspected (readable or not), but a
            # share computed over zero readable records is undefined, not 0.
            for key in _SHARE_KEYS:
                not_measured[key] = _NO_READABLE_RECORDS_REASON
        else:
            metrics["hint_recorded.t0_share"] = tier_counts.get("T0", 0) / total
            metrics["hint_recorded.t1_share"] = tier_counts.get("T1", 0) / total
            metrics["hint_recorded.t2_share"] = tier_counts.get("T2", 0) / total
            fallback_count = sum(status_counts.get(s, 0) for s in _FALLBACK_STATUSES)
            metrics["hint_recorded.fallback_share"] = fallback_count / total

        stamps = sorted(ts for ts in (_parse_ts(r.get("ts")) for r in records) if ts is not None)
        if stamps:
            metrics["hint_recorded.ts_min"] = stamps[0].isoformat()
            metrics["hint_recorded.ts_max"] = stamps[-1].isoformat()
        else:
            not_measured["hint_recorded.ts_min"] = _NO_TIMESTAMPS_REASON
            not_measured["hint_recorded.ts_max"] = _NO_TIMESTAMPS_REASON

    age_days, age_reason, share, share_reason = _receipt_metrics(repo_root)
    if age_days is None:
        not_measured["post_commit_receipt.age_days"] = age_reason or "post-commit sidecar receipt age not available"
    else:
        metrics["post_commit_receipt.age_days"] = age_days
    if share is None:
        not_measured["post_commit_receipt.sidecar_files_share"] = (
            share_reason or "post-commit sidecar receipt share not available"
        )
    else:
        metrics["post_commit_receipt.sidecar_files_share"] = share

    # 8.2 S3: duration_ms is recorded per edit when the computation ran; older
    # records and provisional/never-computed ones have none, so the percentile
    # is computed only over records that measured it, never fabricated as 0.
    _no_duration_reason = "no window record carries a numeric duration_ms"
    p50 = _duration_percentile(records, 50)
    if p50 is None:
        not_measured["hint.duration_p50_ms"] = _no_duration_reason
    else:
        metrics["hint.duration_p50_ms"] = p50
    p95 = _duration_percentile(records, 95)
    if p95 is None:
        not_measured["hint.duration_p95_ms"] = _no_duration_reason
    else:
        metrics["hint.duration_p95_ms"] = p95

    return HintRecordedMetrics(metrics=metrics, not_measured=not_measured)


def _receipt_note(target: Path, config: TRWConfig, *, distill_installed: bool) -> tuple[str, bool]:
    """``(note, is_warn)`` for the post-commit sidecar receipt, or ("", False) when absent."""
    receipt_path = target / _RECEIPT_REL_PATH
    if not receipt_path.is_file():
        return f"no post-commit sidecar receipt at {_RECEIPT_REL_PATH}", False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:  # trw-fail-silent-allow: an unreadable receipt is reported, not fatal
        return f"post-commit sidecar receipt at {_RECEIPT_REL_PATH} is unreadable ({exc})", False
    if not isinstance(receipt, dict):
        return f"post-commit sidecar receipt at {_RECEIPT_REL_PATH} is not a JSON object", False

    ran_at = _parse_ts(receipt.get("ran_at"))
    if "sidecar_rebuild" in receipt:
        outcome = f"sidecar_rebuild={receipt.get('sidecar_rebuild') or 'none'}"
    else:
        files = receipt.get("sidecar_files", "?")
        planned = receipt.get("sidecar_files_planned", "?")
        outcome = f"sidecar_files={files}/{planned}, skipped_reason={receipt.get('sidecar_skipped_reason') or 'none'}"
    if ran_at is None:
        return f"post-commit sidecar receipt has no parseable ran_at ({outcome})", False
    age_days = (datetime.now(timezone.utc) - ran_at).total_seconds() / 86_400
    is_stale = distill_installed and age_days > config.hint_delivery_receipt_stale_days
    note = f"post-commit sidecar receipt ran {ran_at.isoformat()} ({age_days:.1f}d ago), {outcome}"
    if is_stale:
        note += (
            f" -- older than hint_delivery_receipt_stale_days ({config.hint_delivery_receipt_stale_days}d); "
            "the post-commit sidecar refresh has not run recently; check whether a sidecar exists for HEAD "
            "(trw-distill self-improve refresh-sidecars --repo . builds one)"
        )
    return note, is_stale


def hint_delivery_row(target: Path, config: TRWConfig) -> tuple[Literal["PASS", "WARN"], str]:
    """RECORDED (not model-observed) pre-edit hint tiers and fallback share over a bounded window.

    PASS when the window has fewer than 20 records ("not enough recent edits to
    judge"). Otherwise WARN when the fallback-status share meets
    ``hint_delivery_fallback_warn_share``, or when T2 is 0 while trw-distill is
    installed (absent trw-distill, a T2-less window is expected and never
    WARNs), or when the post-commit receipt is stale while trw-distill is
    installed. PASS otherwise. Never FAILs and never writes.
    """
    hints_dir = target / _HINTS_REL_DIR
    window = config.hint_delivery_canary_window
    records, unreadable, tier_counts, status_counts = _analyze_window(hints_dir, window)
    total = len(records) + unreadable

    distill_installed = importlib.util.find_spec("trw_distill") is not None
    receipt_note, receipt_warn = _receipt_note(target, config, distill_installed=distill_installed)

    knobs = (
        f"knobs: hint_delivery_canary_window={window}, "
        f"hint_delivery_fallback_warn_share={config.hint_delivery_fallback_warn_share}, "
        f"hint_delivery_receipt_stale_days={config.hint_delivery_receipt_stale_days}"
    )

    if total < 20:
        message = (
            f"not enough recent edits to judge: {total} recorded hint file(s) under {_HINTS_REL_DIR} "
            f"(need >= 20). {receipt_note}. {knobs}."
        )
        return ("WARN", message) if receipt_warn else ("PASS", message)

    fallback_count = sum(status_counts.get(s, 0) for s in _FALLBACK_STATUSES)
    fallback_share = fallback_count / total
    fallback_warn = fallback_share >= config.hint_delivery_fallback_warn_share
    t2_zero_warn = distill_installed and tier_counts.get("T2", 0) == 0

    top_statuses = ", ".join(f"{name}={count}" for name, count in status_counts.most_common(5))
    tier_mix = ", ".join(f"{tier}={tier_counts.get(tier, 0)}" for tier in ("T0", "T1", "T2"))

    remedies = [_REMEDIES[name] for name, _count in status_counts.most_common() if name in _REMEDIES]

    warn = fallback_warn or t2_zero_warn or receipt_warn
    status: Literal["PASS", "WARN"] = "WARN" if warn else "PASS"

    parts = [
        f"window={total} record(s) ({_ts_range(records)})",
        f"tier mix: {tier_mix}",
        f"distill_status: {top_statuses}",
        f"fallback share {fallback_share:.0%} (warn at {config.hint_delivery_fallback_warn_share:.0%})",
        receipt_note,
    ]
    if t2_zero_warn:
        parts.append("T2=0 while trw-distill is installed")
    if remedies:
        parts.append("remedy: " + "; ".join(remedies))
    parts.append(knobs)
    message = "; ".join(parts) + "."
    return status, message
