"""Bounded untrusted envelopes and receipt projection for the comms facade."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

InboxAction = Literal["fetch", "ack", "status", "accept", "report", "complete"]
#: PRD-CORE-322 FR02-FR04: the handoff writes. The recipient accepts and reports, the sender completes.
HANDOFF_ACTIONS = frozenset({"accept", "report", "complete"})
#: Actions that run the receiver incarnation fence (FR12), so a displaced recipient is refused.
RECEIVER_ACTIONS = frozenset({"fetch", "ack", "accept", "report"})
MessageKind = Literal["request", "reply", "status"]
DeliveryClass = Literal["on_demand", "interrupt", "on_idle"]


class MessageState(str, Enum):
    """An admission row's lifecycle (ledger RC-006): pending, then exactly one terminal state.

    Every stored state and every SQL predicate over ``admissions.state`` comes
    from here, so the verifier, expiry, ACK and the lean hint cannot disagree.
    """

    PENDING = "pending"
    ACKED = "acked"
    EXPIRED = "expired"


MESSAGE_STATES = frozenset(state.value for state in MessageState)
TERMINAL_MESSAGE_STATES = frozenset({MessageState.ACKED.value, MessageState.EXPIRED.value})
#: Per-handoff facts on a ``request`` row (PRD-CORE-322), in the order each requires
#: the one before: accepted needs acked, reported needs accepted, completed needs reported.
HANDOFF_FACTS = ("accepted", "reported", "completed")
#: Milestone facts, in lifecycle order; the terminal ones mirror the terminal states.
#: The handoff facts are valid only in a schema v5 mailbox (the verifier enforces that).
MILESTONE_FACTS = ("admitted", "fetch_prepared", MessageState.ACKED.value, MessageState.EXPIRED.value, *HANDOFF_FACTS)
#: PRD-CORE-322 NFR02: a next-read pointer is 1..512 UTF-8 bytes of untrusted data.
NEXT_READ_MAX_BYTES = 512
_LINE_BREAKS = frozenset({"Zl", "Zp"})


def clean_text(value: str) -> str:
    """Normalise a POINTER (next_read): strip it, so padding never reaches storage; a blank one is then refused.

    Message bodies and request keys are caller DATA and identity: they are never rewritten. Their blank check is
    ``not value.strip()`` in ``Envelope.validate`` (strip to judge, store verbatim). Non-strings pass through for
    the validators to refuse.
    """
    return value.strip() if isinstance(value, str) else value


def valid_next_read(value: object) -> bool:
    """True for 1..512 UTF-8 bytes with no category C* (control, format incl. bidi, surrogate...) or
    Zl/Zp character: U+2028/U+2029 are not "control" to Unicode but break a line wherever the
    pointer is displayed, so a peer could forge an extra board line with them.

    Soundness scope: bounds and printability of the stored text only; never whether the
    pointed-to artifact exists. Runtime callers: the schema v5 verifier (``_schema._handoffs``)
    and the report write (``_messages.report``), so nothing the verifier rejects is ever written.
    """
    if not isinstance(value, str) or not value:
        return False
    categories = (unicodedata.category(char) for char in value)
    if any(category[0] == "C" or category in _LINE_BREAKS for category in categories):
        return False
    return len(value.encode("utf-8")) <= NEXT_READ_MAX_BYTES  # no surrogate survives the check above


KINDS = frozenset({"request", "reply", "status"})
DELIVERY_CLASSES = frozenset({"on_demand", "interrupt", "on_idle"})
#: The member-id grammar, in ONE place (ledger RC-003). It was spelled out
#: identically here, in _peers_page and inside _scope's shard-key regex, so a
#: grammar change needed three synchronized edits. The SOURCE is exported
#: because _scope embeds it in a larger pattern and cannot use the compiled one.
MEMBER_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
MEMBER_ID = re.compile(MEMBER_ID_PATTERN)


class AdmissionError(ValueError):
    """An expected bounded refusal; facade commits its aggregate counter."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Envelope:
    recipient_member_id: str
    request_key: str
    body: str
    kind: MessageKind
    delivery_class: DeliveryClass
    #: Set only by scoped notify, which derives the key itself. A caller-supplied
    #: key in shard form would let a direct send pre-occupy a notify's row and so
    #: fake a scoped delivery to an offline peer.
    derived_key: bool = False

    def validate(self) -> None:
        if not MEMBER_ID.fullmatch(self.recipient_member_id):
            raise AdmissionError("invalid_recipient")
        if self.kind not in KINDS or self.delivery_class not in DELIVERY_CLASSES:
            raise AdmissionError("invalid_message_enum")
        try:
            key_bytes = self.request_key.encode("utf-8")
            self.body.encode("utf-8")
        except UnicodeError as exc:
            raise AdmissionError("invalid_utf8") from exc
        if not 1 <= len(key_bytes) <= 128 or not self.request_key.strip():
            raise AdmissionError("invalid_request_key")
        if not self.body.strip():
            raise AdmissionError("invalid_message_body")
        # A request key is an identifier, not prose. Control characters in one
        # are either a mistake or an attempt to occupy the derived scoped-notify
        # namespace, which is built from a control separator so that it is
        # distinguishable; only the notify path may write inside it. Imported
        # locally: _scope depends on this module for AdmissionError.
        from trw_mcp.comms._scope import has_control_characters, is_shard_key

        if self.derived_key:
            if not is_shard_key(self.request_key):
                raise AdmissionError("invalid_request_key")
        elif has_control_characters(self.request_key):
            raise AdmissionError("invalid_request_key")

    def canonical_sha256(self) -> str:
        """Digest of the canonical payload FR02 compares; survives body tombstoning (FR15)."""
        payload = [self.recipient_member_id, self.kind, self.delivery_class, self.body]
        return hashlib.sha256(canonical_bytes(payload)).hexdigest()

    def matches(self, row: sqlite3.Row) -> bool:
        """FR02 exact retry, by canonical digest so it survives body tombstoning (FR15)."""
        return bool(self.canonical_sha256() == str(row["canonical_sha256"]))


def receipt(row: sqlite3.Row) -> dict[str, Any]:
    """Immutable provenance only: no body, internal paths, pin or incarnation."""
    return {
        key: row[key]
        for key in ("message_id", "sender_member_id", "recipient_member_id", "kind", "delivery_class", "admitted_at")
    }


def canonical_bytes(payload: object) -> bytes:
    """Complete logical JSON payload bound, excluding MCP framing/duplication."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )
