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
    _COMPACT_DROP_KEYS,
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
        # PRD-CORE-257-FR04: the advisory gained the streak's age, its count and
        # the two measurement states, and dropped the legacy ``defer_reason``.
        # This fixture must track ``writer_pressure_details`` or the fold is
        # being exercised against a shape the code no longer emits.
        block = {
            "reason": "writer_pressure",
            "writer_count": 9,
            "peer_writer_count": 8,
            "threshold": 8,
            "deferral_age_hours": 1.5,
            "deferred_count": 3,
            "census_state": "measured",
            "ledger_state": "ok",
        }
        return {
            "learnings": [],
            "run": {"active_run": None},
            "errors": [],
            "success": True,
            "side_effects_deferred": dict(block),
            "auto_upgrade_check_deferred": dict(block),
            "stale_runs_deferred": dict(block),
            "embeddings_backfill_deferred": dict(block),
            # PRD-CORE-248 FR04 deleted wal_checkpoint_deferred; the fold is
            # generic over any *_deferred key, so this uses a step that still
            # defers rather than pinning the shape of one that cannot.
            "pending_learns_deferred": dict(block),
            "auto_recall_deferred": {"reason": "session_start_compacted", "detail": "optional"},
        }

    def test_compact_folds_deferral_blocks(self) -> None:
        result = trim_session_start_payload(self._pressure_payload(), verbose=False)

        assert "side_effects_deferred" not in result
        assert "pending_learns_deferred" not in result
        assert result["deferred"]["writer_pressure"] == [
            "auto_upgrade_check",
            "embeddings_backfill",
            "pending_learns",
            "side_effects",
            "stale_runs",
        ]
        assert result["deferred"]["session_start_compacted"] == ["auto_recall"]
        assert result["deferred_writer_count"] == 9
        # FR11: the compact response keeps the bar the work was deferred against
        # and how long it has been held, instead of stating only that it was.
        assert result["deferred_threshold"] == 8
        assert result["deferred_max_age_hours"] == 1.5
        assert result["deferred_census_state"] == "measured"
        assert result["deferred_ledger_state"] == "ok"

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

    def test_fold_reports_the_worst_age_and_the_least_reassuring_states(self) -> None:
        """Folding must never launder a degraded read into the healthiest one."""
        payload = self._pressure_payload()
        older = dict(payload["side_effects_deferred"])
        older.update({"deferral_age_hours": 5.75, "ledger_state": "degraded", "census_state": "unreadable"})
        payload["side_effects_deferred"] = older
        result = trim_session_start_payload(payload, verbose=False)

        assert result["deferred_max_age_hours"] == 5.75
        assert result["deferred_ledger_state"] == "degraded"
        assert result["deferred_census_state"] == "unreadable"

    def test_no_deferred_blocks_no_summary_key(self) -> None:
        result = trim_session_start_payload({"learnings": [], "run": {}, "errors": [], "success": True}, verbose=False)
        assert "deferred" not in result


class TestCompactDropKeys:
    """Identity/provenance stamps are dropped at the response boundary."""

    @staticmethod
    def _stamped_payload() -> SessionStartResultDict:
        return cast(
            "SessionStartResultDict",
            {
                "learnings": [],
                "run": {"status": "no_active_run"},
                "errors": [],
                "success": True,
                "surface_snapshot_id": "0" * 64,
                "profile_snapshot_id": "surf_" + "a" * 64,
                "session_override_hash": "sess_" + "b" * 64,
                "profile_layers_applied": ["defaults"],
                "first_session_emitted": False,
                "resolved_profile": {"ceremony_tier": "STANDARD"},
            },
        )

    def test_compact_drops_identity_stamps(self) -> None:
        result = trim_session_start_payload(self._stamped_payload(), verbose=False)

        for key in _COMPACT_DROP_KEYS:
            assert key not in result, f"{key} survived compaction"

    def test_compact_keeps_the_load_bearing_neighbours(self) -> None:
        """The drop must not take the resolved profile or run state with it."""
        result = trim_session_start_payload(self._stamped_payload(), verbose=False)

        assert result["resolved_profile"] == {"ceremony_tier": "STANDARD"}
        assert result["run"] == {"status": "no_active_run"}
        assert result["success"] is True

    def test_verbose_keeps_identity_stamps(self) -> None:
        """verbose=True remains the full-audit escape hatch."""
        result = trim_session_start_payload(self._stamped_payload(), verbose=True)

        for key in _COMPACT_DROP_KEYS:
            assert key in result, f"{key} was dropped even under verbose=True"

    def test_drop_is_measurable(self) -> None:
        payload = self._stamped_payload()
        before = estimate_payload_tokens(payload)
        after = trim_session_start_payload(payload, verbose=False)["payload_token_estimate"]

        assert after < before, "dropping five stamps did not shrink the payload"


# ---------------------------------------------------------------------------
# PRD-CORE-263-NFR04 — backward compatibility of the new health keys
# ---------------------------------------------------------------------------


def test_new_health_keys_survive_trim_when_not_healthy() -> None:
    """PRD-CORE-263-NFR04 — a degraded or unmeasured result keeps its reason.

    The added keys are ``sync_health.status``/``reason`` (FR02) and the per-probe
    ``measured`` flag plus the aggregate ``unmeasured`` list (FR03). The trim may
    FOLD the sync block for a fully healthy result — it already does, into
    ``health_summary`` — but the not-measured state must remain readable, and the
    pipeline block is not a trimmed diagnostic at all.
    """
    payload = cast(
        "SessionStartResultDict",
        {
            "success": True,
            "errors": [],
            "run": {"active_run": None, "status": "no_active_run"},
            "framework_reminder": "read the framework",
            "timestamp": "2026-09-04T00:00:00+00:00",
            "learnings": [],
            "sync_health": {
                "status": "not_measured",
                "reason": "sync_state_absent",
                "consecutive_failures": 0,
                "last_push_at": None,
                "advisory": "sync health not measured: sync_state_absent",
            },
            "pipeline_health": {
                "degraded": False,
                "advisory": "",
                "unmeasured": ["sync_push"],
                "sync_push": {
                    "degraded": False,
                    "measured": False,
                    "advisory": "sync_push not measured: RuntimeError",
                },
            },
            "degradations": [{"step": "sync_health", "error_class": "OSError", "message": "io", "severity": "warn"}],
            "degraded_steps": 1,
        },
    )

    trimmed = trim_session_start_payload(payload, verbose=False)

    # The sync block is a declared diagnostic and IS folded — but into a summary
    # that carries the not-measured state rather than dropping it silently.
    assert "not_measured" in str(trimmed["health_summary"])
    # The pipeline block is not a trimmed diagnostic; the flags survive whole.
    pipeline = cast("dict[str, object]", trimmed["pipeline_health"])
    assert pipeline["unmeasured"] == ["sync_push"]
    assert cast("dict[str, object]", pipeline["sync_push"])["measured"] is False
    assert "RuntimeError" in str(cast("dict[str, object]", pipeline["sync_push"])["advisory"])
    # Degradations are never trimmed — they are the enumeration of what failed.
    assert trimmed["degradations"] == payload["degradations"]
    assert trimmed["degraded_steps"] == 1


def test_pre_change_payload_keys_are_a_subset_of_the_post_change_ones() -> None:
    """PRD-CORE-263-NFR04 — the new information arrives as ADDED keys only."""
    pre_change = _make_results(3)
    post_change = cast("dict[str, object]", dict(cast("dict[str, object]", _make_results(3))))
    post_change["pipeline_health"] = {"degraded": False, "advisory": "", "sync_push": {"measured": True}}
    post_change["wal_checkpoint"] = {"checkpointed": True}

    before = trim_session_start_payload(pre_change, verbose=True)
    after = trim_session_start_payload(cast("SessionStartResultDict", post_change), verbose=True)

    assert set(cast("dict[str, object]", before)) <= set(cast("dict[str, object]", after))
    for key, value in cast("dict[str, object]", before).items():
        if key == "payload_token_estimate":
            continue
        assert cast("dict[str, object]", after)[key] == value, key


def test_a_pre_change_config_still_loads_with_unchanged_defaults() -> None:
    """PRD-CORE-263-NFR04 / FR09 — retiring a field is silent to a config that sets it.

    ``TRWConfig`` carries ``extra="ignore"``, so a project still setting the
    retired ``run_auto_close_age_days`` loads without error. That silence is the
    P12 shape the pattern document names and cannot be closed from inside the
    config model, which is why the changelog states the removal outright.
    """
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig(run_auto_close_age_days=14)  # type: ignore[call-arg]
    assert not hasattr(cfg, "run_auto_close_age_days")
    assert cfg.run_stale_ttl_hours == 48
    assert cfg.run_auto_close_enabled is True
    assert cfg.assertion_stale_threshold_days == 30
