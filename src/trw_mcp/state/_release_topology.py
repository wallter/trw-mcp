"""The monorepo's package taxonomy, read from the one file that owns it.

``release-packages.yaml`` lives at the monorepo root and is the single source of
truth for which packages exist, where they live, and how they are distributed.
It is deliberately NOT part of the ``trw-mcp`` subtree, so a public install does
not have it — and that absence is the point: a published wheel must not be able
to enumerate TRW's proprietary siblings.

This module exists because the taxonomy had been hand-copied into shipped source
three times, and the copies had drifted from each other and from the manifest.
Two of them additionally named proprietary packages, which is the leak the
manifest's non-shipped location is meant to prevent. Every reader now derives.

Fails OPEN to the public packages, never to an empty tuple: a caller that gets
nothing back cannot tell "no manifest" from "no packages", and the difference
decides whether a path resolves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

#: The two packages TRW publishes. Always present, manifest or not.
PUBLIC_PACKAGE_DIRS: tuple[str, ...] = ("trw-mcp", "trw-memory")

RELEASE_TOPOLOGY_FILENAME = "release-packages.yaml"

#: Matches ``  - key: x`` / ``    dir: y`` / ``    tier: z`` / ``    manifest_kind: k``
#: in the flat ``packages:`` list. A line reader rather than a YAML parse keeps
#: this dependency-free and bounded; the file's shape is fixed and gate-checked.
_FIELD_RE = re.compile(r"^\s*-?\s*(key|dir|tier|manifest_kind)\s*:\s*(\S+)\s*$")


@dataclass(frozen=True, slots=True)
class TopologyEntry:
    """One declared package. ``tier`` is ``public`` or ``proprietary``."""

    key: str
    directory: str
    tier: str
    manifest_kind: str


def read_topology(root: Path) -> tuple[TopologyEntry, ...]:
    """Parse the topology manifest at ``root``; empty when absent or unreadable."""
    manifest = root / RELEASE_TOPOLOGY_FILENAME
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError:
        # trw-fail-silent-allow: an absent manifest is the NORMAL public-install case, not an error; the empty tuple is consumed only by package_dirs, which then returns the two PUBLIC packages rather than nothing, so no caller can mistake this for "no packages exist"
        return ()
    entries: list[TopologyEntry] = []
    current: dict[str, str] = {}

    def flush() -> None:
        key, directory = current.get("key"), current.get("dir")
        if key and directory:
            entries.append(
                TopologyEntry(
                    key=key,
                    directory=directory,
                    tier=current.get("tier", "proprietary"),
                    manifest_kind=current.get("manifest_kind", ""),
                )
            )

    for line in text.splitlines():
        match = _FIELD_RE.match(line)
        if match is None:
            continue
        field, value = match.group(1), match.group(2)
        if field == "key":
            flush()
            current = {}
        current[field] = value
    flush()
    if not entries:
        logger.warning("release_topology_empty", path=str(manifest))
    return tuple(entries)


def package_dirs(root: Path) -> tuple[str, ...]:
    """Every declared package directory, public ones first and always present.

    In the monorepo this is the full taxonomy. In a public install the manifest
    is absent and this is exactly :data:`PUBLIC_PACKAGE_DIRS` — which is both the
    correct answer (no other directory exists there) and the safe one (the wheel
    names nothing it should not).
    """
    declared = [entry.directory for entry in read_topology(root) if entry.directory not in PUBLIC_PACKAGE_DIRS]
    return (*PUBLIC_PACKAGE_DIRS, *sorted(set(declared)))
