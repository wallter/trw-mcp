"""Module-level helpers for ``tools.learning`` (PRD-DIST-243 Phase 1, cycle 22).

Extracted from ``tools/learning.py`` to keep that module under the
350-effective-LOC operator threshold. Holds:

- ``_SOLUTION_PATTERNS`` + ``_is_solution_summary`` (PRD-FIX-052 FR05)
- ``_build_call_ctx`` (PRD-CORE-141 FR03 — re-exports shared
  ``state._call_context.build_call_context`` cycle 23)
- ``_read_injected_ids`` + ``_annotate_injected_learnings`` (PRD-CORE-095 FR15)
- ``_create_llm_client`` (LLMClient factory; routes usage log via config)

The module-level ``__getattr__`` shim in ``tools/learning.py`` STAYS
there — Python's module-level dunder lookup doesn't resolve through
re-imports, so the shim is load-bearing only at its original site.
"""

from __future__ import annotations

import re as _re
from pathlib import Path

import structlog

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import LearnResultDict
from trw_mcp.state._call_context import build_call_context as _build_call_ctx
from trw_mcp.state._paths import resolve_trw_dir

__all__ = [
    "_LEARN_TYPE_ALIASES",
    "_SOLUTION_PATTERNS",
    "_annotate_injected_learnings",
    "_build_call_ctx",
    "_coerce_learn_type",
    "_coerce_tags",
    "_create_llm_client",
    "_is_solution_summary",
    "_note_run_path_compat",
    "_read_injected_ids",
    "_validate_learn_enums",
    "_validate_learn_update_fields",
]

logger = structlog.get_logger(__name__)

# core185-ENUM-UNGUARDED-3: enum-valued ``trw_learn`` args must be validated in
# the tool BEFORE forwarding to ``execute_learn``. ``_learning_to_memory_entry``
# constructs ``MemoryType(type)`` / ``Confidence(confidence)`` /
# ``ProtectionTier(protection_tier)`` unconditionally; an invalid value raises a
# raw ``ValueError`` that is neither a ``StorageError`` nor caught by the recovery
# branches, so it escapes ``store_learning`` to the MCP caller as an unhandled
# exception -- violating the stable ``LearnResultDict`` return-shape contract.
# ``trw_learn_update`` already guards these; ``trw_learn`` did not. These sets
# mirror the enum members in ``trw_memory.models.memory``.
_VALID_LEARN_TYPES: frozenset[str] = frozenset({"incident", "pattern", "convention", "hypothesis", "workaround"})
_VALID_LEARN_CONFIDENCES: frozenset[str] = frozenset({"unverified", "low", "medium", "high", "verified"})
_VALID_LEARN_TIERS: frozenset[str] = frozenset({"critical", "high", "normal", "low", "protected", "permanent"})


def _validate_learn_enums(*, type: str, confidence: str, protection_tier: str) -> LearnResultDict | None:
    """Return a rejection ``LearnResultDict`` for an invalid enum arg, else None.

    core185-ENUM-UNGUARDED-3. Keeps the tool's contract stable: an out-of-range
    ``type`` / ``confidence`` / ``protection_tier`` yields a structured
    ``{"status": "rejected", "reason": ..., "message": ...}`` instead of letting
    the downstream enum construction raise an unhandled ``ValueError``.
    """
    if type not in _VALID_LEARN_TYPES:
        return {
            "status": "rejected",
            "reason": "invalid_type",
            "message": f"Invalid type '{type}'. Must be one of: {sorted(_VALID_LEARN_TYPES)}",
        }
    if confidence not in _VALID_LEARN_CONFIDENCES:
        return {
            "status": "rejected",
            "reason": "invalid_confidence",
            "message": f"Invalid confidence '{confidence}'. Must be one of: {sorted(_VALID_LEARN_CONFIDENCES)}",
        }
    if protection_tier not in _VALID_LEARN_TIERS:
        return {
            "status": "rejected",
            "reason": "invalid_protection_tier",
            "message": (f"Invalid protection_tier '{protection_tier}'. Must be one of: {sorted(_VALID_LEARN_TIERS)}"),
        }
    return None


def _validate_learn_update_fields(
    *,
    type: str | None,
    confidence: str | None,
    protection_tier: str | None,
    phase_origin: str | None,
    nudge_line: str | None,
    feedback: str | None,
    tags: list[str] | None,
) -> dict[str, str] | None:
    """Validate ``trw_learn_update`` enum/shape args; return a rejection or None.

    Extracted verbatim from ``trw_learn_update`` (PRD-CORE-110) to keep
    ``tools/learning.py`` under the 350-effective-LOC gate. ``type`` is expected
    to already be coerced by :func:`_coerce_learn_type` in the caller, matching
    the prior in-line ordering exactly — this is a behavior-preserving move.
    Uses ``set`` literals (not ``frozenset``) so the interpolated error messages
    render identically to the original in-line checks.
    """
    _valid_types = {"incident", "pattern", "convention", "hypothesis", "workaround"}
    if type is not None and type not in _valid_types:
        return {"error": f"Invalid type '{type}'. Must be one of: {_valid_types}", "status": "invalid"}
    _valid_confidences = {"unverified", "low", "medium", "high", "verified"}
    if confidence is not None and confidence not in _valid_confidences:
        return {
            "error": f"Invalid confidence '{confidence}'. Must be one of: {_valid_confidences}",
            "status": "invalid",
        }
    _valid_tiers = {"critical", "high", "normal", "low", "protected", "permanent"}
    if protection_tier is not None and protection_tier not in _valid_tiers:
        return {
            "error": f"Invalid protection_tier '{protection_tier}'. Must be one of: {_valid_tiers}",
            "status": "invalid",
        }
    _valid_phases = {"", "RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"}
    if phase_origin is not None and phase_origin not in _valid_phases:
        return {
            "error": f"Invalid phase_origin '{phase_origin}'. Must be one of: {_valid_phases}",
            "status": "invalid",
        }
    if nudge_line is not None and len(nudge_line) > 80:
        return {"error": f"nudge_line exceeds 80 chars ({len(nudge_line)})", "status": "invalid"}
    _valid_feedback = {"helpful", "unhelpful"}
    if feedback is not None and feedback not in _valid_feedback:
        return {"error": f"Invalid feedback '{feedback}'. Must be one of: {_valid_feedback}", "status": "invalid"}
    if tags is not None and (not isinstance(tags, list) or any(not isinstance(t, str) for t in tags)):
        return {"error": "tags must be a list of strings", "status": "invalid"}
    return None


# Potemkin defect C (sub_zAfRqZYYq2KtF72d): trw_learn(type='gotcha') was
# rejected ("'gotcha' is not a valid MemoryType") even though the trw_learn
# docstring and the trw-deliver / trw-ceremony-guide skills present "gotchas"
# as first-class durable content to record. Rather than widen trw-memory's
# MemoryType enum (which has downstream consumers), we alias ONLY the type
# vocabulary that TRW's own tool docs / skills advertise to callers, at the
# tool boundary, with a logged coercion. Keep this map small and justified
# (Substrate-First): every key must be a word a caller is told to use that is
# NOT already a valid enum member, and every value must be a valid MemoryType.
_LEARN_TYPE_ALIASES: dict[str, str] = {
    # The trw_learn docstring literally says "you just found a root cause,
    # gotcha, or durable pattern"; a gotcha is a known pitfall to work around.
    "gotcha": "workaround",
    "gotchas": "workaround",
    # Feedback sub_5qbmT6WPNoP58rlv item 8: agents naturally pass
    # type='project' for project/convention knowledge (mirroring the native
    # memory "project" category). A project fact IS a convention; coerce it
    # to the valid MemoryType rather than rejecting the write.
    "project": "convention",
}


def _note_run_path_compat(run_path: str | None) -> None:
    """Log a ``trw_learn(run_path=...)`` argument accepted for compatibility.

    Feedback sub_5qbmT6WPNoP58rlv item 8: agents reasonably pass ``run_path``
    after using the run-path-aware checkpoint/deliver tools. Learnings are
    run-independent, so the value is only ACCEPTED and logged (observable) — it
    is NOT validated against any run directory and does not change storage,
    keeping an otherwise-valid learning from failing.
    """
    if run_path is not None:
        logger.debug("learn_run_path_accepted_for_compat", run_path=run_path)


def _coerce_learn_type(type: str) -> str:
    """Map an advertised type alias to a valid ``MemoryType`` value.

    Returns *type* unchanged when it is already valid or is not an advertised
    alias (so the downstream :func:`_validate_learn_enums` still produces an
    honest rejection for genuine nonsense). Emits a structlog debug event when
    a coercion actually happens, so the remapping is observable, not silent.
    """
    if type in _VALID_LEARN_TYPES:
        return type
    resolved = _LEARN_TYPE_ALIASES.get(type)
    if resolved is None:
        return type
    logger.debug(
        "learn_type_alias_coerced",
        requested=type,
        resolved=resolved,
    )
    return resolved


def _coerce_tags(tags: list[str] | str | None) -> list[str] | None:
    """Coerce a tags argument to ``list[str] | None`` (PRD-IMPROVE-MCP-01 FR1).

    Agents frequently pass ``tags="a,b,c"`` (a comma- or whitespace-separated
    string) instead of a JSON list. Rather than raising a Pydantic
    ``list_type`` error, accept either shape:

    - ``None`` -> ``None`` (no tags).
    - ``list`` -> returned unchanged (each element coerced to ``str``).
    - ``str`` -> split on commas and/or whitespace, trimmed, empties dropped.
      A blank/whitespace-only string yields ``None``.
    """
    if tags is None:
        return None
    if isinstance(tags, list):
        return [str(t) for t in tags]
    parts: list[str] = []
    for chunk in tags.split(","):
        parts.extend(chunk.split())
    cleaned = [p.strip() for p in parts if p.strip()]
    return cleaned or None


# PRD-FIX-052-FR05: Solution-indicator patterns for auto-'pattern' tag suggestion.
_SOLUTION_PATTERNS = _re.compile(
    r"(?:use .+ instead|prefer |always |best practice|"
    r"recommended approach|the fix is|pattern:)",
    flags=_re.IGNORECASE | _re.VERBOSE,
)


def _is_solution_summary(summary: str) -> bool:
    """Return True if the summary matches solution-indicator patterns (FR05)."""
    return bool(_SOLUTION_PATTERNS.search(summary))


def _read_injected_ids(trw_dir: Path) -> set[str]:
    """Read learning IDs already injected by the user-prompt-submit hook.

    PRD-CORE-095 FR15: Returns a set of IDs from
    ``.trw/context/injected_learning_ids.txt`` (one per line).
    Returns empty set if file missing or unreadable.
    """
    state_file = trw_dir / "context" / "injected_learning_ids.txt"
    try:
        return {line.strip() for line in state_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return set()


def _annotate_injected_learnings(
    result: dict[str, object],
    trw_dir: Path,
) -> None:
    """Annotate and deprioritize already-injected learnings in recall results.

    PRD-CORE-095 FR15: Reads injected IDs from state file and moves
    already-injected learnings to the end of the list with an annotation.
    Fresh results fill the primary slots.
    """
    injected_ids = _read_injected_ids(trw_dir)
    if not injected_ids:
        return
    learnings = result.get("learnings")
    if not learnings or not isinstance(learnings, list):
        return
    fresh: list[dict[str, object]] = []
    already: list[dict[str, object]] = []
    for entry in learnings:
        lid = str(entry.get("id", ""))
        if lid in injected_ids:
            entry["already_in_context"] = True
            already.append(entry)
        else:
            fresh.append(entry)
    result["learnings"] = fresh + already


def _create_llm_client() -> LLMClient:
    """Create an LLM client using current config."""
    config = get_config()
    llm_usage_path: Path | None = None
    if config.llm_usage_log_enabled:
        trw_dir = resolve_trw_dir()
        llm_usage_path = trw_dir / config.logs_dir / config.llm_usage_log_file
    return LLMClient(model=config.llm_default_model, usage_log_path=llm_usage_path)
