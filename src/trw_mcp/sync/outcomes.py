"""Outcome sync helpers — PRD-INFRA-051 + PRD-CORE-144.

PRD-CORE-144 FR05/FR06: the pending-outcome source is no longer the
synthesized ``.trw/logs/recall_tracking.jsonl`` shape. Instead we
iterate delivered run.yaml files under ``.trw/runs/{task}/{run_id}/
meta/run.yaml`` and emit one real ``OutcomeSync`` per run whose
sibling ``meta/synced.json`` marker is missing or hash-stale.

Backward compat: ``.trw/logs/recall_tracking.jsonl`` format is
unchanged (other consumers read it). ``PendingOutcome.line_no`` is
retained on the dataclass for legacy coordinator bookkeeping — it now
carries a stable per-run ordinal rather than a byte offset.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

logger = structlog.get_logger(__name__)

_SYNCED_MARKER = "synced.json"


@dataclass(frozen=True)
class PendingOutcome:
    """Outcome payload plus metadata for idempotency bookkeeping.

    Fields preserved for backward compatibility:
    - ``payload``: dict serialised to OutcomeSync wire shape.
    - ``line_no``: ordinal index (1-based) across the scan; used by the
      legacy coordinator to stamp "highest processed" watermarks.

    Added for PRD-CORE-144:
    - ``run_dir``: absolute path to the run directory ``.../runs/{task}/{run_id}``.
    - ``sync_hash``: SHA-256 over (run_id, session_metrics payload).
    - ``run_id``: final path component of ``run_dir``.
    - ``legacy_no_ids``: True when the run predates FR04 (no
      ``session_metrics.learning_exposure.ids``).
    """

    payload: dict[str, object]
    line_no: int
    run_dir: Path | None = None
    sync_hash: str = ""
    run_id: str = ""
    legacy_no_ids: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


def _compute_sync_hash(run_id: str, session_metrics: dict[str, Any]) -> str:
    """Deterministic hash over run_id + canonicalised session_metrics."""
    payload = {"run_id": run_id, "session_metrics": session_metrics}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_run_yaml(run_yaml: Path) -> dict[str, Any] | None:
    """Parse a delivered run.yaml, returning None on any failure."""
    try:
        yaml = YAML(typ="safe")
        with run_yaml.open("r", encoding="utf-8") as fh:
            data = yaml.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:  # justified: per-run try/except per RISK-003
        logger.debug("run_yaml_parse_failed", path=str(run_yaml), exc_info=True)
    return None


def _read_existing_marker(marker_path: Path) -> dict[str, Any] | None:
    """Parse sibling synced.json marker; return None when absent/corrupt."""
    if not marker_path.exists():
        return None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def write_synced_marker(
    run_dir: Path,
    *,
    run_id: str,
    sync_hash: str,
    target_label: str,
) -> None:
    """Write the sibling ``meta/synced.json`` marker after a successful push."""
    marker = run_dir / "meta" / _SYNCED_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "sync_hash": sync_hash,
        "target_label": target_label,
    }
    try:
        marker.write_text(
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
    except OSError:  # justified: fail-open — marker write failure is not fatal
        logger.warning("synced_marker_write_failed", run_dir=str(run_dir), exc_info=True)


def _extract_session_metrics(run_data: dict[str, Any]) -> dict[str, Any] | None:
    """Pull session_metrics (either top-level or nested) from a run.yaml.

    Returns None when the run has no delivered metrics (e.g. abandoned run).
    """
    metrics = run_data.get("session_metrics")
    if isinstance(metrics, dict):
        return metrics
    for key in ("meta", "summary"):
        nested = run_data.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("session_metrics"), dict):
            return nested["session_metrics"]
    return None


def _build_outcome_payload(
    *,
    run_id: str,
    run_dir: Path,
    session_metrics: dict[str, Any],
    legacy_no_ids: bool,
) -> dict[str, object]:
    """Construct an OutcomeSync-shaped dict from one run's session_metrics."""
    exposure = session_metrics.get("learning_exposure") or {}
    raw_ids = exposure.get("ids") if isinstance(exposure, dict) else None
    learning_ids: list[str] = [str(i) for i in raw_ids] if isinstance(raw_ids, list) else []

    rework = session_metrics.get("rework_rate") or {}
    rework_rate: float | None = None
    total_files: int | None = None
    if isinstance(rework, dict):
        raw_rate = rework.get("rework_rate")
        if isinstance(raw_rate, (int, float)):
            rework_rate = float(raw_rate)
        raw_total = rework.get("total_files")
        if isinstance(raw_total, (int, float)):
            total_files = int(raw_total)

    status = str(session_metrics.get("status", ""))
    build_passed = status == "success" if status else None

    propensity_data: dict[str, object] = {
        "source": "run_yaml",
        "run_dir": str(run_dir),
    }
    for key in ("composite_outcome", "normalized_reward"):
        val = session_metrics.get(key)
        if val is not None:
            propensity_data[key] = val
    if legacy_no_ids:
        propensity_data["legacy_no_ids"] = True

    payload: dict[str, object] = {
        "session_id": run_id,
        "learning_ids": learning_ids,
        "propensity_data": propensity_data,
    }
    if rework_rate is not None:
        payload["rework_rate"] = rework_rate
    if build_passed is not None:
        payload["build_passed"] = build_passed
    tasks_completed = session_metrics.get("tasks_completed")
    if isinstance(tasks_completed, (int, float)):
        payload["tasks_completed"] = int(tasks_completed)
    if total_files is not None:
        payload["files_changed"] = total_files
    return payload


def load_pending_outcomes(
    trw_dir: Path,
    *,
    since_line: int = 0,  # retained for signature compat; ignored with new source
) -> list[PendingOutcome]:
    """Iterate delivered run.yaml files and emit one pending outcome per unsynced run.

    PRD-CORE-144 FR05: real OutcomeSync per delivered run.
    PRD-CORE-144 FR06: runs without ``learning_exposure.ids`` still sync
      with ``learning_ids=[]`` and a structured ``legacy_run_pushed`` log.

    ``since_line`` is accepted for signature compatibility with the old
    line-based dedup but is ignored — idempotency is now enforced by the
    sibling ``meta/synced.json`` marker + the ``sync_hash`` check.
    """
    del since_line  # compat-only

    from trw_mcp.state._paths import iter_run_dirs

    runs_root = trw_dir / "runs"
    if not runs_root.is_dir():
        return []

    pending: list[PendingOutcome] = []
    ordinal = 0
    for run_dir, run_yaml in iter_run_dirs(runs_root):
        try:
            run_data = _read_run_yaml(run_yaml)
            if run_data is None:
                continue
            metrics = _extract_session_metrics(run_data)
            if metrics is None:
                continue  # abandoned / never-delivered run

            run_id = run_dir.name
            exposure = metrics.get("learning_exposure") or {}
            legacy_no_ids = not (
                isinstance(exposure, dict)
                and isinstance(exposure.get("ids"), list)
                and exposure["ids"]
            )

            sync_hash = _compute_sync_hash(run_id, metrics)
            marker = run_dir / "meta" / _SYNCED_MARKER
            existing = _read_existing_marker(marker)
            if existing and existing.get("sync_hash") == sync_hash:
                continue  # already synced with matching hash

            payload = _build_outcome_payload(
                run_id=run_id,
                run_dir=run_dir,
                session_metrics=metrics,
                legacy_no_ids=legacy_no_ids,
            )

            if legacy_no_ids:
                logger.info(
                    "legacy_run_pushed",
                    run_id=run_id,
                    run_dir=str(run_dir),
                    outcome="legacy_empty_ids",
                )

            ordinal += 1
            pending.append(
                PendingOutcome(
                    payload=payload,
                    line_no=ordinal,
                    run_dir=run_dir,
                    sync_hash=sync_hash,
                    run_id=run_id,
                    legacy_no_ids=legacy_no_ids,
                )
            )
        except Exception:  # justified: fail-open per RISK-003, keep batch going
            logger.warning(
                "pending_outcome_build_failed",
                run_yaml=str(run_yaml),
                exc_info=True,
            )
            continue

    return pending
