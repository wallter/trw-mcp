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


def truncate_nudge_line(text: str, max_length: int = 80) -> str:
    """Truncate a nudge line to max_length chars, preferring word boundaries.

    Args:
        text: The text to truncate.
        max_length: Maximum length (default 80).

    Returns:
        Truncated text with ellipsis at a word boundary, or hard-cut at max_length.
    """
    if len(text) <= max_length:
        return text
    boundary_start = max(max_length - 20, 0)
    for i in range(boundary_start, max_length):
        if text[i] == " ":
            return text[:i] + "\u2026"
    return text[:max_length]


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
    anchor_validity: float = 1.0


# PRD-FIX-061-FR01: Canonical definition moved to state/analytics/core.py.
# Re-exported here for backward compatibility with existing consumers.
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
                            source_type=_validate_source_type(params.source_type),
                            source_identity=params.source_identity,
                            client_profile=params.client_profile,
                            model_id=params.model_id,
                        )
                        entry_dict = model_to_dict(entry)
                        # PRD-CORE-086 FR05: Include assertions in merge data
                        if params.assertions:
                            entry_dict["assertions"] = params.assertions
                        merged_path = merge_entries(yaml_file, entry_dict, reader, writer)
                        merged_yaml_path = merged_path if isinstance(merged_path, Path) else yaml_file
                        _sync_merged_entry_to_backend(entries_dir, reader.read_yaml(merged_yaml_path))
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
                    continue
    except (ImportError, OSError, RuntimeError, StateError, ValueError, TypeError) as exc:
        logger.debug("dedup_check_failed", error=str(exc))

    return None


def _resolve_dedup_trw_dir(entries_dir: Path) -> Path:
    """Resolve the TRW dir for dedup backend sync."""
    try:
        from trw_mcp.state._paths import resolve_trw_dir

        return resolve_trw_dir()
    except Exception:
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
            detail=str(merged_entry.get("detail", "")),
            tags=[str(tag) for tag in cast("list[object]", merged_entry.get("tags") or [])],
            evidence=[str(item) for item in cast("list[object]", merged_entry.get("evidence") or [])],
            importance=float(str(merged_entry.get("impact", 0.5))),
            recurrence=int(str(merged_entry.get("recurrence", 1))),
            merged_from=[str(item) for item in cast("list[object]", merged_entry.get("merged_from") or [])],
            assertions=[
                dict(item) for item in cast("list[object]", merged_entry.get("assertions") or [])
                if isinstance(item, dict)
            ],
        )
    except Exception:
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
