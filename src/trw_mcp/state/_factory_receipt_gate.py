"""Write-time checks on factory checkpoints: receipt resolution (FACTORY-READY-RECEIPT-RESOLVE) and payload shape
(FACTORY-START-VALIDATE).

Payload shape: a ``factory:1`` message must carry a known kind, an ``attempt``, satisfy the reader's own
:func:`_schema_error`, and hold no key the kind does not define. W1 recorded STARTs with ``"subject"`` instead of
``"attempt"``; the tool accepted them and they surfaced only as a negative start_to_ready that excluded three
attempts. The journal is append-only, so the refusal is at the write, and only for NEW writes: the reader's
classification of history is untouched.

Receipt resolution:

A READY or USED that names a receipt id with no receipt file behind it (a placeholder such as
``build-PENDING`` recorded before ``trw_build_check`` returned) is worthless evidence, and the journal
is append-only: the first READY per attempt wins, so the slip could only be repaired with a new
attempt id. ``trw_checkpoint`` therefore refuses it BEFORE the write.

Only whether the receipt FILE resolves is decided here, with the same :func:`_resolve` the reader
(:mod:`trw_mcp.state._factory_status`) uses. Report-time judgments stay report-time: a receipt bound
to another sha (``build_sha_mismatch``), an unbound one, or one the verifier flags
``binding_unverifiable`` is present and parseable, so it is accepted here and reported there.
A payload that fails the reader's schema is left to the reader, which diagnoses it.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.bootstrap._utils import printable
from trw_mcp.state._factory_status import _ALL_KINDS, CELL_KEYS, _resolve, _schema_error

__all__ = ["factory_payload_refusal", "unresolved_receipts"]

#: Keys every factory payload carries; the per-kind extras are what real workers and the verifier write.
_BASE_KEYS = frozenset({"factory", "kind", "attempt"})
_KIND_KEYS: dict[str, frozenset[str]] = {
    "START": frozenset({"subject_sha", "branch", "base", "note", *CELL_KEYS}),
    "READY": frozenset({"subject_sha", "receipts", "note"}),
    "USED": frozenset({"receipts", "by", "note"}),
    "VOID": frozenset({"reason", "by", "note"}),
}


def _shown(text: object) -> str:
    return printable(str(text))[:40]


def factory_payload_refusal(message: str) -> str | None:
    """Why a ``factory:1`` message must not be recorded, naming the key at fault; ``None`` when it may be."""
    payload = json.loads(message)
    kind = payload.get("kind")
    if kind not in _ALL_KINDS:
        return f"unknown kind {_shown(kind)!r}: use one of {', '.join(_ALL_KINDS)}"
    if "attempt" not in payload:
        others = sorted(set(payload) - _BASE_KEYS - _KIND_KEYS[kind])
        hint = f" (found {', '.join(_shown(k) for k in others)} instead)" if others else ""
        return f"missing key: attempt{hint}; without it the {kind} never joins its attempt"
    if (problem := _schema_error(payload)) is not None:
        return problem
    unknown = sorted(set(payload) - _BASE_KEYS - _KIND_KEYS[kind])
    if unknown:
        allowed = ", ".join(sorted(_BASE_KEYS | _KIND_KEYS[kind]))
        return f"unknown key(s) for {kind}: {', '.join(_shown(k) for k in unknown)} (allowed: {allowed})"
    return None


_GATED_KINDS = ("READY", "USED")
_PRESENT = frozenset({"referenced", "unbound", "build_sha_mismatch"})


def unresolved_receipts(run: Path, message: str) -> list[str]:
    """``family:id (state)`` for each receipt a READY/USED *message* names that does not resolve under *run*."""
    payload = json.loads(message)
    if payload.get("kind") not in _GATED_KINDS or _schema_error(payload) is not None:
        return []
    run = run.resolve()
    subject = payload.get("subject_sha")
    found: list[str] = []
    for family, refs in payload["receipts"].items():
        for ref in refs:
            label, state = _resolve(run, family, ref, subject)
            if state not in _PRESENT:
                found.append(f"{label} ({state})")
    return found
