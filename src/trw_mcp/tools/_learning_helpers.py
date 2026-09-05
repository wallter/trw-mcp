"""Extracted helpers for trw_learn — pure functions for independent testing.

Each function encapsulates a single concern previously inlined in the
229-line trw_learn tool closure, making each independently testable and
reducing the tool body to ~50 lines of orchestration.

PRD lineage:
- calibrate_impact: PRD-CORE-034 (Bayesian calibration)
- check_soft_cap: PRD-CORE-034-FR01 (distribution soft-cap)
- check_and_handle_dedup: PRD-CORE-042 (semantic dedup)
- enforce_distribution: PRD-CORE-034 (forced distribution enforcement)
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import DedupHandleResult
from trw_mcp.state._helpers import truncate_nudge_line as truncate_nudge_line
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)

# Re-export from canonical source for backward compatibility
from trw_mcp.state._constants import VALID_SOURCES as _VALID_SOURCES


def _validate_source_type(source_type: str) -> Literal["human", "agent", "tool", "consolidated"]:
    """Validate and coerce source_type to Literal type.

    For backward compatibility, unknown values are coerced to 'agent'.
    """
    if source_type not in _VALID_SOURCES:
        logger.debug("unknown_source_coerced", source_type=source_type)
        return "agent"
    return cast("Literal['human', 'agent', 'tool', 'consolidated']", source_type)


@dataclass(slots=True)
class LearningParams:
    """Bundle of per-entry fields passed to check_and_handle_dedup.

    Groups the caller-supplied fields so the helper signature stays
    narrow (5 params) regardless of how many entry attributes exist.
    """

    summary: str
    detail: str
    learning_id: str
    tags: list[str]
    evidence: list[str]
    impact: float
    source_type: str
    source_identity: str
    client_profile: str = ""
    model_id: str = ""
    shard_id: str | None = None
    assertions: list[dict[str, str]] | None = None
    # PRD-CORE-110: Typed learning fields
    type: str = "pattern"
    nudge_line: str = ""
    expires: str = ""
    confidence: str = "unverified"
    task_type: str = ""
    domain: list[str] | None = None
    phase_origin: str = ""
    phase_affinity: list[str] | None = None
    team_origin: str = ""
    protection_tier: str = "normal"
    # PRD-CORE-111: Code-grounded anchors
    anchors: list[dict[str, object]] | None = None
    # PRD-CORE-244 FR01: ``None`` == "no anchor assessment has ever run on this
    # learning". Only ``compute_anchor_validity`` over real anchors sets a float.
    anchor_validity: float | None = None


# PRD-FIX-061-FR01: Canonical definition moved to state/analytics/core.py.
# Re-exported here for backward compatibility with existing consumers.
from trw_mcp.state._constants import DEFAULT_NAMESPACE
from trw_mcp.state.analytics.core import _NOISE_PREFIXES as _NOISE_PREFIXES
from trw_mcp.state.analytics.core import is_noise_summary as is_noise_summary


def calibrate_impact(impact: float, config: TRWConfig) -> float:
    """Apply Bayesian calibration to the raw impact score.

    Uses recall tracking stats to weight user accuracy, then blends the
    user-provided impact toward the organisational mean.

    Fail-open: any exception falls back to the raw *impact* value.

    Args:
        impact: Raw impact score 0.0-1.0 from the caller.
        config: Framework configuration (unused directly, but kept
            for symmetry with sibling helpers and future use).

    Returns:
        Calibrated impact score 0.0-1.0.
    """
    try:
        from trw_mcp.scoring import bayesian_calibrate, compute_calibration_accuracy
        from trw_mcp.state.recall_tracking import get_recall_stats

        recall_stats = get_recall_stats()
        user_weight = compute_calibration_accuracy(cast("dict[str, object]", recall_stats))
        return bayesian_calibrate(
            user_impact=impact,
            user_weight=user_weight,
        )
    except (ImportError, OSError, RuntimeError, ValueError, TypeError, ZeroDivisionError):
        return impact  # Fail-open: calibration failure falls back to raw impact


def check_soft_cap(
    impact: float,
    active_entries: list[dict[str, object]],
    config: TRWConfig,
) -> tuple[float, str | None]:
    """Check and apply the forced-distribution soft-cap on impact.

    When high-impact entries (>= 0.8) exceed the configured threshold
    percentage of all active learnings, the new entry's impact is reduced
    iteratively until the ratio falls within bounds (floor 0.5).

    Args:
        impact: Already-calibrated impact score.
        active_entries: All active learning dicts (with ``impact`` key).
        config: Framework configuration providing ``impact_high_threshold_pct``.

    Returns:
        Tuple of (possibly adjusted impact, warning message or None).
    """
    try:
        high_count = sum(1 for e in active_entries if float(str(e.get("impact", 0.5))) >= 0.8)
        total = len(active_entries)
        new_total = total + 1
        new_high = high_count + (1 if impact >= 0.8 else 0)
        threshold_pct = config.impact_high_threshold_pct
        threshold_frac = threshold_pct / 100.0

        if new_total >= 5 and new_total > 0 and (new_high / new_total) > threshold_frac:
            adjusted = impact
            while adjusted >= 0.8 and new_total > 0 and (new_high / new_total) > threshold_frac:
                adjusted *= 0.9
                if adjusted < 0.8:
                    new_high = high_count
                if adjusted < 0.5:  # pragma: no cover — defensive guard; while condition (>=0.8) exits first
                    adjusted = 0.5
                    break
            if adjusted != impact:
                warning = (
                    f"Impact soft-capped from {impact:.2f} to {adjusted:.2f}: "
                    f"high-impact entries ({high_count}/{total} active) would exceed "
                    f"{threshold_pct}% threshold."
                )
                return round(adjusted, 4), warning
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "distribution_check_skipped", exc_info=True
        )  # justified: fail-open, must not block learning recording

    return impact, None


def _resolve_merge_survivor(
    entries_dir: Path, existing_id: str, reader: FileStateReader
) -> tuple[Path, dict[str, object]] | None:
    """Resolve the merge survivor's sidecar for *existing_id* (PRD-FIX-130-FR07).

    Returns ``(path, parsed body)``. Handing the BODY back is what makes the
    FR07 bound real: the resolver has already read and validated the file, so a
    caller that only took the path made ``merge_entries`` read it a second time
    and the backend sync read it a third — three reads under a "at most one"
    requirement.

    Two attempts, in cost order, and no third:

    1. the BOUNDED lookup — the backend row for the id supplies the summary and
       created date that reconstruct the filename, so exactly one YAML file is
       read. This is the path a journal replay takes, and it is O(1) in the
       corpus size where the previous glob-and-read loop was O(N) over 6,532
       files (45,945 ms worst case);
    2. ONE fall-back scan for the ids the bounded lookup cannot reconstruct — a
       sidecar written before the current filename convention, or a summary
       edited after its file was named. Exact id match only.

    Returns ``None`` when both fail. It never returns a "closest" file: merging
    into the wrong survivor silently corrupts the store, while declining to merge
    costs one duplicate row that the next dedup pass can still collapse.
    """
    from trw_mcp.state._entry_paths import resolve_entry_file

    resolved = resolve_entry_file(entries_dir, existing_id, reader, trw_dir=_entries_trw_dir(entries_dir))
    if resolved is not None:
        return resolved
    from trw_mcp.state.analytics.core import find_entry_by_id

    logger.info("learning_dedup_merge_path_scan_fallback", existing_id=existing_id)
    return find_entry_by_id(entries_dir, existing_id, reader=reader)


def check_and_handle_dedup(
    params: LearningParams,
    entries_dir: Path,
    reader: FileStateReader,
    writer: FileStateWriter,
    config: TRWConfig,
) -> DedupHandleResult | None:
    """Check for semantic duplicates and handle skip/merge.

    When dedup is enabled and a near-duplicate is found:
    - ``skip``: returns a result dict indicating the entry was skipped.
    - ``merge``: merges into the existing entry and returns a result dict.

    Args:
        params: Bundled per-entry fields (summary, detail, learning_id, …).
        entries_dir: Path to ``.trw/learnings/entries/``.
        reader: File state reader.
        writer: File state writer.
        config: Framework configuration.

    Returns:
        A result dict if the entry was skipped or merged (caller should
        return it early), or ``None`` if no duplicate was found and the
        caller should proceed with normal storage.
    """
    if not config.dedup_enabled:
        return None

    try:
        from trw_mcp.state.dedup import check_duplicate, merge_entries

        dedup_result = check_duplicate(
            params.summary,
            params.detail,
            entries_dir,
            reader,
            config=config,
        )

        if dedup_result.action == "skip":
            logger.info(
                "learning_dedup_skipped",
                new_id=params.learning_id,
                existing_id=dedup_result.existing_id,
                similarity=dedup_result.similarity,
            )
            return {
                "status": "skipped",
                "learning_id": params.learning_id,
                "duplicate_of": dedup_result.existing_id or "",
                "similarity": round(dedup_result.similarity, 3),
                "message": f"Near-identical entry already exists: {dedup_result.existing_id}",
            }

        if dedup_result.action == "merge":
            # PRD-FIX-130-FR07: resolve the survivor's file by COMPUTING its path
            # from the backend row, never by scanning the corpus. The old sorted
            # glob-and-read loop measured 45,945 ms worst case against 6,532
            # files, and the worst case IS the common one: filenames are date
            # prefixed, so a recently re-learned entry sorts last.
            survivor = _resolve_merge_survivor(entries_dir, dedup_result.existing_id or "", reader)
            if survivor is None:
                # Refusal, not a fallback guess: an id no file provably carries
                # is left unmerged. The caller then proceeds with normal storage,
                # which is exactly the pre-change no-match outcome.
                logger.warning(
                    "learning_dedup_merge_unresolved",
                    new_id=params.learning_id,
                    existing_id=dedup_result.existing_id,
                )
            else:
                try:
                    from trw_mcp.models.learning import (
                        LearningConfidence,
                        LearningEntry,
                        LearningProtectionTier,
                        LearningType,
                    )
                    from trw_mcp.state.persistence import model_to_dict

                    entry = LearningEntry(
                        id=params.learning_id,
                        summary=params.summary,
                        detail=params.detail,
                        tags=params.tags,
                        evidence=params.evidence,
                        impact=params.impact,
                        shard_id=params.shard_id,
                        source_type=_validate_source_type(params.source_type),
                        source_identity=params.source_identity,
                        client_profile=params.client_profile,
                        model_id=params.model_id,
                        # PRD-CORE-110: typed fields — preserve protection on merge
                        # (str→enum coercion mirrors _learn_side_effects.py).
                        type=LearningType(params.type) if isinstance(params.type, str) else params.type,
                        nudge_line=params.nudge_line,
                        expires=params.expires,
                        confidence=LearningConfidence(params.confidence)
                        if isinstance(params.confidence, str)
                        else params.confidence,
                        task_type=params.task_type,
                        domain=params.domain or [],
                        phase_origin=params.phase_origin,
                        phase_affinity=params.phase_affinity or [],
                        team_origin=params.team_origin,
                        protection_tier=LearningProtectionTier(params.protection_tier)
                        if isinstance(params.protection_tier, str)
                        else params.protection_tier,
                        # PRD-CORE-111: code-grounded anchors
                        anchors=params.anchors or [],
                    )
                    # PRD-CORE-244 FR01: an unassessed learning carries no
                    # score at all, so the field is written only when one
                    # was actually computed.
                    if params.anchor_validity is not None:
                        entry = entry.model_copy(update={"anchor_validity": params.anchor_validity})
                    entry_dict = model_to_dict(entry)
                    # PRD-CORE-086 FR05: Include assertions in merge data
                    if params.assertions:
                        entry_dict["assertions"] = params.assertions
                    yaml_file, survivor_data = survivor
                    # FR07: the survivor was parsed once, during resolution. Both
                    # the merge and the backend sync ride that single read —
                    # neither reopens the file.
                    merged_body: dict[str, object] = {}
                    merge_entries(
                        yaml_file,
                        entry_dict,
                        reader,
                        writer,
                        max_merge_tags=config.max_consolidated_tags,
                        existing_data=survivor_data,
                        merged_out=merged_body,
                    )
                    _sync_merged_entry_to_backend(entries_dir, merged_body)
                    logger.info(
                        "learning_dedup_merged",
                        new_id=params.learning_id,
                        existing_id=dedup_result.existing_id,
                        similarity=dedup_result.similarity,
                    )
                    return {
                        "status": "merged",
                        "merged_into": dedup_result.existing_id or "",
                        "new_id": params.learning_id,
                        "similarity": round(dedup_result.similarity, 3),
                        "message": f"Merged into existing entry: {dedup_result.existing_id}",
                    }
                except (OSError, StateError, ValueError, TypeError):
                    logger.debug("learning_dedup_merge_failed", exc_info=True)
    except (ImportError, OSError, RuntimeError, StateError, ValueError, TypeError) as exc:
        logger.debug("dedup_check_failed", error=str(exc))

    return None


def _entries_trw_dir(entries_dir: Path) -> Path:
    """The ``.trw`` dir that OWNS *entries_dir*, derived from its shape, never ambient.

    The merge-survivor lookup (PRD-FIX-130 FR07) must read the same store the
    sidecar directory belongs to. Ambient ``resolve_trw_dir()`` answers "whatever
    project the process is pinned to", which in a test with a scratch
    ``entries_dir`` is a different (possibly live) store — so the canonical
    ``<trw>/learnings/entries`` shape wins and only an unshaped directory falls
    back to the ambient resolver used by the backend-sync path.
    """
    if entries_dir.name == "entries" and entries_dir.parent.name == "learnings":
        return entries_dir.parent.parent
    return _resolve_dedup_trw_dir(entries_dir)


def _resolve_dedup_trw_dir(entries_dir: Path) -> Path:
    """Resolve the TRW dir for dedup backend sync."""
    try:
        from trw_mcp.state._paths import resolve_trw_dir

        return resolve_trw_dir()
    except Exception:  # justified: fail-open, dedup backend resolution falls back to the entries parent
        logger.debug("dedup_trw_dir_resolve_failed", exc_info=True)
        if entries_dir.name == "entries" and entries_dir.parent.name == "learnings":
            return entries_dir.parent.parent
        return entries_dir.parent


def _sync_merged_entry_to_backend(entries_dir: Path, merged_entry: dict[str, object]) -> None:
    """Best-effort sync of merged YAML fields into the primary backend."""
    try:
        from trw_mcp.state.memory_adapter import get_backend

        learning_id = str(merged_entry.get("id", ""))
        if not learning_id:
            return

        backend = get_backend(_resolve_dedup_trw_dir(entries_dir))
        backend.update(
            learning_id,
            namespace=str(merged_entry.get("namespace") or DEFAULT_NAMESPACE),
            detail=str(merged_entry.get("detail", "")),
            tags=[str(tag) for tag in cast("list[object]", merged_entry.get("tags") or [])],
            evidence=[str(item) for item in cast("list[object]", merged_entry.get("evidence") or [])],
            importance=float(str(merged_entry.get("impact", 0.5))),
            recurrence=int(str(merged_entry.get("recurrence", 1))),
            merged_from=[str(item) for item in cast("list[object]", merged_entry.get("merged_from") or [])],
            assertions=[
                dict(item)
                for item in cast("list[object]", merged_entry.get("assertions") or [])
                if isinstance(item, dict)
            ],
            # PRD-CORE-110: propagate the protection-preserving merge result so
            # the primary backend (recall source of truth) keeps the stronger tier.
            protection_tier=str(merged_entry.get("protection_tier") or "normal"),
            confidence=str(merged_entry.get("confidence") or "unverified"),
            type=str(merged_entry.get("type") or "pattern"),
        )
    except Exception:  # justified: fail-open, merged-entry backend sync is best-effort after YAML updates
        logger.debug("dedup_backend_sync_failed", exc_info=True)


def enforce_distribution(
    impact: float,
    calibrated_impact: float,
    learning_id: str,
    active_entries: list[dict[str, object]],
    trw_dir: Path,
    config: TRWConfig,
) -> tuple[str, list[str]]:
    """Enforce forced-distribution caps by demoting excess high-impact entries.

    Appends the newly stored entry to the active list, computes tier
    distributions, and demotes entries that exceed caps via the adapter.

    Args:
        impact: Raw (pre-calibration) impact — used for tier naming.
        calibrated_impact: Calibrated impact score of the new entry.
        learning_id: ID of the just-stored learning.
        active_entries: List of all active learning dicts (mutable —
            the new entry is appended in-place).
        trw_dir: Path to ``.trw/`` directory.
        config: Framework configuration.

    Returns:
        Tuple of (warning message string, list of demoted IDs).
        Warning is empty string when no demotions occurred.
    """
    demoted_ids: list[str] = []
    distribution_warning = ""

    if not config.impact_forced_distribution_enabled or impact < 0.7:
        return distribution_warning, demoted_ids

    try:
        from trw_mcp.scoring import enforce_tier_distribution
        from trw_mcp.state.memory_adapter import update_learning as adapter_update

        # Append newly stored entry so forced distribution sees it
        active_entries.append({"id": learning_id, "impact": calibrated_impact})
        all_entries: list[tuple[str, float]] = []
        for e in active_entries:
            lid = str(e.get("id", ""))
            sc = float(str(e.get("impact", 0.5)))
            if lid:
                all_entries.append((lid, sc))

        demotions = enforce_tier_distribution(all_entries)
        for demoted_id, new_score in demotions:
            demoted_ids.append(demoted_id)
            with contextlib.suppress(OSError, RuntimeError, ValueError, TypeError):
                adapter_update(trw_dir, demoted_id, impact=new_score)

        if demotions:
            tier_name = "critical" if impact >= 0.9 else "high"
            logger.warning(
                "learn_distribution_demoted",
                n_demoted=len(demoted_ids),
                demoted_ids=demoted_ids,
                tier=tier_name,
            )
            distribution_warning = (
                f"Impact tier '{tier_name}' exceeded cap. "
                f"Forced distribution: demoted {len(demotions)} entr"
                f"{'y' if len(demotions) == 1 else 'ies'} to maintain tier caps. "
                f"IDs: {[d[0] for d in demotions]}"
            )
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.debug(
            "distribution_enforcement_skipped", exc_info=True
        )  # justified: fail-open, must not block learning recording

    return distribution_warning, demoted_ids
