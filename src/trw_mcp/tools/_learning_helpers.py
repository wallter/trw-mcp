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
from typing import cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import DedupHandleResult
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger()


@dataclass(slots=True)
class LearningParams:
    """Bundle of per-entry fields passed to check_and_handle_dedup.

    Groups the nine caller-supplied fields so the helper signature stays
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
    shard_id: str | None = None


# Auto-generated noise prefixes that should never be persisted as learnings.
# These are produced by ceremony/telemetry tools and add no institutional value.
_NOISE_PREFIXES = ("Repeated operation:", "Success:")


def is_noise_summary(summary: str) -> bool:
    """Return True if summary matches a known auto-generated noise pattern.

    PRD-QUAL-032-FR09: Reject entries whose summary starts with known
    noise prefixes before they are persisted.
    """
    lower = summary.lower()
    return any(lower.startswith(prefix.lower()) for prefix in _NOISE_PREFIXES)


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
        pass  # Fail-open: distribution check must not block learning recording

    return impact, None


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
            # Find existing file and merge
            for yaml_file in sorted(entries_dir.glob("*.yaml")):
                if yaml_file.name == "index.yaml":
                    continue
                try:
                    data = reader.read_yaml(yaml_file)
                    if str(data.get("id", "")) == dedup_result.existing_id:
                        from trw_mcp.models.learning import LearningEntry
                        from trw_mcp.state.persistence import model_to_dict

                        entry = LearningEntry(
                            id=params.learning_id,
                            summary=params.summary,
                            detail=params.detail,
                            tags=params.tags,
                            evidence=params.evidence,
                            impact=params.impact,
                            shard_id=params.shard_id,
                            source_type=params.source_type,
                            source_identity=params.source_identity,
                        )
                        merge_entries(yaml_file, model_to_dict(entry), reader, writer)
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
                            "similarity": str(round(dedup_result.similarity, 3)),
                            "message": f"Merged into existing entry: {dedup_result.existing_id}",
                        }
                except (OSError, StateError, ValueError, TypeError):
                    continue
    except (ImportError, OSError, RuntimeError, StateError, ValueError, TypeError) as exc:
        logger.debug("dedup_check_failed", error=str(exc))

    return None


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
            distribution_warning = (
                f"Impact tier '{tier_name}' exceeded cap. "
                f"Forced distribution: demoted {len(demotions)} entr"
                f"{'y' if len(demotions) == 1 else 'ies'} to maintain tier caps. "
                f"IDs: {[d[0] for d in demotions]}"
            )
    except (OSError, RuntimeError, ValueError, TypeError):
        pass  # Fail-open: distribution enforcement must not block learning recording

    return distribution_warning, demoted_ids
