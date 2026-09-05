"""``trw_init``'s collapsed ``advanced`` argument — coerce, validate, refuse.

Belongs to the ``orchestration.py`` facade. Kept in its own sibling so the
facade stays under the 350 effective-LOC gate.

WHY THIS EXISTS. A tool *definition* — description plus parameter JSON Schema —
is paid in the system prompt of every session of every client that loads the
surface, before the agent does anything; unlike a response it cannot be trimmed
at runtime. ``trw_init`` carried thirteen flat parameters, seven of which a
small minority of callers ever set, and each optional parameter costs its own
schema entry however rare it is. Those seven are collapsed into one argument.

THE SHAPE, AND WHY IT IS A DICT AT THE BOUNDARY AND A MODEL IN THE BODY.
Measured over the same eight-field payload: flat parameters 598 schema chars,
``dict[str, object]`` 215, a Pydantic ``BaseModel`` 739 — MORE than flat,
because a model re-emits every field in ``$defs`` and adds a ``$ref`` wrapper
on top. So the wire type is a plain dict and :class:`InitAdvanced` validates it
on the first line of the tool body: cheap schema, full typing, real errors.

WHAT THE DICT COSTS, AND THE TWO RULES THAT PAY FOR IT. The calling model no
longer sees these field names in the schema, so a mistake is invisible unless
this module makes it loud:

1. **Keys are byte-identical to the flat parameter names they replaced.** No
   renaming, no nesting, no abbreviation — ``advanced={"task_root": "docs"}``
   is the old ``task_root="docs"``, so callers, docs, and transcripts translate
   mechanically.
2. **An unrecognised key is REFUSED with the accepted-key list, never ignored.**
   ``extra="forbid"`` does the refusing; :func:`parse_init_advanced` re-renders
   the failure so the accepted set is IN the error. A bag that silently drops a
   typo'd key turns a caller mistake into invisible data loss: the run is
   created, the response says ``initialized``, and the setting the caller asked
   for was discarded without a word.

``strict=True`` validation is deliberate for the same reason: coercing
``protected: "no"`` to ``True`` would silently reinterpret a flag that exempts a
run from garbage collection.

``advanced`` also accepts a JSON *string*: fastmcp 3.2.4 performs no
JSON-string pre-parsing, and some clients serialize structured arguments as
strings (Claude Code #3084). Accepting both shapes here is what keeps the
collapse from being a client-compatibility regression.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trw_mcp.exceptions import StateError

__all__ = ["ADVANCED_KEYS", "InitAdvanced", "parse_init_advanced"]


class InitAdvanced(BaseModel):
    """Validated projection of the ``advanced`` bag onto typed ``trw_init`` inputs.

    Defaults reproduce the pre-collapse flat defaults exactly: ``task_root`` and
    ``planning_mode`` stay ``None`` (each has its own downstream fallback, and
    ``None`` is meaningfully distinct from ``""`` for ``task_root``, which falls
    back to ``config.task_root``), ``wave_manifest`` stays ``None`` (its
    presence, not its emptiness, triggers wave-plan creation), and ``protected``
    stays ``False``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_overrides: dict[str, str] | None = None
    task_root: str | None = None
    wave_manifest: list[dict[str, object]] | None = None
    complexity_signals: dict[str, object] | None = None
    artifacts: list[str] = Field(default_factory=list)
    protected: bool = False
    planning_mode: str | None = None
    # PRD-CORE-265-FR03/FR04. Two keys, not two tools: a tool DEFINITION is paid
    # in every session's system prompt of every client, and the formation surface
    # is reachable from the CLI for callers that cannot use MCP at all. ``dict``
    # rather than a nested model for the schema reason in the module docstring —
    # the shape is validated by ``trw_mcp.formation``, which owns it.
    formation: dict[str, object] | None = None
    join_formation: dict[str, str] | None = None


#: Accepted ``advanced`` keys, byte-identical to the ``trw_init`` parameters
#: they replaced. Derived from the model so the refusal message can never drift
#: from what is actually accepted.
ADVANCED_KEYS: tuple[str, ...] = tuple(sorted(InitAdvanced.model_fields))


def _coerce_json(raw: dict[str, object] | str | None) -> dict[str, object]:
    """Normalize the wire shape to a mapping, or raise naming the accepted keys."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items()}
    text = raw.strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except ValueError as exc:
        raise StateError(f"advanced must be an object or a JSON object string: {exc}") from exc
    if not isinstance(decoded, dict):
        raise StateError(f"advanced JSON must decode to an object. Accepted keys: {', '.join(ADVANCED_KEYS)}")
    return {str(key): value for key, value in decoded.items()}


def parse_init_advanced(raw: dict[str, object] | str | None) -> InitAdvanced:
    """Validate the ``advanced`` bag and return the typed ``trw_init`` inputs.

    Raises:
        StateError: on a non-object payload, an unrecognised key, or a value of
            the wrong type. The message always carries the accepted-key list, so
            the fix for the overwhelmingly common failure (a typo) is in the
            error itself. Refusing rather than coercing is the point — see the
            module docstring.
    """
    try:
        return InitAdvanced.model_validate(_coerce_json(raw), strict=True)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'advanced'}: {err['msg']}" for err in exc.errors()
        )
        raise StateError(f"Invalid advanced argument — {problems}. Accepted keys: {', '.join(ADVANCED_KEYS)}") from exc
