"""Original entry archival for memory consolidation — FR04.

Archives original cluster entries after consolidation, with atomic
rollback on failure.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.typed_dicts import LearningEntryDict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

if TYPE_CHECKING:
    from trw_mcp.state.tiers import TierManager

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# FR04 — Original Entry Archival to Cold Tier
# ---------------------------------------------------------------------------


def _archive_originals(
    cluster: Sequence[LearningEntryDict],
    consolidated_id: str,
    entries_dir: Path,
    reader: FileStateReader,
    writer: FileStateWriter,
    tier_manager: TierManager | None = None,
) -> None:
    """Archive original cluster entries after consolidation.

    For each entry in *cluster*:
    1. Adds ``consolidated_into: <consolidated_id>`` to the entry.
    2. If *tier_manager* is available, calls ``cold_archive(entry_id, path)``.
    3. Otherwise, sets ``status`` to ``"archived"`` (graceful degradation).

    Atomic batch: on any failure, rolls back ``consolidated_into`` writes
    for already-processed entries and deletes the consolidated entry file.
    Logs ERROR on reversion failure.

    Args:
        cluster: Original entry dicts being archived.
        consolidated_id: ID of the newly created consolidated entry.
        entries_dir: Path to the learnings/entries/ directory.
        reader: FileStateReader for loading entry files.
        writer: FileStateWriter for atomic writes.
        tier_manager: Optional TierManager for cold archival.
    """
    processed: list[tuple[Path, dict[str, object]]] = []  # rollback tracking

    for entry in cluster:
        entry_id = str(entry.get("id", ""))
        if not entry_id:
            continue

        # Derive exact filename from entry_id (safe slugify, no glob injection)
        slug = re.sub(r"[^a-zA-Z0-9_\-]", "-", entry_id)
        entry_path = entries_dir / f"{slug}.yaml"
        if not entry_path.exists():
            logger.warning(
                "consolidation_archive_file_not_found",
                entry_id=entry_id,
            )
            continue

        try:
            data = reader.read_yaml(entry_path)
            original_data = dict(data)  # snapshot for rollback

            # Add consolidated_into field
            data["consolidated_into"] = consolidated_id
            writer.write_yaml(entry_path, data)
            processed.append((entry_path, original_data))

            # Archive to cold tier or mark as archived
            if tier_manager is not None and hasattr(tier_manager, "cold_archive"):
                try:
                    tier_manager.cold_archive(entry_id, entry_path)
                except Exception:  # justified: fail-open, cold archive failure falls back to status mark
                    # Cold archive failed — mark as archived instead
                    data["status"] = "archived"
                    writer.write_yaml(entry_path, data)
            else:
                data["status"] = "archived"
                writer.write_yaml(entry_path, data)

        except (OSError, StateError) as exc:
            # Archive failed — rollback all processed entries
            logger.exception(
                "consolidation_archive_failed",
                entry_id=entry_id,
                consolidated_id=consolidated_id,
                error=str(exc),
            )
            _rollback_archive(processed, consolidated_id, entries_dir, writer)
            raise

    logger.info(
        "consolidation_archive_complete",
        consolidated_id=consolidated_id,
        archived_count=len(processed),
    )


def _rollback_archive(
    processed: list[tuple[Path, dict[str, object]]],
    consolidated_id: str,
    entries_dir: Path,
    writer: FileStateWriter,
) -> None:
    """Roll back consolidated_into writes on archive failure."""
    for entry_path, original_data in processed:
        try:
            writer.write_yaml(entry_path, original_data)
        except (
            OSError,
            StateError,
        ):  # per-item error handling: log rollback failure per entry, continue rollback
            logger.exception(
                "consolidation_rollback_failed",
                path=str(entry_path),
                consolidated_id=consolidated_id,
            )

    # Delete the consolidated entry file
    slug = consolidated_id.replace("/", "-")
    consolidated_path = entries_dir / f"{slug}.yaml"
    try:
        if consolidated_path.exists():
            consolidated_path.unlink()
    except OSError:
        logger.exception(
            "consolidation_rollback_delete_failed",
            consolidated_id=consolidated_id,
        )
