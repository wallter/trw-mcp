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
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

logger = structlog.get_logger(__name__)

_SYNCED_MARKER = "synced.json"

# Backend `OutcomeSync.learning_ids` is declared `Field(..., max_length=50)`
# with `model_config = ConfigDict(extra="forbid")` in `backend/routers/sync.py`.
# Any payload whose `learning_ids` exceeds 50 entries is 422-rejected, and
# because the whole batch is rejected no outcomes (or learnings) ever sync.
# We therefore cap client-side at the same limit. Truncation keeps the FIRST
# 50 ids (deterministic, preserves the run's recorded exposure ordering).
_MAX_OUTCOME_LEARNING_IDS = 50


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
    run_yaml_hash: str = ""
    legacy_no_ids: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


def _compute_sync_hash(run_id: str, session_metrics: dict[str, Any]) -> str:
    """Deterministic hash over run_id + canonicalised session_metrics."""
    payload = {"run_id": run_id, "session_metrics": session_metrics}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_run_yaml(run_yaml: Path, content: bytes | None = None) -> dict[str, Any] | None:
    """Parse a delivered run.yaml, returning None on any failure."""
    try:
        yaml = YAML(typ="safe")
        source = content.decode("utf-8") if content is not None else run_yaml.read_text(encoding="utf-8")
        data = yaml.load(source)
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


def _read_run_yaml_bytes(run_yaml: Path) -> bytes | None:
    """Read a run.yaml once for hashing, filtering, and parsing."""
    try:
        return run_yaml.read_bytes()
    except OSError:
        logger.debug("run_yaml_read_failed", path=str(run_yaml), exc_info=True)
        return None


def _marker_matches_run_yaml(existing: dict[str, Any] | None, *, run_id: str, run_yaml_hash: str) -> bool:
    """Return whether a marker proves this exact run.yaml was already synced."""
    return bool(
        existing
        and existing.get("run_id") == run_id
        and existing.get("run_yaml_sha256") == run_yaml_hash
        and existing.get("sync_hash")
    )


def _write_marker_payload(marker_path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace a marker so concurrent readers never see partial JSON."""
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=marker_path.parent,
            prefix=f".{marker_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            temp_name = fh.name
            json.dump(payload, fh, separators=(",", ":"), sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_name, marker_path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def _upgrade_marker_run_yaml_hash(marker_path: Path, existing: dict[str, Any], run_yaml_hash: str) -> None:
    """Add the source hash to a legacy marker after its sync hash is verified."""
    payload = dict(existing)
    payload["run_yaml_sha256"] = run_yaml_hash
    try:
        _write_marker_payload(marker_path, payload)
    except OSError:  # justified: fail-open — legacy marker remains valid but slower
        logger.debug("synced_marker_upgrade_failed", marker=str(marker_path), exc_info=True)


def write_synced_marker(
    run_dir: Path,
    *,
    run_id: str,
    sync_hash: str,
    target_label: str,
    run_yaml_hash: str | None = None,
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
    if run_yaml_hash:
        payload["run_yaml_sha256"] = run_yaml_hash
    try:
        _write_marker_payload(marker, payload)
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
        if isinstance(nested, dict):
            nested_metrics = nested.get("session_metrics")
            if isinstance(nested_metrics, dict):
                return dict(nested_metrics)
    return None


def _aggregate_recall_outcomes(trw_dir: Path | None) -> dict[str, dict[str, object]]:
    """Aggregate ``recall_tracking.jsonl`` into per-learning bandit signals.

    P1 broken-wiring F8 fix: ``_step_recall_outcome`` records recall events and
    later outcome-only rows into ``.trw/logs/recall_tracking.jsonl``, but those
    signals never reached the sync ``propensity_data`` payload, so the backend
    IPS / bandit arm-update loop could not learn from recall feedback.

    Each returned record is keyed by ``learning_id`` and shaped so the backend
    attribution pipeline (``_normalize_propensity_entries`` /
    ``_propensity_entry_matches_learning``) can match it per-learning:

    - ``learning_id``: the matched learning ID (also the dict key).
    - ``recall_count``: number of recall receipts (bandit-weight proxy — the
      sweep accepts ``recall_count`` when ``selection_probability`` is absent).
    - ``positive`` / ``negative`` / ``neutral``: outcome tallies.
    - ``selection_probability``: derived bandit weight in (0, 1]; larger when a
      learning is recalled more often so IPS down-weights it less. Provided so
      the IPS path (which reads ``selection_probability``) has a real value
      instead of falling back to the deterministic ``1.0`` default.

    Fail-open: any read/parse failure yields ``{}`` so sync is never blocked.
    """
    if trw_dir is None:
        return {}
    tracking_path = trw_dir / "logs" / "recall_tracking.jsonl"
    if not tracking_path.exists():
        return {}

    try:
        from trw_mcp.state._helpers import read_jsonl_resilient

        # Append-only log written per-recall by concurrent agents: a single torn
        # line must drop only that row, not collapse every learning's bandit
        # signal to {} (the strict reader's StateError did exactly that, silently
        # starving the backend IPS / arm-update loop for the whole sync cycle).
        records = read_jsonl_resilient(tracking_path)
    except Exception:  # justified: fail-open, recall enrichment must not block sync
        logger.debug("recall_outcome_aggregate_read_failed", path=str(tracking_path), exc_info=True)
        return {}

    agg: dict[str, dict[str, int]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        lid = str(rec.get("learning_id", ""))
        if not lid:
            continue
        bucket = agg.setdefault(lid, {"recall_count": 0, "positive": 0, "negative": 0, "neutral": 0})
        outcome = rec.get("outcome")
        if outcome == "positive":
            bucket["positive"] += 1
        elif outcome == "negative":
            bucket["negative"] += 1
        elif outcome == "neutral":
            bucket["neutral"] += 1
        else:
            # outcome is None/absent -> this is a fresh recall receipt
            bucket["recall_count"] += 1

    out: dict[str, dict[str, object]] = {}
    for lid, bucket in agg.items():
        recall_count = bucket["recall_count"]
        # Bandit-weight proxy: more recalls -> higher selection_probability so the
        # IPS estimator (weight = 1 / selection_probability) does not over-inflate
        # frequently-surfaced learnings. Clamp into (0, 1] with a floor of 0.05.
        selection_probability = min(1.0, max(0.05, 1.0 - 1.0 / (1.0 + float(recall_count))))
        out[lid] = {
            "learning_id": lid,
            "recall_count": recall_count,
            "positive": bucket["positive"],
            "negative": bucket["negative"],
            "neutral": bucket["neutral"],
            "selection_probability": round(selection_probability, 4),
        }
    return out


def _build_outcome_payload(
    *,
    run_id: str,
    run_dir: Path,
    session_metrics: dict[str, Any],
    legacy_no_ids: bool,
    trw_dir: Path | None = None,
    recall_outcomes: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    """Construct an OutcomeSync-shaped dict from one run's session_metrics."""
    exposure = session_metrics.get("learning_exposure") or {}
    raw_ids = exposure.get("ids") if isinstance(exposure, dict) else None
    learning_ids: list[str] = [str(i) for i in raw_ids][:_MAX_OUTCOME_LEARNING_IDS] if isinstance(raw_ids, list) else []

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

    # P1 F8: feed aggregated per-learning recall outcomes into propensity_data so
    # the recall -> bandit-weight loop closes. ADDITIVE: existing composite_outcome
    # / normalized_reward / source / run_dir keys are untouched. Only learnings the
    # backend can attribute (those exposed this run, when ids are known) are kept;
    # falls back to all aggregated learnings for legacy/no-ids runs.
    if recall_outcomes is None:
        recall_outcomes = _aggregate_recall_outcomes(trw_dir)
    if recall_outcomes:
        if learning_ids:
            scoped = {lid: recall_outcomes[lid] for lid in learning_ids if lid in recall_outcomes}
        else:
            scoped = recall_outcomes
        if scoped:
            propensity_data["recall_outcomes"] = scoped

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
      with ``learning_ids=[]`` and a structured
      ``legacy_run_sync_no_learning_ids`` log.

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
    recall_outcomes: dict[str, dict[str, object]] | None = None
    for run_dir, run_yaml in iter_run_dirs(runs_root):
        try:
            run_id = run_dir.name
            marker = run_dir / "meta" / _SYNCED_MARKER
            existing = _read_existing_marker(marker)
            run_yaml_content = _read_run_yaml_bytes(run_yaml)
            if run_yaml_content is None:
                continue
            run_yaml_hash = hashlib.sha256(run_yaml_content).hexdigest()
            if _marker_matches_run_yaml(existing, run_id=run_id, run_yaml_hash=run_yaml_hash):
                continue

            # Most historical/abandoned runs have no outcome metrics. A byte
            # absence check is a safe negative filter (quoted and nested keys
            # still contain this token) and avoids constructing a YAML parser
            # for those large documents.
            if b"session_metrics" not in run_yaml_content:
                continue

            run_data = _read_run_yaml(run_yaml, run_yaml_content)
            if run_data is None:
                continue
            metrics = _extract_session_metrics(run_data)
            if metrics is None:
                continue  # abandoned / never-delivered run

            exposure = metrics.get("learning_exposure") or {}
            legacy_no_ids = not (
                isinstance(exposure, dict) and isinstance(exposure.get("ids"), list) and exposure["ids"]
            )

            sync_hash = _compute_sync_hash(run_id, metrics)
            if existing and existing.get("sync_hash") == sync_hash:
                # One-time migration for markers written before source hashes
                # were recorded. Re-read the source before upgrading so a
                # concurrent run.yaml mutation cannot create a false fast hit.
                current_content = _read_run_yaml_bytes(run_yaml)
                if current_content is not None and hashlib.sha256(current_content).hexdigest() == run_yaml_hash:
                    _upgrade_marker_run_yaml_hash(marker, existing, run_yaml_hash)
                continue  # already synced with matching hash

            if recall_outcomes is None:
                recall_outcomes = _aggregate_recall_outcomes(trw_dir)
            payload = _build_outcome_payload(
                run_id=run_id,
                run_dir=run_dir,
                session_metrics=metrics,
                legacy_no_ids=legacy_no_ids,
                trw_dir=trw_dir,
                recall_outcomes=recall_outcomes,
            )
            payload["idempotency_key"] = sync_hash

            if legacy_no_ids:
                logger.info(
                    "legacy_run_sync_no_learning_ids",
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
                    run_yaml_hash=run_yaml_hash,
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
