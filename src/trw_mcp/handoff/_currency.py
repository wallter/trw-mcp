"""Currency of a handoff record: who supersedes it, which record is current, and whether its subject forked.

Belongs to the :mod:`trw_mcp.handoff` package (``trw-mcp handoff check``, R-SUP-1..R-SUP-4). Records
of one ``subject`` can live in several handoffs directories (the project's ``.trw/handoffs/``, every
run's ``handoffs/``, and wherever the record itself sits), so all of them are scanned. Only valid
handoff records count. A ``minimal`` file record is an advisory note (R-SUP-4): it can be superseded
but never forms a fork, and an expired sibling is no longer a current head.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from trw_mcp.handoff._jcs import digest
from trw_mcp.handoff._rules import instant
from trw_mcp.handoff._validate import AhrInputError, AhrParseError, load, validate

__all__ = ["handoff_files", "supersession"]

JsonDoc = dict[str, Any]
_RANK = {"minimal": 0, "standard": 1, "critical": 2}
_MAX_RECORD_BYTES = 256 * 1024  # a sealed record is <= 32 KiB canonical; pretty-printed copies stay well under


def handoff_files(record: Path, project: Path) -> list[Path]:
    """Every ``*.json`` in the record's directory, ``<project>/.trw/handoffs/`` and ``.trw/runs/**/handoffs/``."""
    base = project.resolve()
    found = set(record.parent.glob("*.json"))
    trw = base / ".trw"
    found |= set((trw / "handoffs").glob("*.json"))
    found |= set((trw / "runs").glob("**/handoffs/*.json"))  # ** does not follow symlinked dirs (3.13+)
    keep = []
    for path in sorted(found):
        try:
            real = path.resolve()
            small = path.is_file() and path.stat().st_size <= _MAX_RECORD_BYTES
        except OSError:  # trw-fail-silent-allow: an unreadable entry is not a sibling record
            continue
        if small and (real.is_relative_to(base) or real.parent == record.parent):
            keep.append(path)
    return keep


def _expired(doc: JsonDoc, now: datetime) -> bool:
    raw = doc.get("expires_at")
    try:
        return isinstance(raw, str) and instant(raw) <= now
    except ValueError:  # trw-fail-silent-allow: an unparseable expiry is reported by validity, not here
        return False


def _records(doc: JsonDoc, path: Path, files: list[Path]) -> tuple[dict[str, tuple[JsonDoc, Path]], list[str]]:
    """Valid same-subject records by ``handoff_id``, plus ids seen with two different digests."""
    me = str(doc["handoff_id"])
    records: dict[str, tuple[JsonDoc, Path]] = {me: (doc, path)}
    duplicates: set[str] = set()
    for other in files:
        if other.resolve() == path:
            continue
        try:
            sib = load(other)
        except (AhrParseError, AhrInputError, OSError):  # trw-fail-silent-allow: not a record, not a sibling
            continue
        if sib.get("type") != "handoff" or sib.get("subject") != doc.get("subject") or validate(sib):
            continue
        hid = str(sib["handoff_id"])
        if hid in records:
            if digest(sib) != digest(records[hid][0]):
                duplicates.add(hid)  # one id, two contents: neither copy can be trusted as "the" record
            continue
        records[hid] = (sib, other)
    return records, sorted(duplicates)


def supersession(doc: JsonDoc, path: Path, files: list[Path], now: datetime) -> JsonDoc:
    """R-SUP-1/2/3: status ``current``, ``superseded`` or ``fork``, with tier downgrades and duplicate ids."""
    records, duplicates = _records(doc, path, files)
    me = str(doc["handoff_id"])
    my_digest = digest(doc)
    successors: dict[str, set[str]] = {hid: set() for hid in records}
    stale: list[str] = []
    downgrades: list[JsonDoc] = []
    for hid, (other, _) in records.items():
        for prior in other.get("supersedes", []):
            pid = prior["handoff_id"]
            if pid not in successors:
                continue
            successors[pid].add(hid)
            if pid == me and prior["digest"] != my_digest:
                stale.append(hid)  # it superseded a different version of these bytes
            if _RANK[other["tier"]] < _RANK[records[pid][0]["tier"]]:
                downgrades.append({"handoff_id": hid, "supersedes": pid})  # R-SUP-2: a tier no lower

    def is_head(hid: str) -> bool:
        rec = records[hid][0]
        if successors[hid] or rec["tier"] == "minimal":
            return False
        return hid == me or not _expired(rec, now)

    heads = sorted(filter(is_head, records), key=lambda h: instant(records[h][0]["created_at"]))
    status = "fork" if len(heads) > 1 else "superseded" if successors[me] else "current"
    out: JsonDoc = {"status": status, "superseded_by": sorted(successors[me])}
    newest = heads[-1] if heads else me
    if newest != me:
        rec, where = records[newest]
        out["newest"] = {"handoff_id": newest, "path": str(where), "created_at": rec["created_at"]}
    if len(heads) > 1:
        out["current"] = heads
    if stale:
        out["digest_mismatch"] = sorted(stale)
    if downgrades:
        out["tier_downgrade"] = downgrades
    if duplicates:
        out["duplicate_id"] = duplicates
    return out
