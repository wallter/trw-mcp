"""Out-of-band, scope-bound operator sign-off records (PRD-CORE-255-FR04).

Why this file exists — the defect it closes
-------------------------------------------
FR04's adversarial gate accepted an ``operator`` reviewer posture on the
strength of a *caller-supplied string*: ``trw_review(reviewer_identity={
"reviewer_source": "operator", "reviewer_receipt_id": "anything"})``. The agent
under review could therefore mint its own operator sign-off, stamp the
``adversarial_audit`` rubric on a zero-finding review, set ``adversarial_pass``,
and satisfy every FR04 condition without an adversarial audit ever happening.
A gate that the reviewed party can self-certify is worse than no gate: it
manufactures evidence (CONSTITUTION HB-1/HB-4) instead of merely lacking it.

The fix: an ``operator`` receipt id must now RESOLVE to a record in
``<trw_dir>/approvals/review-signoffs.jsonl`` that

1. exists (an id that resolves to nothing is refused, never assumed),
2. carries a valid HMAC-SHA256 signature over its own canonical payload,
3. has not expired, and whose approver-chosen validity window does not exceed
   ``review_signoff_ttl_hours``, and
4. is BOUND to this exact review — its ``review_ref`` must equal the review's
   ``review_id`` or its content-binding ``scope_digest``. One approval cannot be
   replayed against a different review.

Every refusal carries a machine-readable reason that reaches the ``trw_review``
response as ``family_downgraded_reason``; there is no silent ``False`` and no
reassuring default on an unreadable file.

Honest scope (CONSTITUTION §1 — do not overstate this control)
--------------------------------------------------------------
This makes the sign-off *out-of-band, auditable, expiring, and scope-bound*: it
must be created by a deliberate CLI step that writes an operator-attributed
record, and it cannot be conjured inside the ``trw_review`` call the way a free
string could. It is NOT a cryptographic barrier against an actor who already
holds the signing key and a shell — like ``_entitlements`` (whose key material
this reuses), the key is local. What changes is that fabricating an operator
sign-off now requires forging a named, dated, expiring record on disk, which is
a discrete act an auditor can see, rather than typing a word into a tool call.

Minting (out-of-band, by the approver — never by the reviewed agent)::

    python -m trw_mcp.state.review_signoffs approve \\
        --review-ref <review_id or scope_digest> --approver <name>

It prints the approval id on stdout; that id is what goes into
``reviewer_identity={"reviewer_source": "operator", "reviewer_receipt_id": ...}``.
Folding this into the ``trw-mcp`` argparse surface as ``trw-mcp review approve``
is deferred only because ``server/`` is owned by another lane; the behavior and
the record format are the shipped ones.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

from trw_mcp.security.intent_contract._atomic_json import locked

logger = structlog.get_logger(__name__)

#: Location of the append-only sign-off journal, relative to the project ``.trw``.
SIGNOFF_RELPATH = "approvals/review-signoffs.jsonl"

#: Domain separation for the signing key. The base key material is resolved by
#: :func:`trw_mcp.state._entitlements._get_hmac_key` (reused, not duplicated, so
#: ``TRW_ENTITLEMENT_KEY`` keeps working as the one key-provisioning knob), then
#: run through one HMAC step with this context so an ENTITLEMENT signature can
#: never be replayed as a review approval and vice versa.
_SIGNOFF_KEY_CONTEXT = b"trw-review-signoff-v1"

#: Operators who want approvals keyed independently of entitlements set this.
SIGNOFF_KEY_ENV = "TRW_REVIEW_SIGNOFF_KEY"

#: Signed-payload schema version. A new signed field requires a new version —
#: an old signature must never validate against a differently-shaped payload.
_PAYLOAD_VERSION = "v1"

#: Bounded read of the journal. A control-plane file this size is already
#: pathological; refusing is the fail-closed direction (it yields "unreadable",
#: never "verified").
MAX_SIGNOFF_FILE_BYTES = 4 * 1024 * 1024

#: Closed set of refusal reasons. Every one of them reaches the caller.
REASON_UNRESOLVED = "operator_receipt_unresolved"
REASON_UNREADABLE = "operator_approvals_unreadable"
REASON_SIGNATURE_INVALID = "operator_approval_signature_invalid"
REASON_EXPIRED = "operator_approval_expired"
REASON_SCOPE_MISMATCH = "operator_approval_scope_mismatch"
REASON_TTL_EXCEEDED = "operator_approval_ttl_exceeded"
REASON_POLICY_UNREADABLE = "operator_approval_policy_unreadable"


@dataclass(frozen=True)
class ReviewSignoff:
    """One operator sign-off record, exactly as persisted."""

    approval_id: str
    review_ref: str
    approver: str
    approved_at: str
    expires_at: str
    signature: str

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "approval_id": self.approval_id,
                "review_ref": self.review_ref,
                "approver": self.approver,
                "approved_at": self.approved_at,
                "expires_at": self.expires_at,
                "signature": self.signature,
                "payload_version": _PAYLOAD_VERSION,
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class SignoffResolution:
    """Outcome of resolving a caller-supplied operator receipt id.

    ``verified`` is True only when every condition in the module docstring held.
    ``reason`` is empty exactly when ``verified`` is True — a False with no
    reason would be the silent refusal this module exists to remove.
    """

    verified: bool
    reason: str = ""
    approver: str = ""


def signoffs_path(trw_dir: Path) -> Path:
    """The sign-off journal inside *trw_dir*."""
    return trw_dir / SIGNOFF_RELPATH


def _signing_key() -> bytes:
    """Key material for approval signatures (see :data:`_SIGNOFF_KEY_CONTEXT`)."""
    dedicated = os.environ.get(SIGNOFF_KEY_ENV)
    if dedicated:
        return hmac.new(dedicated.encode("utf-8"), _SIGNOFF_KEY_CONTEXT, hashlib.sha256).digest()
    from trw_mcp.state._entitlements import _get_hmac_key

    return hmac.new(_get_hmac_key(), _SIGNOFF_KEY_CONTEXT, hashlib.sha256).digest()


def _canonical_payload(
    *,
    approval_id: str,
    review_ref: str,
    approver: str,
    approved_at: str,
    expires_at: str,
) -> bytes:
    """Deterministic signed bytes. Field order is FIXED and version-tagged."""
    return (
        f"{_PAYLOAD_VERSION}|approval_id={approval_id}|review_ref={review_ref}"
        f"|approver={approver}|approved_at={approved_at}|expires_at={expires_at}"
    ).encode()


def sign_review_signoff(
    *,
    approval_id: str,
    review_ref: str,
    approver: str,
    approved_at: str,
    expires_at: str,
) -> str:
    """HMAC-SHA256 hex signature over the canonical payload."""
    payload = _canonical_payload(
        approval_id=approval_id,
        review_ref=review_ref,
        approver=approver,
        approved_at=approved_at,
        expires_at=expires_at,
    )
    return hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()


def configured_ttl_hours() -> int:
    """``review_signoff_ttl_hours`` — the maximum validity an approver may grant.

    Raises on an unreadable config rather than substituting a default: the caller
    turns that into :data:`REASON_POLICY_UNREADABLE` (refused), because guessing
    a cap here is how an unbounded approval would slip through.
    """
    from trw_mcp.models.config import get_config

    return int(get_config().review_signoff_ttl_hours)


def append_review_signoff(
    trw_dir: Path,
    *,
    review_ref: str,
    approver: str,
    ttl_hours: int | None = None,
    now: datetime | None = None,
) -> ReviewSignoff:
    """Append one signed approval and return it. The ONLY writer.

    Both the CLI and the tests go through this function, so a test can never
    prove a shape the shipped mint path does not produce.
    """
    ref = review_ref.strip()
    name = approver.strip()
    if not ref:
        raise ValueError("--review-ref must be a non-empty review_id or scope_digest")
    if not name:
        raise ValueError("--approver must be a non-empty operator identity")
    hours = configured_ttl_hours() if ttl_hours is None else int(ttl_hours)
    if hours < 1:
        raise ValueError("ttl_hours must be at least 1")
    issued = now or datetime.now(tz=timezone.utc)
    approved_at = issued.isoformat()
    expires_at = (issued + timedelta(hours=hours)).isoformat()
    approval_id = f"opsign-{secrets.token_hex(16)}"
    record = ReviewSignoff(
        approval_id=approval_id,
        review_ref=ref,
        approver=name,
        approved_at=approved_at,
        expires_at=expires_at,
        signature=sign_review_signoff(
            approval_id=approval_id,
            review_ref=ref,
            approver=name,
            approved_at=approved_at,
            expires_at=expires_at,
        ),
    )
    with locked(signoffs_path(trw_dir), append=True) as descriptor:
        os.write(descriptor, (record.to_json_line() + "\n").encode("utf-8"))
    logger.info("review_signoff_recorded", approval_id=approval_id, review_ref=ref, expires_at=expires_at)
    return record


def _load_signoffs(path: Path) -> list[ReviewSignoff]:
    """Parse the journal. Raises ``OSError``/``ValueError``; never returns partial silence.

    A malformed LINE is skipped (an append-only journal may hold a torn tail),
    but a malformed FILE — unreadable, oversized — raises, and the caller refuses.
    """
    if path.stat().st_size > MAX_SIGNOFF_FILE_BYTES:
        raise ValueError(f"sign-off journal exceeds {MAX_SIGNOFF_FILE_BYTES} bytes")
    records: list[ReviewSignoff] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except ValueError:
            logger.warning("review_signoff_line_malformed", path=str(path))
            continue
        if not isinstance(entry, dict) or entry.get("payload_version") != _PAYLOAD_VERSION:
            continue
        records.append(
            ReviewSignoff(
                approval_id=str(entry.get("approval_id", "")),
                review_ref=str(entry.get("review_ref", "")),
                approver=str(entry.get("approver", "")),
                approved_at=str(entry.get("approved_at", "")),
                expires_at=str(entry.get("expires_at", "")),
                signature=str(entry.get("signature", "")),
            )
        )
    return records


def _parse_iso(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _check(record: ReviewSignoff, review_refs: Sequence[str], moment: datetime, max_hours: int) -> str:
    """The reason *record* is unusable for these refs, or ``""`` when it holds."""
    expected = sign_review_signoff(
        approval_id=record.approval_id,
        review_ref=record.review_ref,
        approver=record.approver,
        approved_at=record.approved_at,
        expires_at=record.expires_at,
    )
    if not hmac.compare_digest(expected, record.signature):
        return REASON_SIGNATURE_INVALID
    # Scope binding is checked only AFTER the signature, so a tampered review_ref
    # is reported as tampering rather than as an innocent mismatch.
    if record.review_ref not in {ref for ref in review_refs if ref}:
        return REASON_SCOPE_MISMATCH
    approved = _parse_iso(record.approved_at)
    expires = _parse_iso(record.expires_at)
    if approved is None or expires is None:
        return REASON_SIGNATURE_INVALID
    if expires - approved > timedelta(hours=max_hours):
        return REASON_TTL_EXCEEDED
    if moment > expires:
        return REASON_EXPIRED
    return ""


def resolve_review_signoff(
    trw_dir: Path,
    approval_id: str,
    review_refs: Sequence[str],
    *,
    now: datetime | None = None,
) -> SignoffResolution:
    """Resolve *approval_id* to a valid approval bound to one of *review_refs*.

    Never raises and never returns a bare ``False``: an absent id, an unreadable
    journal, a bad signature, an over-long or elapsed window, and a reference to
    a DIFFERENT review each produce their own reason.
    """
    token = approval_id.strip()
    if not token:
        return SignoffResolution(verified=False, reason=REASON_UNRESOLVED)
    try:
        max_hours = configured_ttl_hours()
    except Exception:  # justified: an unreadable TTL policy refuses, it does not guess a cap
        logger.warning("review_signoff_policy_unreadable", exc_info=True)
        return SignoffResolution(verified=False, reason=REASON_POLICY_UNREADABLE)
    path = signoffs_path(trw_dir)
    try:
        records = _load_signoffs(path)
    except FileNotFoundError:
        # No journal at all is "this id resolves to nothing", not "unreadable".
        logger.warning("review_signoff_journal_absent", path=str(path), approval_id=token)
        return SignoffResolution(verified=False, reason=REASON_UNRESOLVED)
    except Exception:  # justified: a present-but-unreadable control file must refuse (NFR01)
        logger.warning("review_signoff_journal_unreadable", path=str(path), exc_info=True)
        return SignoffResolution(verified=False, reason=REASON_UNREADABLE)

    moment = now or datetime.now(tz=timezone.utc)
    matches = [record for record in records if record.approval_id == token]
    if not matches:
        logger.warning("review_signoff_unresolved", approval_id=token, path=str(path))
        return SignoffResolution(verified=False, reason=REASON_UNRESOLVED)
    last_reason = REASON_UNRESOLVED
    for record in matches:
        last_reason = _check(record, review_refs, moment, max_hours)
        if not last_reason:
            logger.info("review_signoff_verified", approval_id=token, review_ref=record.review_ref)
            return SignoffResolution(verified=True, approver=record.approver)
    logger.warning("review_signoff_refused", approval_id=token, reason=last_reason)
    return SignoffResolution(verified=False, reason=last_reason)


def trw_dir_for_run(run_path: Path) -> Path:
    """The ``.trw`` tree owning *run_path*, so mint and gate read the SAME journal.

    Runs live at ``<trw_dir>/runs/<task>/<run_id>``. Deriving the journal from the
    run rather than from ambient project resolution keeps the two FR04 call sites
    (receipt writer, delivery gate) on one file even when the process CWD or the
    configured project root differ between them.
    """
    from trw_mcp.state._paths import resolve_trw_dir

    try:
        from trw_mcp.models.config import get_config

        name = Path(str(get_config().trw_dir)).name or ".trw"
    except Exception:  # justified: the conventional directory name, not a security decision
        name = ".trw"
    for parent in run_path.parents:
        if parent.name == name:
            return parent
    return resolve_trw_dir()


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m trw_mcp.state.review_signoffs approve ...`` — the out-of-band mint step."""
    from trw_mcp.state._paths import resolve_trw_dir

    parser = argparse.ArgumentParser(
        prog="trw-review-approve",
        description="Append one signed, scope-bound operator review sign-off (PRD-CORE-255-FR04).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    approve = sub.add_parser("approve", help="append one signed operator review sign-off")
    approve.add_argument("--review-ref", required=True, help="the review_id or scope_digest this approval binds to")
    approve.add_argument("--approver", required=True, help="the operator granting the sign-off")
    approve.add_argument("--ttl-hours", type=int, default=None, help="defaults to review_signoff_ttl_hours")
    approve.add_argument("--trw-dir", type=Path, default=None, help="project .trw directory")
    args = parser.parse_args(argv)

    trw_dir = args.trw_dir if args.trw_dir is not None else resolve_trw_dir()
    try:
        record = append_review_signoff(
            trw_dir,
            review_ref=args.review_ref,
            approver=args.approver,
            ttl_hours=args.ttl_hours,
        )
    except (OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(
        f"approved review-ref {record.review_ref} until {record.expires_at}; pass the id below as reviewer_receipt_id",
        file=sys.stderr,
    )
    print(record.approval_id)
    return 0


__all__ = [
    "MAX_SIGNOFF_FILE_BYTES",
    "REASON_EXPIRED",
    "REASON_POLICY_UNREADABLE",
    "REASON_SCOPE_MISMATCH",
    "REASON_SIGNATURE_INVALID",
    "REASON_TTL_EXCEEDED",
    "REASON_UNREADABLE",
    "REASON_UNRESOLVED",
    "SIGNOFF_KEY_ENV",
    "SIGNOFF_RELPATH",
    "ReviewSignoff",
    "SignoffResolution",
    "append_review_signoff",
    "configured_ttl_hours",
    "main",
    "resolve_review_signoff",
    "sign_review_signoff",
    "signoffs_path",
    "trw_dir_for_run",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
