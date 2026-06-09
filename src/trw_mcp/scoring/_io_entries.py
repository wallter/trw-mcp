"""YAML entry read/write helpers for the scoring I/O boundary.

Belongs to the ``_io_boundary.py`` facade. Re-exported there for back-compat
so ``_correlation.py`` / ``_decay.py`` do not import ``FileStateReader`` /
``FileStateWriter`` from the state layer directly (PRD-FIX-061-FR05).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from trw_mcp.scoring._io_boundary import _PendingUpdate

logger = structlog.get_logger(__name__)


def _write_pending_entries(
    pending_updates: list[_PendingUpdate],
) -> list[str]:
    """Write pending Q-value updates to YAML files.

    PRD-FIX-061-FR05: Extracted from ``process_outcome`` so that
    ``_correlation.py`` does not need to import ``FileStateWriter``
    from the state layer.

    Args:
        pending_updates: List of pending update tuples from process_outcome.

    Returns:
        List of learning IDs that were successfully written.
    """
    from trw_mcp.state.persistence import FileStateWriter

    updated_ids: list[str] = []
    writer = FileStateWriter()
    for lid, entry_path, data, _q_new, _q_obs, _history in pending_updates:
        if entry_path is None:
            # No YAML path — this entry exists in SQLite only.  Skip the YAML
            # write and do NOT report it as written; the SQLite write-back in
            # _batch_sync_to_sqlite is the authoritative persistence path for
            # SQLite-only entries and the caller tracks those separately.
            logger.debug("q_value_yaml_skip_no_path", learning_id=lid)
            continue
        try:
            writer.write_yaml(entry_path, data)
        except Exception:  # justified: fail-open, YAML write failures exclude entry from updated_ids
            logger.warning(
                "q_value_yaml_write_failed",
                learning_id=lid,
                exc_info=True,
            )
            continue  # Do not claim this ID was updated
        updated_ids.append(lid)
    return updated_ids


def _load_entries_from_dir(entries_dir: Path) -> Iterator[dict[str, object]]:
    """Load entry dicts from a YAML entries directory.

    Yields parsed dicts for each readable YAML entry file.
    Silently skips files that fail to parse.

    Args:
        entries_dir: Directory containing YAML entry files.

    Yields:
        Parsed entry dicts.
    """
    from trw_mcp.state._helpers import iter_yaml_entry_files
    from trw_mcp.state.persistence import FileStateReader

    reader = FileStateReader()
    for yaml_file in iter_yaml_entry_files(entries_dir):
        try:
            yield reader.read_yaml(yaml_file)
        except Exception:  # justified: fail-open, skip unreadable YAML entries  # noqa: S112
            continue
