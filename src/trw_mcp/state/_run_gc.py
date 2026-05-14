"""Stale-run sweep — ``sweep_stale_runs`` (PRD-CORE-141 FR09/FR10).

Parent facade: :mod:`trw_mcp.state._paths` (imports :func:`sweep_stale_runs`
at the boot-sequence integration point in ``server/_cli.py::_boot_sequence``).

This module is intentionally independent of pin logic — the caller owns the
job of assembling the ``pinned_paths`` set (typically from
:func:`trw_mcp.state._pin_store.load_pin_store`) and passes it in.  That keeps
the sweep logic unit-testable without a live pin store and keeps the boot
sequence the single integration point between pins and stale-run GC.

Staleness formula (authoritative — MUST match PRD-CORE-141 §FR09)::

    last_activity = max(
        events.jsonl.mtime,
        run.yaml.mtime,
        checkpoints.jsonl.mtime,
        meta/heartbeat.mtime,
    )

A missing file contributes ``0.0`` so it does not drive the ``max``.  The
heartbeat file is load-bearing: :class:`trw_mcp.middleware.ceremony`
``_touch_heartbeat_safe`` updates it on every tool call, providing a liveness
signal for runs that are actively accessed but do not append to events.jsonl
(e.g. ``trw_status``, ``trw_recall``).

A run is marked ``abandoned`` (status rewritten in place) iff ALL of:

* ``run.yaml.status == "active"``
* ``now - last_activity > staleness_hours * 3600``
* ``run_path NOT in pinned_paths``
* ``run.yaml.protected is not True``

Grace window: runs whose ``last_activity`` falls inside
``(now - (staleness_hours + grace_hours) * 3600, now - staleness_hours * 3600]``
log ``run_near_stale_warning`` but are NOT abandoned and appear in
``near_stale_run_ids``.

NFR02 fail-open: every abandonment is a best-effort operation.  A malformed
``run.yaml`` or an events.jsonl append failure is logged and counted; the
sweep MUST NOT crash partway through.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.state._paths import iter_run_dirs
from trw_mcp.state._run_gc_io import (
    _append_event_best_effort,
    _dump_run_yaml_atomic,
    _load_run_yaml,
    _prefilter_protected,
)
from trw_mcp.state._run_gc_io import (
    _prefilter_status as _prefilter_status,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "StaleRunReport",
    "compute_last_activity",
    "sweep_stale_runs",
]


# Terminal statuses are skipped outright — their audit trail is sealed.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"complete", "failed", "delivered", "abandoned"},
)


@dataclass(frozen=True)
class StaleRunReport:
    """Report returned by :func:`sweep_stale_runs`.

    Every counter field is non-negative; list fields are sorted by run-id
    lexicographically so the report is deterministic across runs.
    """

    runs_scanned: int = 0
    runs_abandoned: int = 0
    runs_preserved_pinned: int = 0
    runs_preserved_protected: int = 0
    runs_in_grace_window: int = 0
    runs_skipped_terminal: int = 0
    runs_skipped_malformed: int = 0
    abandoned_run_ids: list[str] = field(default_factory=list)
    near_stale_run_ids: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


def _file_mtime(path: Path) -> float:
    """Return the mtime of *path* or 0.0 if it does not exist."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def compute_last_activity(run_dir: Path) -> float:
    """Return the ``max`` mtime across the four liveness files.

    Files are (in the order spelled out in the PRD):

    1. ``meta/events.jsonl``
    2. ``meta/run.yaml``
    3. ``meta/checkpoints.jsonl``
    4. ``meta/heartbeat`` — CRITICAL; omitting this causes false-abandon
       of runs whose tool calls never append to events.jsonl.

    Missing files contribute ``0.0``.
    """
    meta = run_dir / "meta"
    return max(
        _file_mtime(meta / "events.jsonl"),
        _file_mtime(meta / "run.yaml"),
        _file_mtime(meta / "checkpoints.jsonl"),
        _file_mtime(meta / "heartbeat"),
    )


def _normalize_pinned_paths(pinned_paths: Iterable[Path]) -> set[str]:
    """Resolve each pinned path to its ``str(resolve())`` form for set membership.

    Pin store entries store resolved absolute paths; sweep run_dirs from
    ``iter_run_dirs`` are absolute under the configured ``runs_root``.  We
    normalize both to resolved strings to avoid trailing-slash / relative-path
    mismatches on edge cases.
    """
    normalized: set[str] = set()
    for p in pinned_paths:
        try:
            normalized.add(str(Path(p).resolve()))
        except (OSError, RuntimeError):
            normalized.add(str(p))
    return normalized


def sweep_stale_runs(
    runs_root: Path,
    staleness_hours: int,
    grace_hours: int,
    pinned_paths: Iterable[Path],
    *,
    dry_run: bool = False,
    _now: float | None = None,
) -> StaleRunReport:
    """Scan *runs_root* and mark stale ``active`` runs as ``abandoned``.

    Args:
        runs_root: Root directory containing ``{task}/{run_id}/meta/run.yaml``.
            Scanned via :func:`trw_mcp.state._paths.iter_run_dirs`.
        staleness_hours: Runs whose last_activity is older than this are
            candidates for abandonment.
        grace_hours: Additional window beyond ``staleness_hours`` during which
            a run is preserved but emits ``run_near_stale_warning``.
        pinned_paths: Iterable of live-pin ``run_path`` values — these are
            preserved regardless of age.  Caller is responsible for filtering
            to pins that are actually alive (heartbeat within TTL + pid alive).
        dry_run: When True, compute the report but do NOT mutate run.yaml or
            append events.  Used by ``trw-mcp gc --dry-run``.
        _now: Optional monotonic-seconds override for deterministic tests.

    Returns:
        :class:`StaleRunReport` with counts, id lists (sorted), and duration.
    """
    start = time.monotonic()
    now = _now if _now is not None else time.time()
    staleness_cutoff = now - (staleness_hours * 3600)
    grace_cutoff = now - ((staleness_hours + grace_hours) * 3600)

    pinned = _normalize_pinned_paths(pinned_paths)

    runs_scanned = 0
    runs_abandoned = 0
    runs_preserved_pinned = 0
    runs_preserved_protected = 0
    runs_in_grace_window = 0
    runs_skipped_terminal = 0
    runs_skipped_malformed = 0
    abandoned_ids: list[str] = []
    near_stale_ids: list[str] = []

    if not runs_root.is_dir():
        # Nothing to scan — return an empty report so the caller prints a
        # stable "0 runs scanned" message rather than blowing up.
        duration_ms = (time.monotonic() - start) * 1000.0
        logger.info(
            "boot_gc_complete",
            runs_scanned=0,
            runs_abandoned=0,
            runs_preserved_pinned=0,
            runs_preserved_protected=0,
            runs_in_grace_window=0,
            runs_skipped_terminal=0,
            runs_skipped_malformed=0,
            duration_ms=duration_ms,
            dry_run=dry_run,
            runs_root=str(runs_root),
        )
        return StaleRunReport(duration_ms=duration_ms)

    for run_dir, run_yaml_path in iter_run_dirs(runs_root):
        runs_scanned += 1
        run_id = run_dir.name

        # Fast path: regex-prefilter status + protected from the file
        # header so we can short-circuit without invoking the YAML parser.
        # A handful of legacy run.yaml files have ballooned to several MB
        # from accumulated audit_pattern_promotions arrays — parsing those
        # with ruamel rt-mode takes seconds per file and dominates boot
        # time. Lazy-load YAML only when we actually need to mutate.
        prefilter_status = _prefilter_status(run_yaml_path)
        if prefilter_status in _TERMINAL_STATUSES:
            runs_skipped_terminal += 1
            logger.debug(
                "sweep_skipped_terminal",
                run_id=run_id,
                status=prefilter_status,
                prefilter=True,
            )
            continue

        # If the prefilter resolved status to anything other than "active",
        # treat it as terminal/unknown without a YAML load. The authoritative
        # parser only runs when status was unparseable from the header.
        prefilter_protected: bool | None = None
        if prefilter_status == "active":
            data: dict[str, Any] | None = None
            status = "active"
            prefilter_protected = _prefilter_protected(run_yaml_path)
        elif prefilter_status is not None:
            # Unknown/unsupported status — conservative: treat as terminal.
            runs_skipped_terminal += 1
            logger.debug(
                "sweep_skipped_terminal",
                run_id=run_id,
                status=prefilter_status or "unknown",
                prefilter=True,
            )
            continue
        else:
            # Prefilter ambiguous — fall back to authoritative parse.
            data = _load_run_yaml(run_yaml_path)
            if data is None:
                runs_skipped_malformed += 1
                logger.warning(
                    "sweep_skipped_malformed",
                    run_id=run_id,
                    path=str(run_yaml_path),
                )
                continue
            status_raw = data.get("status")
            status = str(status_raw).strip().lower() if status_raw is not None else ""
            if status in _TERMINAL_STATUSES:
                runs_skipped_terminal += 1
                logger.debug(
                    "sweep_skipped_terminal",
                    run_id=run_id,
                    status=status,
                )
                continue
            if status != "active":
                runs_skipped_terminal += 1
                logger.debug(
                    "sweep_skipped_terminal",
                    run_id=run_id,
                    status=status or "unknown",
                )
                continue

        # Protected runs are preserved regardless of age (FR10).
        if prefilter_protected is True:
            runs_preserved_protected += 1
            continue
        if prefilter_protected is None and data is not None and data.get("protected") is True:
            runs_preserved_protected += 1
            continue

        # Live-pin preservation (FR09) — compare resolved string forms.
        try:
            run_dir_key = str(run_dir.resolve())
        except (OSError, RuntimeError):
            run_dir_key = str(run_dir)
        if run_dir_key in pinned:
            runs_preserved_pinned += 1
            continue

        last_activity = compute_last_activity(run_dir)

        # Fresh activity — nothing to do.
        if last_activity >= staleness_cutoff:
            continue

        age_seconds = max(0.0, now - last_activity)
        age_hours = age_seconds / 3600.0

        # Grace window — inside [grace_cutoff, staleness_cutoff] — preserve
        # and warn.  `>=` ensures a run whose last_activity falls exactly on
        # the grace boundary is preserved (spec: "runs younger than
        # staleness+grace") — audit P2-03.
        if last_activity >= grace_cutoff:
            runs_in_grace_window += 1
            near_stale_ids.append(run_id)
            grace_remaining_hours = max(
                0.0,
                (last_activity - grace_cutoff) / 3600.0,
            )
            logger.warning(
                "run_near_stale_warning",
                run_id=run_id,
                age_hours=round(age_hours, 3),
                grace_hours_remaining=round(grace_remaining_hours, 3),
                run_path=str(run_dir),
            )
            continue

        # Abandon path.
        abandoned_ids.append(run_id)
        runs_abandoned += 1

        if dry_run:
            logger.info(
                "run_auto_abandoned",
                run_id=run_id,
                reason="stale_activity_dry_run",
                last_activity_ts=last_activity,
                staleness_hours=staleness_hours,
                age_hours=round(age_hours, 3),
                dry_run=True,
                run_path=str(run_dir),
            )
            continue

        # Mutate run.yaml — preserve every field except `status`. Load
        # YAML lazily here: read paths short-circuit via the prefilter,
        # so the expensive ruamel rt-mode parse only happens for the
        # rare run we are actually about to abandon.
        if data is None:
            data = _load_run_yaml(run_yaml_path)
        if data is None:
            abandoned_ids.pop()
            runs_abandoned -= 1
            runs_skipped_malformed += 1
            logger.warning(
                "sweep_skipped_malformed",
                run_id=run_id,
                path=str(run_yaml_path),
                reason="yaml_read_failed_at_abandon",
            )
            continue
        data["status"] = "abandoned"
        try:
            _dump_run_yaml_atomic(run_yaml_path, data)
        except OSError as exc:
            # Write failure — best-effort: log, count, and keep going on
            # the next run.  Do NOT add to abandoned_ids since the mutation
            # did not land.
            abandoned_ids.pop()
            runs_abandoned -= 1
            runs_skipped_malformed += 1
            logger.warning(
                "sweep_skipped_malformed",
                run_id=run_id,
                path=str(run_yaml_path),
                error=type(exc).__name__,
                detail=str(exc),
                reason="yaml_write_failed",
            )
            continue

        events_path = run_dir / "meta" / "events.jsonl"
        _append_event_best_effort(
            events_path,
            "run_auto_abandoned",
            {
                "reason": "stale_activity",
                "last_activity_ts": last_activity,
                "staleness_hours": staleness_hours,
                "age_hours": round(age_hours, 3),
            },
        )

        logger.info(
            "run_auto_abandoned",
            run_id=run_id,
            reason="stale_activity",
            last_activity_ts=last_activity,
            staleness_hours=staleness_hours,
            age_hours=round(age_hours, 3),
            dry_run=False,
            run_path=str(run_dir),
        )

    abandoned_ids.sort()
    near_stale_ids.sort()
    duration_ms = (time.monotonic() - start) * 1000.0

    report = StaleRunReport(
        runs_scanned=runs_scanned,
        runs_abandoned=runs_abandoned,
        runs_preserved_pinned=runs_preserved_pinned,
        runs_preserved_protected=runs_preserved_protected,
        runs_in_grace_window=runs_in_grace_window,
        runs_skipped_terminal=runs_skipped_terminal,
        runs_skipped_malformed=runs_skipped_malformed,
        abandoned_run_ids=abandoned_ids,
        near_stale_run_ids=near_stale_ids,
        duration_ms=duration_ms,
    )

    logger.info(
        "boot_gc_complete",
        runs_scanned=runs_scanned,
        runs_abandoned=runs_abandoned,
        runs_preserved_pinned=runs_preserved_pinned,
        runs_preserved_protected=runs_preserved_protected,
        runs_in_grace_window=runs_in_grace_window,
        runs_skipped_terminal=runs_skipped_terminal,
        runs_skipped_malformed=runs_skipped_malformed,
        duration_ms=duration_ms,
        dry_run=dry_run,
        runs_root=str(runs_root),
    )

    # asdict is exposed through the module as a convenience for the CLI's
    # ``--json`` flag; see ``server/_cli.py::_run_gc``.
    _ = asdict  # keep import alive for mypy / avoid unused-import noise

    return report
