"""Unit tests for PRD-IMPROVE-MCP-04 session_start payload trimming + marker.

Covers ``_session_start_trim``:

- FR1: ``trim_session_start_payload`` compact-by-default (top-K cap, health
  summary, token estimate, load-bearing-field preservation) and verbose
  pass-through.
- FR1: ``estimate_payload_tokens`` produces a positive monotonic estimate.
- FR2: ``find_intentional_marker`` detects the marker on/above a line and
  extracts the reason; absence returns ``None``.
"""

from __future__ import annotations

from typing import cast

from trw_mcp.models.typed_dicts import SessionStartResultDict
from trw_mcp.tools._session_start_trim import (
    DEFAULT_TOP_K,
    estimate_payload_tokens,
    find_intentional_marker,
    trim_session_start_payload,
)


def _make_results(n_learnings: int) -> SessionStartResultDict:
    learnings = [{"id": f"L-{i:03d}", "summary": f"learning {i}", "impact": 0.9 - i * 0.01} for i in range(n_learnings)]
    return cast(
        "SessionStartResultDict",
        {
            "timestamp": "2026-06-03T00:00:00Z",
            "learnings": learnings,
            "learnings_count": n_learnings,
            "run": {"active_run": "/path/run", "phase": "IMPLEMENT"},
            "errors": [],
            "success": True,
            "framework_reminder": "Read FRAMEWORK.md",
            "embed_health": {"status": "ok", "embedded": 100, "missing": 0},
            "assertion_health": {"failing": 2, "total": 10, "passing": 8},
            "sync_health": {"status": "ok"},
            "step_durations_ms": {"recall": 12.3, "total": 42.0},
            "pipeline_health_advisory": "graph empty — run trw_knowledge_sync",
        },
    )


class TestCompactTrimming:
    """FR1 — default compact mode trims while preserving load-bearing fields."""

    def test_compact_caps_learnings_to_top_k(self) -> None:
        results = _make_results(20)
        trimmed = trim_session_start_payload(results, verbose=False)

        assert trimmed["compact"] is True
        assert len(trimmed["learnings"]) == DEFAULT_TOP_K
        assert trimmed["learnings_count"] == DEFAULT_TOP_K
        assert trimmed["learnings_omitted"] == 20 - DEFAULT_TOP_K

    def test_compact_preserves_top_k_relevance_ordering(self) -> None:
        results = _make_results(20)
        trimmed = trim_session_start_payload(results, verbose=False)
        kept_ids = [e["id"] for e in trimmed["learnings"]]
        # Recall returns relevance/impact order; the kept slice must be the
        # highest-signal prefix, in order.
        assert kept_ids == [f"L-{i:03d}" for i in range(DEFAULT_TOP_K)]

    def test_compact_summarizes_diagnostic_subblocks(self) -> None:
        results = _make_results(3)
        trimmed = trim_session_start_payload(results, verbose=False)

        # Diagnostic blocks removed...
        assert "embed_health" not in trimmed
        assert "assertion_health" not in trimmed
        assert "sync_health" not in trimmed
        assert "step_durations_ms" not in trimmed
        # ...folded into a one-line summary carrying the load-bearing signal.
        summary = trimmed["health_summary"]
        assert "embed=ok" in summary
        assert "2 failing/10" in summary
        assert "ms" in summary

    def test_compact_preserves_load_bearing_fields(self) -> None:
        results = _make_results(20)
        trimmed = trim_session_start_payload(results, verbose=False)

        assert trimmed["run"] == {"active_run": "/path/run", "phase": "IMPLEMENT"}
        assert trimmed["errors"] == []
        assert trimmed["framework_reminder"] == "Read FRAMEWORK.md"
        assert trimmed["success"] is True
        # Degraded advisory must survive compaction.
        assert trimmed["pipeline_health_advisory"].startswith("graph empty")

    def test_compact_records_token_estimate(self) -> None:
        results = _make_results(20)
        trimmed = trim_session_start_payload(results, verbose=False)
        assert isinstance(trimmed["payload_token_estimate"], int)
        assert trimmed["payload_token_estimate"] > 0

    def test_compact_reduces_token_cost_vs_verbose(self) -> None:
        full = _make_results(20)
        compact = _make_results(20)
        verbose_out = trim_session_start_payload(full, verbose=True)
        compact_out = trim_session_start_payload(compact, verbose=False)
        assert compact_out["payload_token_estimate"] < verbose_out["payload_token_estimate"]

    def test_compact_small_corpus_not_capped(self) -> None:
        results = _make_results(3)
        trimmed = trim_session_start_payload(results, verbose=False)
        assert len(trimmed["learnings"]) == 3
        assert trimmed["learnings_omitted"] == 0


class TestVerbosePassthrough:
    """FR1 — verbose mode returns the full diagnostic payload (legacy behavior)."""

    def test_verbose_keeps_all_learnings(self) -> None:
        results = _make_results(20)
        trimmed = trim_session_start_payload(results, verbose=True)
        assert len(trimmed["learnings"]) == 20
        assert trimmed["compact"] is False

    def test_verbose_keeps_diagnostic_subblocks(self) -> None:
        results = _make_results(20)
        trimmed = trim_session_start_payload(results, verbose=True)
        assert "embed_health" in trimmed
        assert "assertion_health" in trimmed
        assert "step_durations_ms" in trimmed
        assert "health_summary" not in trimmed

    def test_verbose_records_token_estimate(self) -> None:
        results = _make_results(20)
        trimmed = trim_session_start_payload(results, verbose=True)
        assert trimmed["payload_token_estimate"] > 0


class TestFailOpen:
    """FR1 — trimming must never drop run-recovery or error fields."""

    def test_missing_learnings_key_does_not_raise(self) -> None:
        results = cast(
            "SessionStartResultDict",
            {"run": {"active_run": "/r"}, "errors": ["boom"], "success": False},
        )
        trimmed = trim_session_start_payload(results, verbose=False)
        assert trimmed["run"] == {"active_run": "/r"}
        assert trimmed["errors"] == ["boom"]
        assert trimmed["compact"] is True


class TestEstimatePayloadTokens:
    def test_monotonic_with_size(self) -> None:
        small = estimate_payload_tokens({"a": 1})
        large = estimate_payload_tokens({"a": "x" * 1000})
        assert large > small >= 1

    def test_non_serializable_fails_open(self) -> None:
        assert estimate_payload_tokens({"f": object()}) >= 1


class TestFindIntentionalMarker:
    """FR2 — detect the marker on/above a line and extract the reason."""

    def test_marker_above_line_python(self) -> None:
        source = "\n".join(
            [
                "def score():",
                "    # trw:intentional no-data is a fail by design",
                "    return 0.0",
            ]
        )
        # line 3 (1-indexed) is the return; marker is on line 2 (directly above).
        reason = find_intentional_marker(source, 3)
        assert reason == "no-data is a fail by design"

    def test_marker_trailing_comment_same_line(self) -> None:
        source = "    return 0.0  # trw:intentional fail-closed scorer\n"
        reason = find_intentional_marker(source, 1)
        assert reason == "fail-closed scorer"

    def test_marker_js_double_slash(self) -> None:
        source = "\n".join(
            [
                "// trw:intentional skip empty values",
                "if (!value) return;",
            ]
        )
        assert find_intentional_marker(source, 2) == "skip empty values"

    def test_marker_case_insensitive_and_colon(self) -> None:
        source = "# TRW:Intentional: truthfulness gate\nx = 1\n"
        assert find_intentional_marker(source, 2) == "truthfulness gate"

    def test_no_marker_returns_none(self) -> None:
        source = "x = 1\ny = 2\n"
        assert find_intentional_marker(source, 2) is None

    def test_marker_too_far_above_not_matched(self) -> None:
        source = "\n".join(
            [
                "# trw:intentional far away",
                "a = 1",
                "b = 2",
            ]
        )
        # default lookback=1; marker is 2 lines above line 3 -> not matched.
        assert find_intentional_marker(source, 3) is None

    def test_marker_with_wider_lookback(self) -> None:
        source = "\n".join(
            [
                "# trw:intentional far away",
                "a = 1",
                "b = 2",
            ]
        )
        assert find_intentional_marker(source, 3, lookback=2) == "far away"

    def test_marker_without_reason_returns_empty_string(self) -> None:
        source = "x = 1  # trw:intentional\n"
        assert find_intentional_marker(source, 1) == ""

    def test_out_of_range_line_returns_none(self) -> None:
        assert find_intentional_marker("a = 1\n", 99) is None


class TestFoldDeferredBlocks:
    """Compact mode folds repetitive *_deferred advisory blocks (2026-07-12)."""

    def _pressure_payload(self) -> dict:  # type: ignore[type-arg]
        block = {"reason": "writer_pressure", "writer_count": 9, "threshold": 2}
        return {
            "learnings": [],
            "run": {"active_run": None},
            "errors": [],
            "success": True,
            "side_effects_deferred": dict(block),
            "auto_upgrade_check_deferred": dict(block),
            "stale_runs_deferred": dict(block),
            "embeddings_backfill_deferred": dict(block),
            "wal_checkpoint_deferred": dict(block),
            "auto_recall_deferred": {"reason": "session_start_compacted", "detail": "optional"},
        }

    def test_compact_folds_deferral_blocks(self) -> None:
        result = trim_session_start_payload(self._pressure_payload(), verbose=False)

        assert "side_effects_deferred" not in result
        assert "wal_checkpoint_deferred" not in result
        assert result["deferred"]["writer_pressure"] == [
            "auto_upgrade_check",
            "embeddings_backfill",
            "side_effects",
            "stale_runs",
            "wal_checkpoint",
        ]
        assert result["deferred"]["session_start_compacted"] == ["auto_recall"]
        assert result["deferred_writer_count"] == 9

    def test_verbose_keeps_individual_blocks(self) -> None:
        result = trim_session_start_payload(self._pressure_payload(), verbose=True)

        assert "deferred" not in result
        assert result["side_effects_deferred"]["reason"] == "writer_pressure"

    def test_unrecognized_block_shape_is_not_folded(self) -> None:
        payload = self._pressure_payload()
        payload["custom_deferred"] = {"reason": "writer_pressure", "payload": {"x": 1}}
        result = trim_session_start_payload(payload, verbose=False)

        assert result["custom_deferred"] == {"reason": "writer_pressure", "payload": {"x": 1}}
        assert "custom" not in result["deferred"]["writer_pressure"]

    def test_no_deferred_blocks_no_summary_key(self) -> None:
        result = trim_session_start_payload({"learnings": [], "run": {}, "errors": [], "success": True}, verbose=False)
        assert "deferred" not in result
