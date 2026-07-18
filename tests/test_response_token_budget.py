"""Tool-response token-budget tripwires (operator mandate 2026-07-12).

WHY THIS EXISTS — the cost model every response field must answer to:

Tool responses are paid for on EVERY call, by EVERY calling LLM, for the life
of the deployment. A field that costs 50 tokens on a hot-path tool
(session_start / recall / checkpoint / learn) costs tens of thousands of
tokens per working day across sessions — crowding out the caller's actual
working context. The 2026-07-12 campaign measured the drift this produces:
trw_recall had ballooned to ~22k tokens per default call (38 keys/entry,
internal scoring state 3x the content) and trw_session_start shipped five
near-identical deferral blocks. See trw-mcp CHANGELOG 0.57.0.

These are SOFT ceilings with generous slack (~35% above the post-campaign
measurements), not byte-exact snapshots. If your change trips one:

1. Ask whether the calling LLM needs the field to act. Diagnostics belong in
   structlog events; audit detail belongs behind ``verbose=True``; constants
   and derivable values belong nowhere.
2. Prefer the established compaction patterns: compact-by-default +
   ``verbose=True`` passthrough, fail-open helpers, fold-by-shape summaries
   (see ``tools/_session_start_trim.py`` and ``tools/_recall_projection.py``).
3. If the growth is genuinely load-bearing, raise the ceiling IN THE SAME
   change with a comment recording the new measurement and why the tokens
   earn their place. Never raise it to "make the test pass".
"""

from __future__ import annotations

from typing import cast

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import SessionStartResultDict
from trw_mcp.tools._recall_projection import strip_internal_response_fields
from trw_mcp.tools._session_start_trim import (
    estimate_payload_tokens,
    trim_session_start_payload,
)

# Post-campaign measurements (2026-07-12, real stdio): session_start ~966 tok,
# recall (25 entries, 8000 budget) ~7.7k tok. Ceilings = measurement + slack.
SESSION_START_CEILING_TOKENS = 1300
RECALL_ENTRY_CEILING_TOKENS = 450  # per projected entry with rich content

_BLOAT_GUIDANCE = (
    "Response token budget exceeded — every field here is paid on EVERY call "
    "by EVERY calling LLM. Move diagnostics to structlog, put audit detail "
    "behind verbose=True, drop derivable/constant fields; if the tokens are "
    "genuinely load-bearing, raise the ceiling in this same change with the "
    "new measurement and justification. See this file's docstring and "
    ".claude/rules/trw-mcp-python.md §Tool Response Token Budget."
)


def _representative_session_start_payload() -> dict[str, object]:
    """A worst-case-ish session_start payload: pressure deferrals + full run."""
    deferral = {"reason": "writer_pressure", "writer_count": 9, "threshold": 2}
    return {
        "timestamp": "2026-07-12T00:00:00+00:00",
        "learnings": [
            {
                "id": f"L-{i:08d}",
                "summary": "A representative learning summary of realistic length "
                "covering a gotcha discovered in a prior session" + "x" * 40,
                "impact": 0.95,
                "status": "active",
            }
            for i in range(12)
        ],
        "learnings_count": 12,
        "query": "representative focused recall query",
        "query_matched": 13,
        "total_available": 12,
        "response_compacted": True,
        "side_effects_deferred": dict(deferral),
        "auto_upgrade_check_deferred": dict(deferral),
        "stale_runs_deferred": dict(deferral),
        "embeddings_backfill_deferred": dict(deferral),
        "wal_checkpoint_deferred": dict(deferral),
        "auto_recall_deferred": {"reason": "session_start_compacted", "detail": "optional"},
        "ceremony_status_deferred": {"reason": "session_start_compacted", "detail": "optional"},
        "run": {"active_run": None, "status": "no_active_run"},
        "hint": "No active run for this session. Call trw_init() to create a new run, "
        "or call trw_adopt_run(run_path=...) to resume an existing run.",
        "candidate_runs": [
            {
                "run_path": f"/home/user/project/.trw/runs/some-task/2026071{i}T000000Z-abcdef{i}",
                "pin_key": f"00000000-0000-0000-0000-00000000000{i}",
                "pid": 100000 + i,
                "last_heartbeat_ts": "2026-07-12T00:00:00.000000Z",
            }
            for i in range(3)
        ],
        "surface_snapshot_id": "a" * 64,
        "resolved_profile": {"ceremony_tier": "COMPREHENSIVE"},
        "profile_layers_applied": ["defaults"],
        "profile_snapshot_id": "surf_" + "b" * 64,
        "session_override_hash": "sess_" + "c" * 64,
        "profile_explanation": {"fields": [{"field": "x", "value": None}] * 10},
        "embed_health": {"status": "ok"},
        "assertion_health": {"failing": 0, "total": 5},
        "sync_health": {"status": "ok"},
        "step_durations_ms": {"total": 900.0},
        "first_session_emitted": False,
        "errors": [],
        "success": True,
        "framework_reminder": "Call trw_deliver() when done to persist your work.",
    }


def test_session_start_compact_payload_stays_under_ceiling() -> None:
    fixture = cast("SessionStartResultDict", _representative_session_start_payload())
    payload = trim_session_start_payload(fixture, verbose=False)
    tokens = estimate_payload_tokens(payload)
    assert tokens <= SESSION_START_CEILING_TOKENS, (
        f"compact trw_session_start payload is ~{tokens} tokens "
        f"(ceiling {SESSION_START_CEILING_TOKENS}). {_BLOAT_GUIDANCE}"
    )


def test_recall_projected_entry_stays_under_ceiling() -> None:
    entry: dict[str, object] = {
        "id": "L-abcdef01",
        "summary": "s" * 120,
        "detail": "d" * 900,
        "tags": ["tag-one", "tag-two", "tag-three", "tag-four"],
        "evidence": ["src/module/file.py", "tests/test_file.py"],
        "impact": 0.9,
        "type": "gotcha",
        "status": "active",
        "confidence": "verified",
        "created": "2026-07-01",
        "updated": "2026-07-10",
        # Internal state the projection must remove:
        "outcome_history": [{"outcome": "pass"}] * 8,
        "q_observations": 12,
        "q_value": 0.42,
        "combined_score": 0.88,
        "access_count": 64,
        "recall_count": 9,
        "helpful_count": 3,
        "unhelpful_count": 0,
        "session_count": 4,
        "sessions_surfaced": ["s1", "s2"],
        "last_accessed_at": "2026-07-11",
        "anchor_validity": 1.0,
        "avg_rework_delta": 0.1,
        "recurrence": 1,
        "outcome_correlation": {"positive": 2},
    }
    projected = strip_internal_response_fields([entry], get_config().recall_internal_fields)
    tokens = estimate_payload_tokens(projected[0])
    assert tokens <= RECALL_ENTRY_CEILING_TOKENS, (
        f"projected recall entry is ~{tokens} tokens (ceiling {RECALL_ENTRY_CEILING_TOKENS}). {_BLOAT_GUIDANCE}"
    )


def test_recall_internal_field_stripping_is_configured() -> None:
    """The projection must never be silently disabled — an empty
    recall_internal_fields default would reintroduce the ~22k-token recall."""
    assert get_config().recall_internal_fields, (
        "recall_internal_fields default is empty — internal scoring state "
        f"would ship on every recall entry again. {_BLOAT_GUIDANCE}"
    )
