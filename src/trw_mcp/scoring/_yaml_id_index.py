"""Learning-id extraction and index construction for the scoring I/O boundary.

Extracted from ``_io_boundary`` under the 350-effective-LOC module gate. These
two functions are the whole cost of the index: one reads a single top-level
scalar out of a file, the other does it across a directory. Keeping them here
lets the boundary module stay about caching policy rather than parsing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["_build_yaml_path_index", "_read_learning_id"]


class _YamlReader(Protocol):
    """The single method the fallback parse needs."""

    def read_yaml(self, path: Path) -> dict[str, object]: ...


#: A top-level ``id:`` scalar, matched without composing the document.
#: Anchored to column 0 so a nested ``id:`` inside a mapping cannot match.
_TOP_LEVEL_ID_RE = re.compile(
    rb"""^id:[ \t]*(?:"([^"\n]+)"|'([^'\n]+)'|([^\s#][^\n#]*?))[ \t]*(?:#.*)?$""",
    re.MULTILINE,
)


def _read_learning_id(reader: _YamlReader, yaml_file: Path) -> str | None:
    """Read a YAML entry id, returning None when the entry is unreadable.

    The index needs ONE top-level scalar per file, so composing the whole
    document to get it is the wrong cost. Full ruamel parsing of this repo's
    6,805-entry store took 32 seconds; a regex over the same bytes takes well
    under a second, and the parse is kept only as the fallback for a file whose
    id the pattern cannot find (a folded scalar, an unusual quoting style).

    That mattered because the cost compounded: see ``_get_yaml_path_index``.
    """
    try:
        raw = yaml_file.read_bytes()
    except OSError:  # justified: fail-open, skip unreadable entries during index build
        logger.debug("yaml_path_index_entry_skipped", path=str(yaml_file), exc_info=True)
        return None

    match = _TOP_LEVEL_ID_RE.search(raw)
    if match is not None:
        raw_id = next((group for group in match.groups() if group), None)
        # A block-scalar indicator, anchor or alias is a POINTER to the value,
        # not the value: `id: >-` would otherwise be read as the literal id
        # ">-". Fall through to the parser rather than index a bogus key.
        if raw_id is not None and raw_id[:1] in (b"|", b">", b"&", b"*"):
            raw_id = None
        if raw_id is not None:
            try:
                lid = raw_id.decode("utf-8").strip()
            except UnicodeDecodeError:
                lid = ""
            if lid:
                return lid

    # Fallback: the pattern did not find a top-level id, so pay for a real parse
    # rather than silently dropping the entry from the index.
    try:
        data = reader.read_yaml(yaml_file)
    except Exception:  # justified: fail-open, skip unreadable entries during index build
        logger.debug("yaml_path_index_entry_skipped", path=str(yaml_file), exc_info=True)
        return None

    lid_any = data.get("id")
    return lid_any if isinstance(lid_any, str) and lid_any else None


def _build_yaml_path_index(entries_dir: Path) -> dict[str, Path]:
    """Scan entries_dir once and build {learning_id -> yaml_path} map.

    Reads the ``id`` field from each YAML file. Skips files that fail
    to parse. This is O(N) but runs once per TTL window instead of
    once per lookup (which was O(M*N) total).
    """
    from trw_mcp.state._helpers import iter_yaml_entry_files
    from trw_mcp.state.persistence import FileStateReader

    index: dict[str, Path] = {}
    reader = FileStateReader()
    for yaml_file in iter_yaml_entry_files(entries_dir):
        lid = _read_learning_id(reader, yaml_file)
        if lid is not None:
            index[lid] = yaml_file
    return index
