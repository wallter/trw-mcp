"""FR03: append-only, hash-chained, checkpoint-anchored override ledger.

Chain shape follows the two in-repo precedents (``meta_tune/audit.py``,
``state/requirements_registry.py``): ``entry_hash = SHA256(prev_hash +
canonical_json(entry_sans_hash))``. What is NEW here is the co-located
checkpoint: neither precedent anchors to anything outside the file it protects,
so a wholesale-replaced, internally-consistent genesis chain self-verifies. The
checkpoint closes that (R13).

There is deliberately NO configurable ledger path — a config knob would be a
parallel-ledger substitution surface.

**Threat model — honest limits (probe-verified 2026-07-24).** The checkpoint is
co-located with the ledger, so it shares one trust domain: an attacker who
rewrites BOTH files with a self-consistent forged pair passes
:func:`verify_override_ledger`, and no in-file mechanism can detect that. What
remains is git: C9 treats the ledger, the checkpoint, and the approval records
as control-plane paths, so COMMITTING a forged pair requires a verified
signature (FR01) and an uncommitted forged pair is detectable only against
history (FR08's walk / ``git diff``). The corollary is deliberate: a commit that
carries legitimate new ledger entries must also be signed.

Run ``python3 -m trw_mcp.security.intent_contract.ledger verify`` to check the
chain from a shell.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from trw_mcp.security.intent_contract._atomic_json import atomic_write_json, locked
from trw_mcp.security.intent_contract._git_run import path_in_history
from trw_mcp.security.intent_contract.paths import (
    CHECKPOINT_PATH,
    LEDGER_PATH,
    parse_root_arg,
    repo_root,
    stray_options,
)

__all__ = [
    "GENESIS_PREV",
    "LedgerError",
    "LedgerVerification",
    "checkpoint_path",
    "ledger_path",
    "ledger_tamper_reason",
    "open_violation_keys",
    "record_override",
    "record_resolution",
    "record_violation",
    "verify_override_ledger",
]

GENESIS_PREV = "0" * 64

ControlPoint = Literal["FR01", "FR02", "FR05", "FR07", "FR10"]
BlockClass = Literal["blocked", "false_block", "break_glass", "violation", "resolved"]


class LedgerError(RuntimeError):
    """The ledger cannot be appended to safely — always maps to fail-closed."""


class LedgerVerification(BaseModel):
    """Result of a full chain + checkpoint verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    entry_count: int
    head_hash: str
    broken_index: int | None = None
    reason: str = ""


def ledger_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / LEDGER_PATH


def checkpoint_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / CHECKPOINT_PATH


def _canonical(entry_sans_hash: dict[str, object]) -> bytes:
    return json.dumps(entry_sans_hash, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _entry_hash(prev_hash: str, entry_sans_hash: dict[str, object]) -> str:
    digest = hashlib.sha256()
    digest.update(prev_hash.encode("ascii"))
    digest.update(_canonical(entry_sans_hash))
    return digest.hexdigest()


def _read_entries(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        # Unreadable bytes are NOT an empty ledger. Letting UnicodeDecodeError
        # escape as-is meant a binary-garbage ledger crashed the verifier instead
        # of failing it, and the callers' `except LedgerError` never fired.
        raise LedgerError(f"ledger could not be read ({type(exc).__name__})") from exc
    entries: list[dict[str, object]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except ValueError as exc:
            raise LedgerError(f"ledger line {len(entries)} is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise LedgerError(f"ledger line {len(entries)} is not an object")
        entries.append(parsed)
    return entries


def _read_checkpoint(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ledger_id": "", "entry_count": -1, "head_hash": "<unreadable>"}
    return parsed if isinstance(parsed, dict) else {"ledger_id": "", "entry_count": -1, "head_hash": "<invalid>"}


def _walk_chain(entries: list[dict[str, object]]) -> tuple[str, int | None]:
    """Return ``(head_hash, first_broken_index)``."""
    prev = GENESIS_PREV
    for index, entry in enumerate(entries):
        recorded = str(entry.get("entry_hash", ""))
        sans = {key: value for key, value in entry.items() if key != "entry_hash"}
        if str(entry.get("prev_hash", "")) != prev or _entry_hash(prev, sans) != recorded:
            return prev, index
        prev = recorded
    return prev, None


def verify_override_ledger(root: Path | None = None) -> LedgerVerification:
    """Verify the internal chain AND its anchoring checkpoint."""
    path = ledger_path(root)
    checkpoint = _read_checkpoint(checkpoint_path(root))
    try:
        entries = _read_entries(path)
    except LedgerError as exc:
        return LedgerVerification(valid=False, entry_count=0, head_hash="", reason=str(exc))

    head, broken = _walk_chain(entries)
    if broken is not None:
        return LedgerVerification(
            valid=False,
            entry_count=len(entries),
            head_hash=head,
            broken_index=broken,
            reason=f"chain broken at entry {broken}",
        )
    if checkpoint is None:
        if entries:
            return LedgerVerification(
                valid=False,
                entry_count=len(entries),
                head_hash=head,
                reason="checkpoint missing for a non-empty ledger",
            )
        return LedgerVerification(valid=True, entry_count=0, head_hash=GENESIS_PREV)
    recorded_count = checkpoint.get("entry_count", -1)
    recorded_count = recorded_count if isinstance(recorded_count, int) else -1
    if recorded_count != len(entries) or str(checkpoint.get("head_hash", "")) != head:
        return LedgerVerification(
            valid=False,
            entry_count=len(entries),
            head_hash=head,
            reason="checkpoint mismatch (truncation or wholesale re-genesis)",
        )
    return LedgerVerification(valid=True, entry_count=len(entries), head_hash=head)


def _ledger_existed(root: Path) -> bool:
    """Durable evidence that this project HAS a ledger, held outside the ledger file.

    Two independent signals, either sufficient: the co-located checkpoint (which
    :func:`_append` rewrites with every entry) and git history (HEAD first — see
    :func:`trw_mcp.security.intent_contract._git_run.path_in_history`).

    A project that never recorded an override has neither, so it stays inert.
    """
    if checkpoint_path(root).exists():
        return True
    return path_in_history(root, LEDGER_PATH)


def ledger_tamper_reason(root: Path | None = None) -> str | None:
    """Why the override ledger cannot be trusted right now — ``None`` when it can.

    Three states, and only the first is clean:

    * never existed (no file, no checkpoint, nothing in git history) -> ``None``.
      Inert projects that never recorded an override are untouched.
    * existed and is now GONE -> a reason. ``rm .trw/contracts/intent-override-
      ledger.jsonl`` (with the runtime marker) used to read as "nothing is open"
      and returned NO_BLOCK from the deliver gate (probe finding N4, 2026-07-24).
    * present but unreadable, chain-broken, or checkpoint-mismatched -> a reason.
      A corrupt LEDGER now fails closed exactly as a corrupt MARKER always did;
      the asymmetry between the two was the second half of the same finding.
    """
    resolved = root or repo_root()
    if not ledger_path(resolved).exists():
        if _ledger_existed(resolved):
            return f"{LEDGER_PATH} is MISSING although this project has recorded overrides before"
        return None
    state = verify_override_ledger(resolved)
    return None if state.valid else f"{LEDGER_PATH} does not verify: {state.reason}"


def _append(root: Path | None, entry_sans_hash: dict[str, object]) -> dict[str, object]:
    """Append one chained entry and re-anchor the checkpoint, under one lock.

    Keeps its own bespoke body rather than the shared load/mutate/store helper:
    the verify-head-before-append step below is what stops a new tail laundering
    an already-broken chain, and it has no analogue in the other state files.
    """
    path = ledger_path(root)
    checkpoint_file = checkpoint_path(root)
    with locked(path, append=True) as fd:
        # Verify-head-before-append: never extend a chain that already fails
        # its own checkpoint, or the tamper would be laundered by the new tail.
        state = verify_override_ledger(root)
        if not state.valid:
            raise LedgerError(f"refusing to append to an invalid ledger: {state.reason}")
        existing = _read_checkpoint(checkpoint_file)
        ledger_id = str(existing.get("ledger_id", "")) if existing else ""
        if not ledger_id:
            ledger_id = uuid.uuid4().hex
        entry = dict(entry_sans_hash)
        entry["seq"] = state.entry_count
        entry["prev_hash"] = state.head_hash
        record = dict(entry)
        record["entry_hash"] = _entry_hash(state.head_hash, entry)
        os.write(fd, (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        os.fsync(fd)
        atomic_write_json(
            checkpoint_file,
            {
                "ledger_id": ledger_id,
                "entry_count": state.entry_count + 1,
                "head_hash": str(record["entry_hash"]),
            },
        )
        return record


def record_override(
    *,
    claim_id: str,
    file_path: str,
    control_point: ControlPoint,
    session_id: str,
    reason: str,
    block_class: BlockClass,
    root: Path | None = None,
) -> dict[str, object]:
    """Append exactly one override entry. An empty *reason* is rejected."""
    if not reason.strip():
        raise LedgerError("override reason must be non-empty")
    return _append(
        root,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "override",
            "control_point": control_point,
            "claim_id": claim_id,
            "file_path": file_path,
            "session_id": session_id,
            "reason": reason.strip(),
            "block_class": block_class,
        },
    )


def record_violation(
    *,
    claim_id: str,
    file_path: str,
    session_id: str,
    reason: str,
    root: Path | None = None,
) -> dict[str, object]:
    """Append one FR07 falsifier-violation entry (not an override)."""
    if not reason.strip():
        raise LedgerError("violation reason must be non-empty")
    return _append(
        root,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "violation",
            "control_point": "FR07",
            "claim_id": claim_id,
            "file_path": file_path,
            "session_id": session_id,
            "reason": reason.strip(),
            "block_class": "violation",
        },
    )


def record_resolution(
    *,
    claim_id: str,
    file_path: str,
    session_id: str,
    reason: str,
    root: Path | None = None,
) -> dict[str, object]:
    """Append one resolution entry, closing an open FR07 violation."""
    if not reason.strip():
        raise LedgerError("resolution reason must be non-empty")
    return _append(
        root,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "resolution",
            "control_point": "FR07",
            "claim_id": claim_id,
            "file_path": file_path,
            "session_id": session_id,
            "reason": reason.strip(),
            "block_class": "resolved",
        },
    )


def open_violation_keys(root: Path | None = None) -> set[tuple[str, str]]:
    """``{(claim_id, file_path)}`` still open according to the append-only ledger.

    The ledger is the DURABLE authority behind the runtime marker: deleting the
    marker cannot clear a block, because the violation/resolution history lives
    in a hash-chained file whose tampering is independently detectable.

    Raises :class:`LedgerError` when the ledger exists but cannot be parsed. It
    used to swallow that into an empty set, which made an unreadable ledger
    indistinguishable from a clean one at every seam that consumed it (probe
    finding N4). Callers on an enforcement path must treat the raise as a block;
    :func:`ledger_tamper_reason` is the non-raising form for gate messages.
    """
    path = ledger_path(root)
    if not path.exists():
        return set()
    entries = _read_entries(path)
    open_keys: set[tuple[str, str]] = set()
    for entry in entries:
        key = (str(entry.get("claim_id", "")), str(entry.get("file_path", "")))
        kind = str(entry.get("kind", ""))
        if kind == "violation":
            open_keys.add(key)
        elif kind in {"resolution", "override"}:
            open_keys.discard(key)
    return open_keys


def main(argv: list[str] | None = None) -> int:
    """``python3 -m trw_mcp.security.intent_contract.ledger verify [--root PATH]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    root_arg, args = parse_root_arg(args)
    stray = stray_options(args)
    if stray:
        # `verify --root` (no value) used to verify the DEFAULT root's chain —
        # a clean report about a project the operator never asked about.
        print(f"unrecognized argument(s): {' '.join(stray)}", file=sys.stderr)
    if stray or not args or args[0] != "verify":
        print("usage: python3 -m trw_mcp.security.intent_contract.ledger verify [--root PATH]", file=sys.stderr)
        return 2
    result = verify_override_ledger(root_arg)
    print(result.model_dump_json())
    return 0 if result.valid else 1


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
