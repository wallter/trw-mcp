"""Bounded untrusted envelopes and receipt projection for the comms facade."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

InboxAction = Literal["fetch", "ack", "status"]
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
#: Milestone facts, in lifecycle order; the terminal ones mirror the terminal states.
MILESTONE_FACTS = ("admitted", "fetch_prepared", MessageState.ACKED.value, MessageState.EXPIRED.value)
KINDS = frozenset({"request", "reply", "status"})
DELIVERY_CLASSES = frozenset({"on_demand", "interrupt", "on_idle"})
MEMBER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


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
        if not 1 <= len(key_bytes) <= 128:
            raise AdmissionError("invalid_request_key")
        # A request key is an identifier, not prose. Control characters in one
        # are either a mistake or an attempt to occupy the derived scoped-notify
        # namespace, which is built from a control separator precisely so that
        # nothing a direct caller writes can land inside it. Imported locally:
        # _scope depends on this module for AdmissionError.
        from trw_mcp.comms._scope import has_control_characters, is_shard_key

        if has_control_characters(self.request_key) and not is_shard_key(self.request_key):
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
