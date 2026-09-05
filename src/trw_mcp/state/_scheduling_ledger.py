"""Append-only, hash-chained scheduling ledger primitives.

Belongs to the ``state/requirements_registry.py`` facade. Re-exported there
for back-compat — import these names from the facade, not from this module.

This module owns the ledger substrate only: the typed failure, the chain and
head-anchor verification, and the EvaluationEpoch derived from the committed
head. Registry reconciliation and the sole writer live in the facade.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from trw_mcp.models.requirements import EvaluationEpoch, SchedulingAction

LEDGER_FILENAME = "scheduling-ledger.jsonl"
GENESIS_DIGEST = "genesis"

#: Effective date of the sentinel epoch returned when the ledger carries no
#: authorized ``advance_evaluation_epoch`` action. It is deliberately absurd —
#: any renewal date is "in the future" relative to it — which is exactly why
#: PRD-CORE-244-FR07 refuses to run the expiry loop against it.
GENESIS_EPOCH_DATE = "1970-01-01"
ANCHOR_FILENAME = "ledger-head.json"


class SchedulingLedgerError(RuntimeError):
    """Typed ledger failure — fork, gap, stale head, rollback, or tamper."""


def _anchor_path(ledger_path: Path) -> Path:
    return ledger_path.parent / ANCHOR_FILENAME


def _read_anchor(ledger_path: Path) -> tuple[int, str] | None:
    """Return the last committed (sequence, head_digest) anchor, if any."""
    anchor = _anchor_path(ledger_path)
    if not anchor.exists():
        return None
    try:
        data = json.loads(anchor.read_text(encoding="utf-8"))
        return int(data["sequence"]), str(data["head_digest"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SchedulingLedgerError(f"ledger head anchor unreadable: {exc}") from exc


def _write_anchor(ledger_path: Path, sequence: int, head_digest: str) -> None:
    _anchor_path(ledger_path).write_text(
        json.dumps({"sequence": sequence, "head_digest": head_digest}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_ledger_head_anchor(ledger_path: Path, actions: list[SchedulingAction]) -> None:
    """Anti-rollback anchor check (PRD-QUAL-121 §Authorized Scheduling Ledger).

    Prefix-chain verification alone cannot detect a truncated ledger (a valid
    prefix) or an in-place rewrite of the TAIL action (nothing chains atop it).
    The writer records the committed (sequence, head digest) beside the ledger
    after every append; any ledger whose length or head diverges from the
    anchor is an older/rolled-back/tampered head and MUST reconcile as
    ``stale_scheduling_head``. Threat model: operator error and concurrent
    races — a filesystem-level adversary who can forge both files is out of
    scope (the ledger has no signing key by design).
    """
    anchor = _read_anchor(ledger_path)
    if anchor is None:
        if actions:
            raise SchedulingLedgerError("ledger has actions but no committed head anchor")
        return
    sequence, head = anchor
    if len(actions) != sequence or ledger_head_digest(actions) != head:
        raise SchedulingLedgerError(
            f"ledger head diverges from committed anchor (anchor seq={sequence}, ledger seq={len(actions)}): "
            "older, rolled-back, or tail-tampered head"
        )


def action_digest(action: SchedulingAction) -> str:
    """Content digest binding an action into the hash chain."""
    payload = json.dumps(action.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_ledger(ledger_path: Path) -> list[SchedulingAction]:
    """Load and chain-verify the scheduling ledger (typed failure on tamper).

    Verifies sequence continuity (1..n) and that each action's
    ``previous_action_digest`` equals the digest of its predecessor.
    """
    if not ledger_path.exists():
        return []
    actions: list[SchedulingAction] = []
    previous_digest = GENESIS_DIGEST
    for line_no, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            action = SchedulingAction.model_validate(json.loads(line))
        except Exception as exc:  # justified: boundary — malformed ledger is a typed failure
            raise SchedulingLedgerError(f"ledger line {line_no} does not parse: {exc}") from exc
        if action.sequence != len(actions) + 1:
            raise SchedulingLedgerError(
                f"ledger sequence gap or fork at line {line_no}: expected {len(actions) + 1}, got {action.sequence}"
            )
        if action.previous_action_digest != previous_digest:
            raise SchedulingLedgerError(
                f"ledger chain break at sequence {action.sequence}: stale or forked previous digest"
            )
        previous_digest = action_digest(action)
        actions.append(action)
    return actions


def ledger_head_digest(actions: list[SchedulingAction]) -> str:
    return action_digest(actions[-1]) if actions else GENESIS_DIGEST


def derive_evaluation_epoch(actions: list[SchedulingAction]) -> EvaluationEpoch:
    """EvaluationEpoch = (sequence, effective_utc_date, ledger_head_digest) of the
    latest authorized ``advance_evaluation_epoch`` at the committed head."""
    head = ledger_head_digest(actions)
    for action in reversed(actions):
        if action.kind == "advance_evaluation_epoch":
            return EvaluationEpoch(
                sequence=action.sequence,
                effective_utc_date=action.effective_utc_date,
                ledger_head_digest=head,
            )
    return EvaluationEpoch(sequence=0, effective_utc_date=GENESIS_EPOCH_DATE, ledger_head_digest=head)


def is_genesis_epoch(epoch: EvaluationEpoch) -> bool:
    """True when *epoch* is the never-advanced sentinel (PRD-CORE-244-FR07).

    Both halves are checked, not just the date: a real authorized action could in
    principle carry a 1970 effective date, and a sequence of 0 alone would also
    match a hypothetical zero-indexed real action. The sentinel is the pair.
    """
    return epoch.sequence == 0 and epoch.effective_utc_date == GENESIS_EPOCH_DATE
