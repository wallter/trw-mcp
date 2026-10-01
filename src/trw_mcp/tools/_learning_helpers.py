"""Extracted helpers for trw_learn — pure functions for independent testing.

Each function encapsulates a single concern previously inlined in the
229-line trw_learn tool closure, making each independently testable and
reducing the tool body to ~50 lines of orchestration.

PRD lineage:
- check_and_handle_dedup: PRD-CORE-042 (semantic dedup)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import DedupHandleResult
from trw_mcp.state._helpers import truncate_nudge_line as truncate_nudge_line
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.telemetry._tool_span_attrs import set_dedup_action

if TYPE_CHECKING:
    from collections.abc import Callable

    from trw_memory.lifecycle.correction import LearningPatch

    from trw_mcp.state._store_selection import MemoryStore

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
    evidence_level: str = "unknown"  # PRD-CORE-312-FR01
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
from trw_mcp.state.analytics.core import _NOISE_PREFIXES as _NOISE_PREFIXES
from trw_mcp.state.analytics.core import is_noise_summary as is_noise_summary


def _resolve_merge_survivor(
    entries_dir: Path, existing_id: str, reader: FileStateReader
) -> tuple[Path, dict[str, object]] | None:
    """Resolve the merge survivor's sidecar for *existing_id* (PRD-FIX-130-FR07).

    Returns ``(path, parsed body)``. Handing the BODY back is what makes the
    FR07 bound real: the resolver has already read and validated the file, so a
    caller that only took the path made ``merge_into_survivor`` read it a second time
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

    from trw_mcp.state.dedup import dedup_verdict, merge_base, merge_into_survivor
    from trw_mcp.state.persistence import lock_for_rmw

    # PRD-CORE-302 C4: a daemon refusal raises out of here. Reading it as "no
    # duplicate" would store a copy the daemon could not check.
    dedup_result = dedup_verdict(params.summary, params.detail, entries_dir, config=config)
    if dedup_result.action == "skip":
        set_dedup_action("skip")  # PRD-CORE-344 FR03: in-memory span attribute, no host lock held
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
            # Only the fields the merge folds in: the survivor keeps its own
            # provenance, so the incoming entry's is not built at all.
            entry_dict: dict[str, object] = {
                "id": params.learning_id,
                "summary": params.summary,
                "detail": params.detail,
                "tags": params.tags,
                "evidence": params.evidence,
                "impact": params.impact,
                "type": getattr(params.type, "value", params.type),
                "confidence": getattr(params.confidence, "value", params.confidence),
                "protection_tier": getattr(params.protection_tier, "value", params.protection_tier),
                "assertions": params.assertions or [],  # PRD-CORE-086 FR05
            }
            yaml_file, survivor_data = survivor
            # FR07: the survivor was parsed once, during resolution; the merge rides that read.
            # PRD-CORE-308 (B71-12): the store's row is the base and takes the merge first;
            # the sidecar is written only once the store accepted.
            store, merged_body = _merge_into_store(
                entries_dir,
                survivor_data,
                params.learning_id,
                lambda base: merge_into_survivor(
                    yaml_file,
                    entry_dict,
                    reader,
                    writer,
                    max_merge_tags=config.max_consolidated_tags,
                    existing_data=base,
                    write=False,
                ),
            )
            # Two accepted merges can write their sidecars in either order; each writes the
            # row as it is NOW, under the file's lock, so the last write is the latest row.
            # The lock file sits under runtime/, not beside the sidecar: a sibling ``.lock`` was left
            # in entries/ after every merge (E2E-INC-010).
            with lock_for_rmw(_merge_lock_anchor(entries_dir, yaml_file)):
                fresh = store.get(str(survivor_data.get("id", "")))
                writer.write_yaml(yaml_file, merge_base(merged_body, fresh) if fresh is not None else merged_body)
            set_dedup_action("merge")  # after the lock is released: no telemetry under a host lock
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

    set_dedup_action("store")  # a checked non-duplicate, or an unresolved survivor left unmerged
    return None


def _merge_lock_anchor(entries_dir: Path, yaml_file: Path) -> Path:
    """The path whose ``.lock`` sibling serialises sidecar writes for *yaml_file*, kept out of ``entries/``."""
    return _entries_trw_dir(entries_dir) / "runtime" / "entry-locks" / yaml_file.name


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


def _merge_into_store(
    entries_dir: Path, survivor: dict[str, object], incoming_id: str, fold: Callable[[dict[str, object]], object]
) -> tuple[MemoryStore, dict[str, object]]:
    """Fold a duplicate into the survivor's STORE row; return the store and the body it accepted.

    B71-12: the base is the store's row, never the sidecar alone. A sidecar misses a merge
    the store took before its own write failed, and two writers each read theirs before the
    other committed; values computed from either overwrote a merge that had landed. So the
    write is conditional on the revision the merge was computed from (PRD-CORE-308): a merge
    landing in between answers ``conflict`` and this one re-reads and re-folds, at most
    ``CONFLICT_ATTEMPTS`` times. An incoming id the row already lists in ``merged_from`` is a
    replay of a merge that landed (a journal replay keeps its id): the store is not written
    again and the sidecar is brought up to the row. Any refusal raises, before the sidecar
    is written (PRD-CORE-302 C5).
    """
    from trw_memory.lifecycle.correction import CONFLICT_ATTEMPTS, not_found, revision_of

    from trw_mcp.state._store_selection import StoreUnavailableError, selected_store
    from trw_mcp.state.dedup import merge_base

    learning_id = str(survivor.get("id", ""))
    store, _namespace = selected_store(_resolve_dedup_trw_dir(entries_dir))
    result: dict[str, str] = {}
    for _attempt in range(CONFLICT_ATTEMPTS):
        row = store.get(learning_id)
        if row is None:
            result = not_found(learning_id)
            break
        body = merge_base(survivor, row)
        if incoming_id in row.merged_from:
            return store, body
        fold(body)
        result = store.correct(learning_id, _merge_patch(body, revision_of(row)))
        if result.get("status") in {"updated", "no_changes"}:
            return store, body
        if result.get("status") != "conflict":
            break
    raise StoreUnavailableError(
        f"the memory store did not accept the dedup merge into {learning_id} "
        f"({result.get('status')}: {result.get('error', 'no reason given')}); nothing was merged"
    )


def _merge_patch(merged_entry: dict[str, object], revision: str | None) -> LearningPatch:
    """The merged body's merge-owned fields as absolute values, conditional on *revision*."""
    from trw_memory.lifecycle.correction import LearningPatch

    return LearningPatch.model_validate(
        {
            "detail": str(merged_entry.get("detail", "")),
            "tags": [str(tag) for tag in cast("list[object]", merged_entry.get("tags") or [])],
            "evidence": [str(item) for item in cast("list[object]", merged_entry.get("evidence") or [])],
            "impact": float(str(merged_entry.get("impact", 0.5))),
            "recurrence": int(str(merged_entry.get("recurrence", 1))),
            "merged_from": [str(item) for item in cast("list[object]", merged_entry.get("merged_from") or [])],
            "assertions": [
                dict(item)
                for item in cast("list[object]", merged_entry.get("assertions") or [])
                if isinstance(item, dict)
            ],
            # PRD-CORE-110: propagate the protection-preserving merge result so
            # the primary backend (recall source of truth) keeps the stronger tier.
            "protection_tier": str(merged_entry.get("protection_tier") or "normal"),
            "confidence": str(merged_entry.get("confidence") or "unverified"),
            "type": str(merged_entry.get("type") or "pattern"),
            "if_revision": revision,
        }
    )
