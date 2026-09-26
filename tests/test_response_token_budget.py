"""Tool-response size-budget tripwires (operator mandate 2026-07-12).

WHY THIS EXISTS — the cost model every response field must answer to:

Tool responses are paid for on EVERY call, by EVERY calling LLM, for the life
of the deployment. A field that costs 50 tokens on a hot-path tool
(session_start / recall / checkpoint / learn) costs tens of thousands of
tokens per working day across sessions — crowding out the caller's actual
working context. The 2026-07-12 campaign measured the drift this produces:
trw_recall had ballooned to ~22k tokens per default call (38 keys/entry,
internal scoring state 3x the content) and trw_session_start shipped five
near-identical deferral blocks. See trw-mcp CHANGELOG 0.57.0.

Sizes use a test-only four-character JSON heuristic, not actual tokenizer counts.
These are SOFT ceilings with generous slack (~35% above the post-campaign
measurements), not byte-exact snapshots. If your change trips one:

1. Ask whether the calling LLM needs the field to act. Diagnostics belong in
   structlog events; audit detail belongs behind ``verbose=True``; constants
   and derivable values belong nowhere.
2. Prefer the established compaction patterns: compact-by-default +
   ``verbose=True`` passthrough, fail-open helpers, fold-by-shape summaries
   (see ``tools/_session_start_trim.py`` and ``tools/_recall_projection.py``).
3. If the growth is genuinely load-bearing, raise the ceiling IN THE SAME
   change with a comment recording the new measurement and why the fields
   earn their place. Never raise it to "make the test pass".
"""

from __future__ import annotations

from typing import cast

import pytest

from tests._ceremony_helpers import payload_size_units
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import SessionStartResultDict
from trw_mcp.tools._ceremony_runtime_helpers import _no_active_run_hint
from trw_mcp.tools._recall_projection import strip_internal_response_fields
from trw_mcp.tools._session_start_trim import trim_session_start_payload

# Post-campaign measurements (2026-07-12, real stdio): session_start ~966 size units,
# recall (25 entries, 8000 budget) ~7.7k size units. Ceilings = measurement + slack.
#
# 2026-07-24 correction (UF-052): the session_start fixture below had OMITTED the
# unconditionally-emitted connection_fingerprint block, so the ~966 figure
# understated what agents actually receive. With the block present and the
# zero-match query_advisory populated, the honest compact measurement is
# ~1038 size units (~1134 if connection_fingerprint were not reduced in compact mode).
# The ceiling is unchanged — the fixture got more truthful, not the payload
# bigger.
#
# 2026-09-23 PRD-CORE-294 FR02/NFR02: session_start runs one recall and shows at
# most three stubs, so the fixture above now carries the stub block. Measured
# 782 size units compact (learning block 1,223 bytes); the ceiling drops from
# 1300 to 1000.
SESSION_START_CEILING_SIZE_UNITS = 1000
RECALL_ENTRY_CEILING_SIZE_UNITS = 450  # per projected entry with rich content

_BLOAT_GUIDANCE = (
    "Response size budget exceeded — every field here is paid on EVERY call "
    "by EVERY calling LLM. Move diagnostics to structlog, put audit detail "
    "behind verbose=True, drop derivable/constant fields; if the fields are "
    "genuinely load-bearing, raise the ceiling in this same change with the "
    "new measurement and justification. See this file's docstring and "
    ".claude/rules/trw-mcp-python.md §Tool Response Token Budget."
)


def _representative_session_start_payload() -> dict[str, object]:
    """A worst-case-ish session_start payload: full run + rich diagnostics."""
    return {
        "timestamp": "2026-07-12T00:00:00+00:00",
        # PRD-CORE-294 FR02: the recall step already presented at most three stubs.
        "learnings": [
            {
                "id": f"L-{i:08d}",
                "claim": "A representative learning summary of realistic length covering a gotcha "
                "discovered in a prior session" + "x" * 40,
                "anchor": "trw-mcp/src/trw_mcp/tools/_session_recall_helpers.py:perform_session_recalls",
            }
            for i in range(3)
        ],
        "learnings_count": 3,
        "learnings_omitted": 9,
        "query": "representative focused recall query",
        "store_count": 1402,
        "run": {"active_run": None, "status": "no_active_run"},
        # Built from the live production builder rather than a copied literal:
        # the previous hard-coded copy had already drifted from the shipped
        # string, so the tripwire was measuring a payload nobody receives.
        # PRD-CORE-233 FR03 added the run_path= remedy here (~+15 size units).
        "hint": _no_active_run_hint([{"run_path": "/x", "pin_key": "k"}]),
        "candidate_runs": [
            {
                "run_path": f"/home/user/project/.trw/runs/some-task/2026071{i}T000000Z-abcdef{i}",
                "pin_key": f"00000000-0000-0000-0000-00000000000{i}",
                "pid": 100000 + i,
                "last_heartbeat_ts": "2026-07-12T00:00:00.000000Z",
            }
            for i in range(3)
        ],
        # PRD-CORE-215 FR01 block, emitted unconditionally by
        # finalize_session_start. Omitting it here let the tripwire certify a
        # payload ~124 size units smaller than agents actually receive (UF-052).
        # Keep it byte-shaped like build_connection_fingerprint() output.
        "connection_fingerprint": {
            "protocol_version": "2",
            "build_identity": "0.63.0",
            "project_identity": "trw-framework",
            "connection_nonce": "d" * 32,
            "result_schema": "trw.session_start.v1",
            "transport": "stdio",
            "owner_status_capability": True,
            "request_identity_capability": True,
            "process_fingerprint_digest": "e" * 64,
            "loaded_module_digest": "f" * 64,
        },
        "query_advisory": (
            "Focused recall matched 0 entries: the vector index was not initialized in this "
            "process (session_start never triggers a model load), so only all-token keyword "
            "matching ran -- a multi-word natural-language query cannot match that way. Call "
            "trw_recall(query=...) for full hybrid BM25+vector search."
        ),
        "surface_snapshot_id": "a" * 64,
        "resolved_profile": {"ceremony_tier": "COMPREHENSIVE"},
        "profile_layers_applied": ["defaults"],
        "profile_snapshot_id": "surf_" + "b" * 64,
        "session_override_hash": "sess_" + "c" * 64,
        "assertion_health": {"failing": 0, "total": 5},
        "sync_health": {"status": "ok"},
        "step_durations_ms": {"total": 900.0},
        "first_session_emitted": False,
        "errors": [],
        "success": True,
        "framework_reminder": (
            "Preserve unfinished work with trw_checkpoint() or a durable handoff. "
            "Use trw_deliver() only to accept completed work under delivery gates."
        ),
    }


def test_session_start_compact_payload_stays_under_ceiling() -> None:
    fixture = cast("SessionStartResultDict", _representative_session_start_payload())
    payload = trim_session_start_payload(fixture, verbose=False)
    size_units = payload_size_units(payload)
    assert size_units <= SESSION_START_CEILING_SIZE_UNITS, (
        f"compact trw_session_start payload is {size_units} four-character size units "
        f"(ceiling {SESSION_START_CEILING_SIZE_UNITS}). {_BLOAT_GUIDANCE}"
    )


def test_session_start_learning_block_stays_under_its_byte_budget() -> None:
    """NFR02: the learning block (stubs plus its own keys) is at most 1,500 encoded bytes."""
    import json

    from trw_mcp.tools._recall_presenter import SESSION_BYTE_BUDGET

    payload = trim_session_start_payload(
        cast("SessionStartResultDict", _representative_session_start_payload()), verbose=False
    )
    block_keys = ("learnings", "learnings_omitted", "query", "query_advisory", "store_count")
    block = {key: payload[key] for key in block_keys if key in payload}  # type: ignore[literal-required]
    size = len(json.dumps(block).encode("utf-8"))
    assert SESSION_BYTE_BUDGET == 1_500
    assert size <= SESSION_BYTE_BUDGET, f"session_start learning block is {size} bytes. {_BLOAT_GUIDANCE}"


def test_session_start_fixture_includes_connection_fingerprint() -> None:
    """UF-052 regression: the tripwire fixture must carry every block that the
    session-start finalizer emits unconditionally, or it certifies a payload
    smaller than agents receive. connection_fingerprint is written by
    ``finalize_session_start`` on EVERY call, so it belongs in the fixture."""
    from trw_mcp.tools._connection_fingerprint import build_connection_fingerprint

    fixture_block = _representative_session_start_payload()["connection_fingerprint"]
    assert isinstance(fixture_block, dict)
    assert set(fixture_block) == set(build_connection_fingerprint()), (
        "fixture connection_fingerprint has drifted from the emitted block — "
        "the measured payload no longer matches what agents receive."
    )


def test_compact_mode_reduces_connection_fingerprint_to_non_constant_fields() -> None:
    """Compact mode keeps only the two fields that vary and that a caller can
    act on. The eight constants/derivables/opaque digests cost ~96 size units on
    every session for zero decision value."""
    fixture = cast("SessionStartResultDict", _representative_session_start_payload())
    unreduced = payload_size_units(fixture["connection_fingerprint"])

    payload = trim_session_start_payload(fixture, verbose=False)
    block = payload["connection_fingerprint"]
    assert isinstance(block, dict)
    assert set(block) == {"build_identity", "connection_nonce"}
    assert payload_size_units(block) < unreduced


def test_verbose_mode_preserves_full_connection_fingerprint() -> None:
    """verbose=True stays the full-audit path — the tamper digests required by
    PRD-INFRA-164 FR07/NFR04 must remain reachable."""
    fixture = cast("SessionStartResultDict", _representative_session_start_payload())
    payload = trim_session_start_payload(fixture, verbose=True)
    block = payload["connection_fingerprint"]
    assert isinstance(block, dict)
    assert "loaded_module_digest" in block
    assert "process_fingerprint_digest" in block
    assert "transport" in block


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
        "last_accessed_at": "2026-07-11",
        "anchor_validity": 1.0,
        "recurrence": 1,
    }
    projected = strip_internal_response_fields([entry], get_config().recall_internal_fields)
    size_units = payload_size_units(projected[0])
    assert size_units <= RECALL_ENTRY_CEILING_SIZE_UNITS, (
        f"projected recall entry is {size_units} four-character size units (ceiling {RECALL_ENTRY_CEILING_SIZE_UNITS}). {_BLOAT_GUIDANCE}"
    )


def test_recall_internal_field_stripping_is_configured() -> None:
    """The projection must never be silently disabled — an empty
    recall_internal_fields default would reintroduce the ~22k-token recall."""
    assert get_config().recall_internal_fields, (
        "recall_internal_fields default is empty — internal scoring state "
        f"would ship on every recall entry again. {_BLOAT_GUIDANCE}"
    )


# PRD-FIX-144 NFR02: key sets captured from the pre-change code (2026-09-18) by
# driving this exact sequence. The telemetry it added (receipt keys, hint
# exposure rows, feedback outcome rows, session observations) goes to logs only.
_PRE_FIX144_RESPONSE_KEYS: dict[str, set[str]] = {
    # PRD-CORE-294 FR01 cut trw_recall to stubs within a byte budget: the eleven
    # counter/shaping keys are gone and nothing may come back.
    "trw_recall": {"ceremony_status", "learnings", "nudge_content", "query", "total_matches"},
    "trw_code_hint": {
        "distill_action",
        "distill_hint",
        "distill_sidecar_path",
        "distill_sidecar_sha",
        "distill_status",
        "enrichment",
        "file_path",
        "learnings",
        "learnings_count",
        "tier",
        # PRD-CORE-294 FR04(b): this scenario's own trw_learn call above wrote
        # a learning anchored to "app.py", so this exact before_edit_hint call
        # now legitimately surfaces one transition-nudge line naming it — the
        # key is conditional (only present when a candidate is selected), not
        # unconditional bloat.
        "transition_nudge",
    },
    "trw_build_check": {
        "build_receipt_id",
        "cache_path",
        "ceremony_status",
        "coverage_pct",
        "failure_count",
        "failures",
        "mypy_clean",
        "nudge_content",
        "reversion_prompt",
        "scope",
        "static_checks_clean",
        "step_durations_ms",
        "test_count",
        "tests_passed",
        "timed_out",
        "typed_receipt_reason",
        "typed_receipt_state",
    },
}


def test_feedback_telemetry_adds_no_response_keys(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._memory_store_fake import FakeMemoryStore
    from tests.conftest import extract_tool_fn, make_test_server
    from trw_mcp.state import _store_selection

    # Pinned to "default" (not the shared fixture's FAKE_NAMESPACE): the fake's
    # recall() only searches "default", and this test needs trw_recall to find
    # what trw_learn just wrote (see test_tools_learning_recall_modes.py for
    # the same workaround).
    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "default"))

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    server = make_test_server("learning", "code", "build")
    extract_tool_fn(server, "trw_learn")(
        summary="app.py startup must load config first", detail="app.py reads config.", impact=0.7
    )
    observed = {
        "trw_recall": set(extract_tool_fn(server, "trw_recall")(query="app.py startup")),
        # Unwrap the {"status","hints","count"} trw_code envelope to the single
        # per-file hint dict, so this key set is comparable to the old
        # trw_code_hint tool's flat response.
        "trw_code_hint": set(extract_tool_fn(server, "trw_code")(mode="hint", files="app.py")["hints"][0]),
        "trw_build_check": set(extract_tool_fn(server, "trw_build_check")(tests_passed=False, test_count=1)),
    }
    assert observed == _PRE_FIX144_RESPONSE_KEYS, _BLOAT_GUIDANCE
    # Non-vacuity: the telemetry really was written, just not into the responses.
    logs = tmp_project / ".trw" / "logs"
    assert (logs / "session_outcomes.jsonl").exists()
    assert '"surface": "before_edit_hint"' in (logs / "recall_tracking.jsonl").read_text()
