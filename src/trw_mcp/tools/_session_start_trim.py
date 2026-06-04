"""trw_session_start payload trimming + intentional-marker helpers.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

PRD-IMPROVE-MCP-04:

- FR1 — ``trim_session_start_payload`` makes ``trw_session_start`` compact by
  default. The full payload (entire learnings list + embed_health +
  assertion_health + sync_health + step_durations_ms + auto_recalled) is large
  and is returned on *every* session. This caps the learnings list to the
  top-K most relevant, collapses the low-signal diagnostic sub-blocks into a
  one-line ``health_summary``, and records an approximate
  ``payload_token_estimate`` so the reduction is measurable. Load-bearing
  fields (run/pin recovery, errors, framework_reminder, advisories) are NEVER
  dropped. ``verbose=True`` is a no-op pass-through (current full behavior).

- FR2 — ``find_intentional_marker`` detects a ``# trw:intentional <reason>``
  (or ``// trw:intentional <reason>``) marker on or just above a given line so
  scanners/reviewers can treat flagged code as a settled, deliberate decision.

Fail-open: trimming never raises; on any internal error the original payload is
returned unchanged so resume correctness is preserved.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import SessionStartResultDict

logger = structlog.get_logger(__name__)

# Default cap on the learnings list in compact mode. The highest-impact
# learnings are kept (recall already returns them in relevance order); the
# remainder is summarized via a "N more" indicator. Chosen to keep the most
# load-bearing context while cutting the bulk of the token cost.
DEFAULT_TOP_K = 8

# Diagnostic sub-blocks that are low-signal for resume. In compact mode they
# are removed from the payload and folded into a one-line ``health_summary``.
# trw:intentional these are diagnostics, not resume state — safe to summarize.
_DIAGNOSTIC_KEYS = (
    "embed_health",
    "assertion_health",
    "sync_health",
    "step_durations_ms",
)

# Fields that are NEVER dropped in compact mode — losing any of these would
# break run/pin recovery, error reporting, or the framework reminder the agent
# depends on to resume. Used only as a documented invariant / guard reference.
LOAD_BEARING_KEYS = (
    "run",
    "errors",
    "success",
    "framework_reminder",
    "hint",
    "candidate_runs",
    "timestamp",
)

# ``# trw:intentional <reason>`` (Python/shell/YAML ``#``) or
# ``// trw:intentional <reason>`` (TS/JS/C-family). The reason is everything
# after the marker token, trimmed. Case-insensitive on the marker token.
_INTENTIONAL_RE = re.compile(
    r"(?:#|//)\s*trw:intentional\b[ \t:]*(?P<reason>.*?)\s*$",
    re.IGNORECASE,
)


def estimate_payload_tokens(payload: object) -> int:
    """Approximate the token cost of a JSON-serializable payload.

    Uses the common ~4-characters-per-token heuristic over the compact JSON
    serialization. This is an *estimate* for visibility (FR1 "make it
    measurable"), not an exact tokenizer count. Fail-open: returns 0 on any
    serialization error.
    """
    try:
        serialized = json.dumps(payload, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return 0
    return max(1, len(serialized) // 4)


def _summarize_health(results: SessionStartResultDict) -> str:
    """Collapse the diagnostic sub-blocks into a single human-readable line.

    Surfaces only the load-bearing signal: embed/assertion health counts and
    the total session_start latency. Degraded advisories are NOT touched here —
    they live in their own top-level keys (pipeline_health_advisory,
    embeddings_advisory, etc.) which compact mode preserves.
    """
    parts: list[str] = []

    embed = results.get("embed_health")
    if isinstance(embed, dict) and embed:
        status = embed.get("status") or embed.get("state")
        if status:
            parts.append(f"embed={status}")

    assertion = results.get("assertion_health")
    if isinstance(assertion, dict) and assertion:
        failing = assertion.get("failing", 0)
        total = assertion.get("total", 0)
        parts.append(f"assertions={failing} failing/{total}")

    sync = results.get("sync_health")
    if isinstance(sync, dict) and sync:
        status = sync.get("status") or sync.get("state")
        if status:
            parts.append(f"sync={status}")

    durations = results.get("step_durations_ms")
    if isinstance(durations, dict):
        total_ms = durations.get("total")
        if isinstance(total_ms, (int, float)):
            parts.append(f"start={round(float(total_ms))}ms")

    if not parts:
        return "ok (verbose=True for full diagnostics)"
    return "; ".join(parts) + " (verbose=True for full diagnostics)"


def trim_session_start_payload(
    results: SessionStartResultDict,
    *,
    verbose: bool,
    top_k: int = DEFAULT_TOP_K,
) -> SessionStartResultDict:
    """Trim ``trw_session_start`` output to a compact payload by default.

    FR1. In compact mode (``verbose=False``):

    - The learnings list is capped to the top-K most relevant entries (recall
      already returns them in relevance/impact order, so slicing preserves the
      highest-signal items). ``learnings_count`` is set to the *kept* count and
      ``learnings_omitted`` records how many were dropped ("N more").
    - The low-signal diagnostic sub-blocks (embed_health, assertion_health,
      sync_health, step_durations_ms) are removed and summarized into a
      one-line ``health_summary``.
    - A ``payload_token_estimate`` is added so the reduction is measurable.
    - ``compact`` is set to ``True``.

    Load-bearing fields (run/pin, errors, framework_reminder, hints,
    advisories) are preserved unchanged.

    In verbose mode the payload is returned unchanged except for the added
    ``compact=False`` flag and ``payload_token_estimate`` (so the size is still
    measurable). ``verbose=True`` reproduces the legacy full behavior.

    Fail-open: any internal error returns the original ``results`` untouched.
    """
    try:
        if verbose:
            results["compact"] = False
            results["payload_token_estimate"] = estimate_payload_tokens(results)
            return results

        learnings = results.get("learnings")
        if isinstance(learnings, list) and len(learnings) > top_k:
            kept = learnings[:top_k]
            omitted = len(learnings) - len(kept)
            results["learnings"] = kept
            results["learnings_count"] = len(kept)
            results["learnings_omitted"] = omitted
        elif isinstance(learnings, list):
            results["learnings_count"] = len(learnings)
            results["learnings_omitted"] = 0

        summary = _summarize_health(results)
        for key in _DIAGNOSTIC_KEYS:
            results.pop(key, None)  # type: ignore[misc]
        results["health_summary"] = summary

        results["compact"] = True
        results["payload_token_estimate"] = estimate_payload_tokens(results)
        return results
    except Exception:  # justified: fail-open, trimming must never break resume
        logger.debug("session_start_trim_failed", exc_info=True)
        return results


def find_intentional_marker(
    source: str,
    line_number: int,
    *,
    lookback: int = 1,
) -> str | None:
    """Return the reason from a ``trw:intentional`` marker on/above a line.

    FR2. ``source`` is the full file text; ``line_number`` is 1-indexed. The
    marker is recognized on the target line itself (trailing-comment form) or
    on any of the ``lookback`` lines immediately above it (own-line form). The
    nearest marker (target line, then the line directly above, etc.) wins.

    Returns the trimmed reason string (possibly empty if the marker carries no
    reason text), or ``None`` when no marker is present. Fail-open: returns
    ``None`` on malformed input.
    """
    try:
        lines = source.splitlines()
        if line_number < 1 or line_number > len(lines):
            return None
        # Search the target line first, then walk upward through lookback lines.
        for offset in range(lookback + 1):
            idx = line_number - 1 - offset
            if idx < 0:
                break
            match = _INTENTIONAL_RE.search(lines[idx])
            if match is not None:
                return match.group("reason").strip()
        return None
    except Exception:  # justified: fail-open, marker detection is best-effort tooling
        logger.debug("intentional_marker_scan_failed", exc_info=True)
        return None
