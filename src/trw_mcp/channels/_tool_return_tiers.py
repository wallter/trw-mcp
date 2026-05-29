"""Shared tier-aware response shaping for distill MCP tools.

Maps resolved client tier (T0 / T1 / T2) to the content included in
trw_before_edit_hint, trw_codebase_risk_report, and trw_entity_risk_map
tool RESPONSES.

Design contract
---------------
- **T2** (codex, opencode, cursor-ide, cursor-cli): full distill payload —
  importers, inferred_tests, co_change_neighbors, hotspot_warnings, risk_score
  are included without truncation.
- **T1** (claude-code, antigravity, gemini, aider, default): compressed subset
  — hotspot_warnings (max 3), importers (max 5), inferred_tests (max 3),
  risk_score; drops doc_references and co_change_neighbors.
- **T0** (copilot, free tier): presence beacon only — distill_status,
  distill_action, risk_score (scalar), tier.  No list fields.

Enrichment is ADDITIVE: the base result fields are always present and
unchanged.  Per-tier shaping appends an ``enrichment`` key to the response.

Fail-open guarantee: ``enrich_response`` catches all exceptions and returns
the original dict unmodified.  Enrichment failure NEVER breaks the base tool
response.

NO trw_distill imports — this module is in the PUBLIC trw-mcp package.
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "enrich_response",
]

# Maximum list lengths per field at T1.
_T1_HOTSPOT_WARNINGS_MAX = 3
_T1_IMPORTERS_MAX = 5
_T1_INFERRED_TESTS_MAX = 3


def _extract_distill_hint(result: dict[str, object]) -> dict[str, object] | None:
    """Return the distill_hint sub-dict if present and non-null."""
    hint = result.get("distill_hint")
    if isinstance(hint, dict):
        return hint
    return None


def _safe_list(d: dict[str, object], key: str, max_items: int = 0) -> list[object]:
    """Return a list from *d[key]*, truncated to *max_items* if > 0."""
    raw = d.get(key)
    if not isinstance(raw, list):
        return []
    if max_items > 0:
        return raw[:max_items]
    return list(raw)


def _safe_float(d: dict[str, object], key: str) -> float | None:
    """Return float from *d[key]* or None."""
    raw = d.get(key)
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _build_t2_enrichment(result: dict[str, object]) -> dict[str, object]:
    """Full distill payload for T2 clients."""
    hint = _extract_distill_hint(result)
    if hint is None:
        return {"tier_applied": "T2", "distill_context": None}

    return {
        "tier_applied": "T2",
        "distill_context": {
            "importers": _safe_list(hint, "importers"),
            "inferred_tests": _safe_list(hint, "inferred_tests"),
            "co_change_neighbors": _safe_list(hint, "co_change_neighbors"),
            "hotspot_warnings": _safe_list(hint, "hotspot_warnings"),
            "risk_score": _safe_float(hint, "risk_score"),
        },
    }


def _build_t1_enrichment(result: dict[str, object]) -> dict[str, object]:
    """Compressed subset for T1 clients."""
    hint = _extract_distill_hint(result)
    if hint is None:
        # For codebase_risk_report and entity_risk_map: pull aggregate fields.
        risk_score: float | None = _safe_float(result, "risk_score")
        return {
            "tier_applied": "T1",
            "distill_context": {"risk_score": risk_score} if risk_score is not None else None,
        }

    return {
        "tier_applied": "T1",
        "distill_context": {
            "hotspot_warnings": _safe_list(hint, "hotspot_warnings", _T1_HOTSPOT_WARNINGS_MAX),
            "importers": _safe_list(hint, "importers", _T1_IMPORTERS_MAX),
            "inferred_tests": _safe_list(hint, "inferred_tests", _T1_INFERRED_TESTS_MAX),
            "risk_score": _safe_float(hint, "risk_score"),
        },
    }


def _build_t0_enrichment(result: dict[str, object]) -> dict[str, object]:
    """Presence beacon only for T0 clients."""
    # Scalar risk_score (top-level or from hint)
    hint = _extract_distill_hint(result)
    risk_score = _safe_float(result, "risk_score")
    if risk_score is None and hint is not None:
        risk_score = _safe_float(hint, "risk_score")

    return {
        "tier_applied": "T0",
        "distill_context": {
            "distill_status": result.get("distill_status"),
            "distill_action": result.get("distill_action"),
            "risk_score": risk_score,
        },
    }


_TIER_BUILDERS = {
    "T0": _build_t0_enrichment,
    "T1": _build_t1_enrichment,
    "T2": _build_t2_enrichment,
}


def enrich_response(
    result: dict[str, object],
    *,
    client_tier: str,
) -> dict[str, object]:
    """Return *result* with an ``enrichment`` key shaped by *client_tier*.

    The base result is returned unmodified when:
    - *client_tier* is not a recognised tier string.
    - Any exception occurs inside the builder.

    Args:
        result: The raw ``model_dump()`` dict from a distill tool.
        client_tier: Resolved tier string (``"T0"``, ``"T1"``, or ``"T2"``).

    Returns:
        A new dict (shallow copy) with an ``enrichment`` key added, or the
        original *result* dict on failure.
    """
    builder = _TIER_BUILDERS.get(client_tier)
    if builder is None:
        # Unknown tier — skip enrichment silently
        log.debug(
            "tool_return_tier_unknown",
            client_tier=client_tier,
            outcome="skipped",
        )
        return result

    try:
        enrichment = builder(result)
        enriched: dict[str, object] = {**result, "enrichment": enrichment}
        log.debug(
            "tool_return_enriched",
            client_tier=client_tier,
            outcome="ok",
        )
        return enriched
    except Exception as exc:
        log.debug(
            "tool_return_enrich_failed",
            client_tier=client_tier,
            error=str(exc),
            outcome="skipped",
        )
        return result
