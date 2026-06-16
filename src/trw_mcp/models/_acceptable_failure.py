"""PRD-CORE-191 — structured acceptable-failure record for the trw_deliver override.

A free-text ``unverified_reason`` (e.g. "WIP") is the #1 governance loophole:
it makes a deliver override indistinguishable from a documented exception when
inspected post-delivery. This model replaces the free-text acceptance with four
required, auditable fields so the bypass is time-bounded and owner-labelled.

The model is intentionally small. Parsing + expiry enforcement + ledger writes
live in :mod:`trw_mcp.tools._acceptable_failure_validation`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Disallowed C0/C1 control characters in audit-trail string fields. Normal
# whitespace (tab/newline/CR) is intentionally permitted — a multi-line
# residual_risk is legitimate — but NUL and the other non-printing control
# bytes are not: they corrupt the YAML override ledger and enable
# log/structured-log injection when an attacker controls the override string.
_CONTROL_CHARS = frozenset(
    chr(c)
    for c in (
        *range(0x09),  # C0 controls below tab
        *range(0x0B, 0x0D),  # VT, FF (tab 0x09 / LF 0x0A excluded)
        *range(0x0E, 0x20),  # remaining C0 (CR 0x0D excluded)
        0x7F,  # DEL
        *range(0x80, 0xA0),  # C1 controls (incl. NEL 0x85)
    )
)


class AcceptableFailureRecord(BaseModel):
    """A structured, time-bounded acceptable-failure override (PRD-CORE-191-FR01).

    All four fields are required and non-empty. ``expiry_iso`` is an ISO-8601
    date (``YYYY-MM-DD``); after that date the same override string is rejected
    (FR02 — enforced by the caller against the current UTC date).
    """

    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)

    failed_command: str = Field(min_length=1, description="The exact command or gate check that failed.")
    residual_risk: str = Field(min_length=1, description="What could go wrong shipping past the gate.")
    owner: str = Field(min_length=1, description="The actor accepting responsibility (run-id, name, SHA).")
    expiry_iso: str = Field(
        min_length=1, description="ISO-8601 date (YYYY-MM-DD) after which this override is invalid."
    )

    @field_validator("failed_command", "residual_risk", "owner", "expiry_iso")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("field must be non-empty")
        # Round-2 hardening: reject embedded control characters (NUL, BEL, ANSI
        # escape, etc.). These survive ``.strip()`` and would otherwise be
        # written verbatim into the ``.trw/overrides/`` ledger YAML and into the
        # structured override log line — an audit-record injection vector.
        if _CONTROL_CHARS.intersection(stripped):
            raise ValueError("field must not contain control characters")
        return stripped
