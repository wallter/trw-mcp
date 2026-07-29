"""FR07 hard enforcement: the open-violation state + deliver-gate seam.

PostToolUse exit-2 is treated strictly as high-visibility feedback (the write has
already happened; upstream semantics cannot un-run it). The HARD enforcement is
the open-violation state: while any violation is open,
:func:`intent_violation_gate_block` returns a BLOCK-class message for the
existing, machine-enforced deliver gate.

Two sources, deliberately: the runtime marker file (fast, cheap) AND the
append-only FR03 ledger (durable authority). Deleting or corrupting the marker
therefore cannot clear a block — a corrupt marker fails CLOSED, and a deleted one
falls back to the hash-chained ledger, whose own tampering is caught by
``verify_override_ledger`` and by C9 at commit time.

Both sources fail closed SYMMETRICALLY. Making only the marker fail closed simply
moved the hole one file over: ``rm`` of the marker AND the ledger, a truncated
ledger, or a corrupt ledger all returned NO_BLOCK (probe finding N4, 2026-07-24).
:func:`trw_mcp.security.intent_contract.ledger.ledger_tamper_reason` now
distinguishes "never existed" (inert project — stays clean) from "existed and is
now gone or unreadable" (block), using the same durable-signal shape as the
enrollment marker.

Integration seam (one function; the deliver gate needs nothing else)::

    from trw_mcp.security.intent_contract.violations import intent_violation_gate_block
    block = intent_violation_gate_block(trw_dir.parent)
    if block:
        result["intent_violation_block"] = block   # NO_ESCAPE / STRUCTURED gate

A violation is cleared only by a falsifier re-pass (:func:`clear_violation`) or by
an R3 break-glass override — both append to the ledger, so a cleared block is as
auditable as the block itself.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.security.intent_contract._atomic_json import atomic_write_json, locked
from trw_mcp.security.intent_contract.ledger import (
    LedgerError,
    ledger_tamper_reason,
    open_violation_keys,
    record_resolution,
)
from trw_mcp.security.intent_contract.paths import LEDGER_PATH, OPEN_VIOLATIONS_PATH, repo_root

__all__ = [
    "clear_violation",
    "intent_violation_gate_block",
    "open_violations",
    "record_gate_override",
    "record_open_violation",
    "violations_path",
]

#: Returned by :func:`_load` when the marker exists but cannot be parsed.
_CORRUPT = "corrupt"


def violations_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / OPEN_VIOLATIONS_PATH


def _load(path: Path) -> list[dict[str, str]] | str:
    """Entries, or ``_CORRUPT``. Absence is empty; unparseable is NOT."""
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _CORRUPT
    if not isinstance(parsed, list):
        return _CORRUPT
    return [item for item in parsed if isinstance(item, dict)]


def _mutate(root: Path, mutate: Callable[[list[dict[str, str]]], list[dict[str, str]]]) -> bool:
    """Load, apply *mutate*, and store under ONE exclusive lock.

    The lock MUST span load and store: with that window open, a concurrent clear
    writes a stale snapshot and silently resurrects or drops another claim's open
    violation (probe-verified). Returns True when the marker was corrupt.
    """
    path = violations_path(root)
    with locked(path):
        current = _load(path)
        entries: list[dict[str, str]] = [] if isinstance(current, str) else current
        atomic_write_json(path, mutate(entries))
        return isinstance(current, str)


def record_open_violation(
    root: Path,
    *,
    claim_id: str,
    file_path: str,
    claim_text: str,
    falsifier: str,
    detail: str = "",
) -> None:
    """Persist one open violation (idempotent per ``claim_id`` + ``file_path``)."""

    def _apply(entries: list[dict[str, str]]) -> list[dict[str, str]]:
        kept = [item for item in entries if (item.get("claim_id"), item.get("file_path")) != (claim_id, file_path)]
        kept.append(
            {
                "claim_id": claim_id,
                "file_path": file_path,
                "claim_text": claim_text,
                "falsifier": falsifier,
                "detail": detail,
                "opened_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return kept

    _mutate(root, _apply)


def clear_violation(
    root: Path,
    *,
    claim_id: str,
    file_path: str,
    session_id: str = "",
    reason: str = "falsifier re-passed",
) -> bool:
    """Clear one open violation from BOTH sources; True when one was open.

    Propagates :class:`LedgerError` when the ledger is unreadable: the callers on
    this path (the FR07 post-edit entry point) map any exception to exit 2, so an
    untrustworthy ledger blocks rather than silently clearing.
    """
    was_open = (claim_id, file_path) in open_violation_keys(root)

    def _apply(entries: list[dict[str, str]]) -> list[dict[str, str]]:
        nonlocal was_open
        remaining = [item for item in entries if (item.get("claim_id"), item.get("file_path")) != (claim_id, file_path)]
        was_open = was_open or len(remaining) != len(entries)
        return remaining

    _mutate(root, _apply)
    if not was_open:
        return False
    try:
        record_resolution(claim_id=claim_id, file_path=file_path, session_id=session_id, reason=reason, root=root)
    except (LedgerError, OSError):
        # The marker is already clear; a ledger failure must not resurrect the
        # block, and verify_override_ledger surfaces the chain problem itself.
        return True
    return True


def open_violations(root: Path | None = None) -> list[dict[str, str]]:
    """Marker entries only (``[]`` when the marker is corrupt — see the gate)."""
    current = _load(violations_path(root))
    return [] if isinstance(current, str) else current


def record_gate_override(root: Path | None = None, *, session_id: str = "", reason: str = "") -> bool:
    """Deliver-gate override seam: ledger EVERY open violation, then clear it.

    Returns False (override REFUSED) when the reason is empty, when either
    enforcement source is untrustworthy, or when a ledger write fails, so an
    override can never be granted without a record — success criterion 3 ("every
    override is ledgered") holds at this seam rather than by convention at the
    call site.

    The corrupt-source refusals are load-bearing, not defensive padding: a corrupt
    marker yields NO keys, and a no-keys override used to return True — granting
    an override with no ledger entry at all, which is the exact signature of the
    original unledgered-override finding (probe finding N5, 2026-07-24).
    """
    from trw_mcp.security.intent_contract.ledger import record_override

    resolved = root or repo_root()
    if not reason.strip():
        return False
    if isinstance(_load(violations_path(resolved)), str):
        return False
    if ledger_tamper_reason(resolved) is not None:
        return False
    keys = open_violation_keys(resolved) | {
        (item.get("claim_id", ""), item.get("file_path", "")) for item in open_violations(resolved)
    }
    if not keys:
        return True
    for claim_id, file_path in sorted(keys):
        try:
            record_override(
                claim_id=claim_id,
                file_path=file_path,
                control_point="FR07",
                session_id=session_id,
                reason=reason.strip(),
                block_class="blocked",
                root=resolved,
            )
        except (LedgerError, OSError):
            return False
        _drop(resolved, claim_id, file_path)
    return True


def _drop(root: Path, claim_id: str, file_path: str) -> None:
    _mutate(
        root,
        lambda entries: [
            item for item in entries if (item.get("claim_id"), item.get("file_path")) != (claim_id, file_path)
        ],
    )


def intent_violation_gate_block(root: Path | None = None) -> str | None:
    """Deliver-gate seam: a BLOCK-class message while any violation is open.

    ``None`` (no block) only when BOTH sources are readable AND neither holds an
    open violation — including for every project that never enrolled, whose files
    simply do not exist.
    """
    resolved = root or repo_root()
    current = _load(violations_path(resolved))
    if isinstance(current, str):
        return (
            "BLOCKED: the intent-contract open-violation marker is CORRUPT and cannot be read.\n"
            "  It is enforcement state, so an unreadable marker fails closed — a violation\n"
            "  may still be outstanding.\n\n"
            f"ACTION: inspect {OPEN_VIOLATIONS_PATH}, re-run the claim's falsifier, and let the\n"
            "  post-edit check rewrite the marker."
        )

    tampered = ledger_tamper_reason(resolved)
    if tampered is not None:
        return (
            f"BLOCKED: the intent-contract override LEDGER cannot be trusted — {tampered}.\n"
            "  It is the durable authority behind the runtime marker, so a missing or\n"
            "  unreadable ledger fails closed exactly as a corrupt marker does: a violation\n"
            "  may still be outstanding, and deleting the evidence is not a resolution.\n\n"
            f"ACTION: restore {LEDGER_PATH} and its checkpoint from git, or have an operator\n"
            "  re-establish the chain as a signed, reviewed change."
        )

    entries = list(current)
    known = {(item.get("claim_id", ""), item.get("file_path", "")) for item in entries}
    for claim_id, file_path in sorted(open_violation_keys(resolved) - known):
        entries.append(
            {
                "claim_id": claim_id,
                "file_path": file_path,
                "claim_text": "(open per the FR03 ledger — the runtime marker no longer lists it)",
                "falsifier": "<see ledger>",
            }
        )
    if not entries:
        return None

    lines = [
        f"  - {item.get('claim_id', '?')} on {item.get('file_path', '?')}: "
        f"{item.get('claim_text', '')} (falsifier: {item.get('falsifier', '?')})"
        for item in entries
    ]
    return (
        f"BLOCKED: an intent-contract must_not_happen violation is still open ({len(entries)} outstanding).\n"
        + "\n".join(lines)
        + "\n\nACTION: fix the violation so the claim's falsifier passes (the post-edit check "
        "clears it on re-pass), or have an operator mint a break-glass token — every "
        "override is recorded in the FR03 ledger."
    )
