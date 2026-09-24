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


logger = structlog.get_logger(__name__)


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
