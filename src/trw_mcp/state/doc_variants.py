"""Variant naming for derived artifacts (PRD-CORE-299).

Responsibility: name, locate, write and judge the files other agents or models
derive from one base document (reviews, audits, drafts, rewrites).

Interface: :data:`VARIANT_KINDS`, :class:`VariantName`,
:class:`VariantLocationError`, :func:`variant_name`,
:func:`parse_variant_name`, :func:`list_variants`, :func:`variant_dir`,
:func:`write_variant`, :func:`variant_status`.

Grammar (kind first, operator decision 2026-09-23)::

    STEM "." KIND [ "-" PRODUCER ] [ "-r" ROUND ] ".md"

STEM is the base file name minus ``.md`` (or the whole name for a non-markdown
base). KIND is a closed vocabulary; PRODUCER is a lowercase hyphenated slug of
at most 32 characters that is never ``r<digits>``; ROUND is 1..999 with no
leading zero. A trailing ``-r<digits>`` is always the round.

Invariants:
- :func:`parse_variant_name` is total: it returns a value or None for any str.
- Every name :func:`variant_name` emits parses back to the same fields, and a
  base whose own name already parses as a variant is refused as ambiguous.
- :func:`write_variant` never overwrites: it creates with ``O_EXCL`` and moves
  to the next round on a collision.
- Staleness is the sha256 of the base bytes, never an mtime (git resets mtimes
  on checkout).
- A variant is never written into a directory whose markdown files are
  discovered, counted or auto-loaded (PRD, sprint, bundled-data and client
  agent/skill/rule directories), so no scanner ever counts one.
"""

from __future__ import annotations

import glob
import hashlib
import os
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple

from ruamel.yaml import YAML

from trw_mcp.models.config import get_config
from trw_mcp.state.prd_utils import parse_frontmatter

VARIANT_KINDS: tuple[str, ...] = ("review", "audit", "verdict", "draft", "rewrite", "notes", "summary")
VariantStatus = Literal["fresh", "stale", "orphaned", "unrecorded"]

_MAX_ROUND = 999
_MAX_PRODUCER_LEN = 32
_HEAD_BYTES = 4096  # a variant block is small; frontmatter is read from this head only
_PRODUCER_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# A producer may not end in a round-shaped part ("r12" or "peer-r1"): the name
# x.review-peer-r1.md would then read back as producer "peer", round 1.
_ROUND_TAIL_RE = re.compile(r"(?:\A|-)r[0-9]+\Z")
_TRAILING_ROUND_RE = re.compile(r"-r([0-9]+)\Z")
_KIND_RE = re.compile(rf"({'|'.join(VARIANT_KINDS)})(?:-(.+))?\Z")
# Client config dirs (".claude", ".cursor", ...) whose children with these names
# hold auto-loaded or counted markdown. Structural, so no client is named here.
_CLIENT_DISCOVERY_NAMES = frozenset({"agents", "skills", "rules", "commands", "prompts"})
_BUNDLED_DATA = ("trw-mcp", "src", "trw_mcp", "data")


class VariantName(NamedTuple):
    """The parsed fields of a variant file name."""

    stem: str
    kind: str
    producer: str | None
    round: int | None


class VariantLocationError(ValueError):
    """A variant cannot be placed: discovery directory, escaping base, or rounds exhausted."""


def _valid_producer(producer: str) -> bool:
    return (
        len(producer) <= _MAX_PRODUCER_LEN
        and _PRODUCER_RE.fullmatch(producer) is not None
        and _ROUND_TAIL_RE.search(producer) is None
    )


def _parse_round(digits: str) -> int | None:
    if digits.startswith("0") or len(digits) > len(str(_MAX_ROUND)):
        return None
    return int(digits)


def parse_variant_name(name: str) -> VariantName | None:
    """Parse *name* as a variant file name, or return None when it is not one."""
    if not name.endswith(".md"):
        return None
    stem, dot, segment = name[:-3].rpartition(".")
    if not dot or not stem:
        return None
    rnd: int | None = None
    trailing = _TRAILING_ROUND_RE.search(segment)
    if trailing:
        rnd = _parse_round(trailing.group(1))
        if rnd is None:
            return None
        segment = segment[: trailing.start()]
    match = _KIND_RE.fullmatch(segment)
    if match is None:
        return None
    producer = match.group(2)
    if producer is not None and not _valid_producer(producer):
        return None
    return VariantName(stem, match.group(1), producer, rnd)


def variant_name(base_name: str, kind: str, producer: str | None = None, round: int | None = None) -> str:
    """Format the variant file name of *base_name*; raise ValueError on any invalid part."""
    stem = base_name.removesuffix(".md")
    if not stem or "/" in base_name or "\\" in base_name:
        raise ValueError(f"base must be a plain file name, got {base_name!r}")
    if parse_variant_name(base_name) is not None:
        raise ValueError(f"base {base_name!r} already reads as a variant; its variants would be ambiguous")
    if kind not in VARIANT_KINDS:
        raise ValueError(f"kind {kind!r} is not one of {VARIANT_KINDS}")
    if producer is not None and not _valid_producer(producer):
        raise ValueError(
            f"producer {producer!r} must be a lowercase hyphenated slug of at most 32 chars, not ending in -r<digits>"
        )
    if round is not None and not 1 <= round <= _MAX_ROUND:
        raise ValueError(f"round {round} is outside 1..{_MAX_ROUND}")
    segment = kind + (f"-{producer}" if producer else "") + (f"-r{round}" if round else "")
    return f"{stem}.{segment}.md"


def list_variants(base: Path) -> list[tuple[Path, VariantName]]:
    """The variants beside *base*, sorted by (kind, producer, round); reads names only."""
    stem = base.name.removesuffix(".md")
    found: list[tuple[Path, VariantName]] = []
    for path in base.parent.glob(f"{glob.escape(stem)}.*.md"):
        parsed = parse_variant_name(path.name)
        if parsed is not None and parsed.stem == stem:
            found.append((path, parsed))
    return sorted(found, key=lambda item: (item[1].kind, item[1].producer or "", item[1].round or 0))


def _discovery_prefixes() -> tuple[tuple[str, ...], ...]:
    prds = PurePosixPath(get_config().prds_relative_path).parts
    reqs = prds[:-1]
    return (prds, (*reqs, "archive", "prds"), (*reqs, "sprints"), _BUNDLED_DATA)


def variant_dir(base: Path, root: Path) -> Path:
    """The directory a variant of *base* is written to; raise VariantLocationError when refused.

    Refuses a base whose own name already reads as a variant, a base resolving
    outside *root*, and a discovery directory. Callers use it as the preflight.
    Every check runs on the resolved path, so a symlink is judged by its target.
    """
    base = base.resolve()
    if parse_variant_name(base.name) is not None:
        raise VariantLocationError(f"base {base.name!r} already reads as a variant; its variants would be ambiguous")
    resolved_root = root.resolve()
    directory = base.parent
    if not directory.is_relative_to(resolved_root):
        raise VariantLocationError(f"base {base} resolves outside the project root {resolved_root}")
    parts = directory.relative_to(resolved_root).parts
    client_dir = len(parts) >= 2 and parts[0].startswith(".") and parts[1] in _CLIENT_DISCOVERY_NAMES
    if client_dir or any(parts[: len(prefix)] == prefix for prefix in _discovery_prefixes()):
        raise VariantLocationError(
            f"{PurePosixPath(*parts)} is a discovery directory: a variant there would be counted or loaded"
        )
    return directory


def _variant_block(base_name: str, name: VariantName, **fields: object) -> str:
    block = {
        "variant": {
            "base": base_name,
            "kind": name.kind,
            "producer": name.producer,
            "round": name.round,
            **fields,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    }
    out = StringIO()
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.dump(block, out)
    return f"---\n{out.getvalue()}---\n\n"


def write_variant(
    base: Path,
    root: Path,
    *,
    kind: str,
    producer: str,
    body: str,
    ok: bool,
    role: str | None = None,
    model: str | None = None,
) -> Path:
    """Create the next free round of *base*'s (kind, producer) variant and return its path.

    The ``variant:`` block records the base (relative to the variant's own
    directory), the base's sha256, the fields, and ``ok``; paths and digests
    only, never prompt text.
    """
    base = base.resolve()
    directory = variant_dir(base, root)
    variant_name(base.name, kind, producer, 1)  # validate every part before any I/O
    taken = [n.round or 0 for _, n in list_variants(base) if (n.kind, n.producer) == (kind, producer)]
    digest = hashlib.sha256(base.read_bytes()).hexdigest()
    for rnd in range(max(taken, default=0) + 1, _MAX_ROUND + 1):
        name = variant_name(base.name, kind, producer, rnd)
        try:
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:  # trw-fail-silent-allow: a concurrent writer took this round; take the next
            continue
        block = _variant_block(
            base.name,
            VariantName(base.name.removesuffix(".md"), kind, producer, rnd),
            base_sha256=digest,
            role=role,
            model=model,
            ok=ok,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(block + body.rstrip("\n") + "\n")
        return directory / name
    raise VariantLocationError(f"no free round left for {kind}-{producer} of {base.name} (max {_MAX_ROUND})")


def variant_status(path: Path) -> VariantStatus:
    """fresh / stale by the recorded base sha256; orphaned when the base is gone; else unrecorded."""
    with path.open("rb") as handle:
        head = handle.read(_HEAD_BYTES).decode("utf-8", errors="replace")
    block = parse_frontmatter(head).get("variant")
    if not isinstance(block, dict) or not block.get("base_sha256") or not block.get("base"):
        return "unrecorded"
    base = path.parent / str(block["base"])
    if not base.is_file():
        return "orphaned"
    fresh = hashlib.sha256(base.read_bytes()).hexdigest() == block["base_sha256"]
    return "fresh" if fresh else "stale"
