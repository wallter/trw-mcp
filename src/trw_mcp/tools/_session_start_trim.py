"""trw_session_start payload trimming + intentional-marker helpers.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

PRD-IMPROVE-MCP-04:

- FR1 — ``trim_session_start_payload`` makes ``trw_session_start`` compact by
  default. The full payload (assertion_health + sync_health +
  step_durations_ms) is large and is returned on *every* session. This reduces
  the ``connection_fingerprint`` block to its non-constant fields and collapses
  the low-signal diagnostic sub-blocks into a one-line ``health_summary``. Load-bearing
  fields (run/pin recovery, errors, framework_reminder, advisories) are NEVER
  dropped. ``verbose=True`` is a no-op pass-through (current full behavior).

- FR2 — ``find_intentional_marker`` detects a ``# trw:intentional <reason>``
  (or ``// trw:intentional <reason>``) marker on or just above a given line so
  scanners/reviewers can treat flagged code as a settled, deliberate decision.

Fail-open: trimming never raises; on any internal error the original payload is
returned unchanged so resume correctness is preserved.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import SessionStartResultDict

logger = structlog.get_logger(__name__)

# Diagnostic sub-blocks that are low-signal for resume. In compact mode they
# are removed from the payload and folded into a one-line ``health_summary``.
# trw:intentional these are diagnostics, not resume state — safe to summarize.
_DIAGNOSTIC_KEYS = (
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
    # Emitted only on a zero-match focused recall; it is the only thing that
    # explains that the returned learnings are NOT query matches.
    "query_advisory",
)

# Compact-mode projection of the PRD-CORE-215 FR01 connection fingerprint.
# FR01 requires the block on every session_start, so it is reduced rather than
# dropped. Eight of its ten fields are literal constants or caller-derivable
# (protocol_version, result_schema, transport, owner_status_capability,
# request_identity_capability, project_identity) or opaque 64-char digests with
# no documented caller action (process_fingerprint_digest, loaded_module_digest)
# — ~124 tokens on every call for zero decision value. The two kept fields are
# the only ones that vary and that a caller can act on: build_identity (which
# server version answered) and connection_nonce (which stdio process). The full
# ten-field block, including the tamper digests, is preserved under verbose=True.
_FINGERPRINT_COMPACT_FIELDS = ("build_identity", "connection_nonce")

# Identity/provenance stamps dropped outright in compact mode — not folded into
# health_summary, because they carry no health signal to summarize.
#
# Each is an opaque digest or an internal telemetry flag with no caller action:
# there is nothing an agent can do differently on seeing one, and none is an
# input to any other tool. They are all consumed INTERNALLY before this trim
# runs (step_log_session_event stamps the run's own bootstrap event with
# surface_snapshot_id during run_steps; step_first_session_marker writes its own
# idempotence flag file), so dropping them at the response boundary costs no
# telemetry. ~90 tokens on the single most frequently called tool in the
# roster — every session, plus every post-compaction resume.
#
# The full profile-resolution audit shape, including both snapshot ids and the
# layer chain, is what trw_status(detail="surface") exists to serve. verbose=True keeps
# them here too.
# trw:intentional diagnostics with no caller action — response-boundary only.
_COMPACT_DROP_KEYS = (
    "surface_snapshot_id",
    "profile_snapshot_id",
    "session_override_hash",
    "profile_layers_applied",
    "first_session_emitted",
)

# ``# trw:intentional <reason>`` (Python/shell/YAML ``#``) or
# ``// trw:intentional <reason>`` (TS/JS/C-family). The reason is everything
# after the marker token, trimmed. Case-insensitive on the marker token.
_INTENTIONAL_RE = re.compile(
    r"(?:#|//)\s*trw:intentional\b[ \t:]*(?P<reason>.*?)\s*$",
    re.IGNORECASE,
)


def _summarize_health(results: SessionStartResultDict) -> str:
    """Collapse the diagnostic sub-blocks into a single human-readable line.

    Surfaces only the load-bearing signal: assertion health counts and
    the total session_start latency. Degraded advisories are NOT touched here —
    they live in their own top-level keys (pipeline_health_advisory,
    embeddings_coverage_ratio, etc.) which compact mode preserves.
    """
    parts: list[str] = []

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


def _compact_connection_fingerprint(results: SessionStartResultDict) -> None:
    """Reduce the FR01 connection fingerprint to its non-constant fields.

    Mutates *results* in place. A missing or non-dict block is left untouched,
    and a block that carries none of the kept fields is left as-is (fail-safe:
    never replace a fingerprint with an empty dict).
    """
    block = results.get("connection_fingerprint")
    if not isinstance(block, dict):
        return
    reduced = {field: block[field] for field in _FINGERPRINT_COMPACT_FIELDS if field in block}
    if reduced:
        results["connection_fingerprint"] = reduced


def trim_session_start_payload(
    results: SessionStartResultDict,
    *,
    verbose: bool,
) -> SessionStartResultDict:
    """Trim ``trw_session_start`` output to a compact payload by default.

    FR1. In compact mode (``verbose=False``):

    - The learnings are left as the recall step presented them: PRD-CORE-294
      FR02 already bounded them to the stub block, so nothing is capped here.
    - The ``connection_fingerprint`` block is reduced to its two non-constant
      fields (PRD-CORE-215 FR01 requires the block, not every field).
    - The low-signal diagnostic sub-blocks (assertion_health, sync_health,
      step_durations_ms) are removed and summarized into a
      one-line ``health_summary``.
    - The identity/provenance stamps in ``_COMPACT_DROP_KEYS`` (snapshot ids,
      the session override hash, the profile layer chain, the first-session
      flag) are dropped outright — they carry no health signal to summarize and
      no caller action, and are consumed internally before this runs.
    - ``compact`` is set to ``True``.

    Load-bearing fields (run/pin, errors, framework_reminder, hints,
    advisories) are preserved unchanged.

    In verbose mode the payload is returned unchanged except for the added
    ``compact=False`` flag. ``verbose=True`` reproduces the legacy full behavior.

    Fail-open: any internal error returns the original ``results`` untouched.
    """
    try:
        if verbose:
            results["compact"] = False
            return results

        summary = _summarize_health(results)
        for key in _DIAGNOSTIC_KEYS:
            results.pop(key, None)  # type: ignore[misc]
        results["health_summary"] = summary

        for key in _COMPACT_DROP_KEYS:
            results.pop(key, None)  # type: ignore[misc]

        _compact_connection_fingerprint(results)

        results["compact"] = True
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
