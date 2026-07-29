"""Structured-argument bags for ``trw_learn`` / ``trw_learn_update``.

Belongs to the ``learning.py`` facade. Re-exported there for back-compat.

WHY A BAG. A tool *definition* — description plus parameter JSON Schema — is
paid unconditionally in the system prompt of every session of every client that
loads the surface. Each ``x: str | None = None`` parameter costs ~78 chars of
schema no matter what the docstring says, so the fix for a 24-parameter tool is
an API change, not more editing (see ``tests/test_tool_definition_budget.py``).
``trw_learn`` and ``trw_learn_update`` were the two largest signatures in the
package; the rarely-set fields now travel in one object.

WHY THE PARAMETER IS A ``dict`` AND THE MODEL IS INSIDE. Measured against the
same 8 rare parameters: flat 598 chars, ``dict[str, object]`` 215, a Pydantic
``BaseModel`` parameter 739 — *more* than flat, because the model re-emits every
field into ``$defs`` and adds a ``$ref`` wrapper on top. So the wire type is a
plain mapping and the Pydantic model is applied on the first line of the tool
body: cheap schema, full typing, real validation errors. This applies ONLY to
the tool-parameter position; ``models/`` stays Pydantic v2 throughout.

TWO INVARIANTS THIS MODULE EXISTS TO HOLD:

1. **Bag keys are byte-identical to the flat parameter names they replaced.**
   The migration is mechanical and a verbatim move cannot silently mis-map.
2. **An unknown key is REJECTED, naming the accepted set** (``extra="forbid"``
   plus an error that lists the keys). The cost of a dict parameter is that the
   calling model no longer sees field names in the schema, so a typo like
   ``{"tpye": "incident"}`` MUST fail loudly. Silently dropping it would leave a
   learning that looks stored and is wrong — under Vision Principle 1 a
   silently-dropped field is a poisoned learning, which compounds exactly as
   efficiently as a good one.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import LearnResultDict

__all__ = [
    "LearnMetadata",
    "LearnUpdateFields",
    "parse_learn_metadata",
    "parse_learn_update_fields",
]


def _coerce_json(raw: dict[str, object] | str | None) -> tuple[dict[str, object] | None, str | None]:
    """Return ``(mapping, error)`` for a bag argument that may arrive as a string.

    fastmcp 3.2.4 does no JSON pre-parsing and Claude Code
    (anthropics/claude-code#3084) can deliver a structured argument as a
    stringified object, so both shapes are accepted at the boundary.
    """
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None, "must be an object or a JSON object string"
        if not isinstance(decoded, dict):
            return None, "must be an object mapping field names to values"
        return {str(k): v for k, v in decoded.items()}, None
    if not isinstance(raw, dict):
        return None, "must be an object mapping field names to values"
    return raw, None


def _listify(value: object) -> object:
    """Wrap a bare string in a list for a list-typed field.

    A single string is the one unambiguous mis-shape agents produce for a list
    field (``domain="mcp"``). Coercing it beats rejecting an otherwise-valid
    learning; every other shape is left for Pydantic to accept or reject.
    """
    if isinstance(value, str):
        return [value] if value else []
    return value


class LearnMetadata(BaseModel):
    """Type-normalized ``trw_learn(metadata=...)`` values.

    Defaults are byte-identical to the flat parameters they replaced, so an
    omitted key behaves exactly as the omitted argument did. ``client_profile``
    and ``model_id`` default to ``None`` because ``None`` is the auto-detect
    signal — an explicit ``""`` means "deliberately blank".
    """

    model_config = ConfigDict(extra="forbid")

    source_identity: str = ""
    client_profile: str | None = None
    model_id: str | None = None
    consolidated_from: list[str] | None = None
    assertions: list[dict[str, str]] | None = None
    nudge_line: str = ""
    task_type: str = ""
    domain: list[str] | None = None
    phase_origin: str = ""
    phase_affinity: list[str] | None = None
    protection_tier: str = "normal"

    @field_validator("consolidated_from", "domain", "phase_affinity", mode="before")
    @classmethod
    def _wrap_bare_string(cls, value: object) -> object:
        return _listify(value)


class LearnUpdateFields(BaseModel):
    """Type-normalized ``trw_learn_update(fields=...)`` values.

    Every field defaults to ``None`` because ``None`` IS the partial-update
    sentinel the adapter reads: "the caller did not ask me to touch this".

    ``model_fields_set`` distinguishes an absent key from an explicit ``null``,
    which the flat signature could not express at all. Both still map to
    "unchanged" here, because that is the adapter's existing contract and
    quietly redefining an explicit null as "clear this field" would be a
    destructive behaviour change hiding inside a signature refactor. Clearing
    stays explicit: pass ``""`` or ``[]``.
    """

    model_config = ConfigDict(extra="forbid")

    type: str | None = None
    nudge_line: str | None = None
    expires: str | None = None
    confidence: str | None = None
    task_type: str | None = None
    domain: list[str] | None = None
    phase_origin: str | None = None
    phase_affinity: list[str] | None = None
    team_origin: str | None = None
    protection_tier: str | None = None
    assertions: list[dict[str, str]] | None = None

    @field_validator("domain", "phase_affinity", mode="before")
    @classmethod
    def _wrap_bare_string(cls, value: object) -> object:
        return _listify(value)


def _format_error(param_name: str, model: type[BaseModel], detail: str) -> str:
    """Build a rejection message that names the accepted keys."""
    return f"{param_name} {detail}. Accepted keys: {sorted(model.model_fields)}"


def _first_validation_problem(exc: ValidationError) -> str:
    """Summarize a ValidationError as one caller-actionable clause."""
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
    return f"is invalid at '{location}': {first.get('msg', 'invalid value')}"


def parse_learn_metadata(
    raw: dict[str, object] | str | None,
) -> tuple[LearnMetadata, LearnResultDict | None]:
    """Parse ``trw_learn(metadata=...)``; return ``(values, rejection)``."""
    mapping, error = _coerce_json(raw)
    if mapping is None:
        return LearnMetadata(), {
            "status": "rejected",
            "reason": "invalid_metadata",
            "message": _format_error("metadata", LearnMetadata, error or "is invalid"),
        }
    try:
        return LearnMetadata.model_validate(mapping), None
    except ValidationError as exc:
        return LearnMetadata(), {
            "status": "rejected",
            "reason": "invalid_metadata",
            "message": _format_error("metadata", LearnMetadata, _first_validation_problem(exc)),
        }


def parse_learn_update_fields(
    raw: dict[str, object] | str | None,
) -> tuple[LearnUpdateFields, dict[str, str] | None]:
    """Parse ``trw_learn_update(fields=...)``; return ``(values, rejection)``.

    The rejection shape is ``trw_learn_update``'s ``{"error", "status"}``, not
    ``trw_learn``'s ``{"status", "reason", "message"}`` — the two tools have
    different documented output contracts and unifying them here would be a
    silent contract change for every caller that branches on the result.
    """
    mapping, error = _coerce_json(raw)
    if mapping is None:
        return LearnUpdateFields(), {
            "error": _format_error("fields", LearnUpdateFields, error or "is invalid"),
            "status": "invalid",
        }
    try:
        return LearnUpdateFields.model_validate(mapping), None
    except ValidationError as exc:
        return LearnUpdateFields(), {
            "error": _format_error("fields", LearnUpdateFields, _first_validation_problem(exc)),
            "status": "invalid",
        }
