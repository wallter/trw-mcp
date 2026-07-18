"""Unit tests for the typed fail-open degradation collector (mcp-x-failopen).

The ceremony hot path replaced its ad-hoc ``except Exception: logger.debug(...)``
swallows with ONE typed collector. These tests pin the two guarantees that make
that change safe:

1. A swallowed non-fatal failure is now OBSERVABLE — recorded as a typed
   ``Degradation`` in the payload (``degradations`` / ``degraded_steps``).
2. Recording a degradation NEVER flips ``success`` — the session/deliver still
   survives exactly the same set of failures it survived before.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._ceremony_degradations import DegradationCollector, record_into
from trw_mcp.tools._ceremony_session_start_steps import (
    finalize_session_start,
    step_recall_learnings,
    step_surface_stamp,
)
from trw_mcp.tools._ceremony_step_table import (
    SessionStartContext,
    Step,
    _ss_embed_health,
    run_steps,
)


class TestDegradationCollector:
    def test_record_appends_typed_entry_with_exception_metadata(self) -> None:
        c = DegradationCollector()
        c.record("recall", ValueError("boom"))
        assert len(c) == 1
        entry = c.items[0]
        assert entry["step"] == "recall"
        assert entry["error_class"] == "ValueError"
        assert entry["message"] == "boom"
        assert entry["severity"] == "warn"

    def test_record_default_severity_is_warn_and_info_is_explicit(self) -> None:
        c = DegradationCollector()
        c.record("probe", KeyError("k"), severity="info")
        assert c.items[0]["severity"] == "info"

    def test_into_writes_degradations_and_count(self) -> None:
        c = DegradationCollector()
        c.record("a", ValueError("x"))
        c.record("b", RuntimeError("y"))
        results: dict[str, object] = {}
        c.into(results)
        assert results["degraded_steps"] == 2
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert [d["step"] for d in degradations] == ["a", "b"]

    def test_into_is_noop_when_empty_keeps_payload_clean(self) -> None:
        """A clean session must not carry an empty ``degradations`` key."""
        c = DegradationCollector()
        results: dict[str, object] = {"success": True}
        c.into(results)
        assert "degradations" not in results
        assert "degraded_steps" not in results

    def test_into_merges_with_existing_degradations(self) -> None:
        results: dict[str, object] = {}
        record_into(results, "first", ValueError("1"))
        record_into(results, "second", ValueError("2"), severity="info")
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert [d["step"] for d in degradations] == ["first", "second"]
        assert degradations[1]["severity"] == "info"
        assert results["degraded_steps"] == 2

    def test_record_emits_structured_log_at_matching_level(self) -> None:
        c = DegradationCollector()
        with structlog.testing.capture_logs() as logs:
            c.record("pipeline_health", RuntimeError("down"), severity="warn")
            c.record("probe", KeyError("k"), severity="info")
        events = {(log["event"], log["log_level"]) for log in logs}
        assert ("pipeline_health_degraded", "warning") in events
        assert ("probe_degraded", "info") in events


class TestRunStepsRecordsDegradations:
    """The step-table driver turns a swallowed non-critical failure into a
    typed degradation WITHOUT flipping success."""

    def _make_ctx(self) -> SessionStartContext:
        results = cast("dict[str, object]", {})
        return SessionStartContext(
            query="",
            config=cast("object", None),  # unused by the boom step
            ctx=None,
            is_focused=False,
            results=cast("object", results),  # type: ignore[arg-type]
            errors=[],
        )

    def test_non_critical_step_failure_records_degradation_and_keeps_errors_empty(self) -> None:
        sctx = self._make_ctx()

        def _boom(_sctx: SessionStartContext) -> None:
            raise RuntimeError("step exploded")

        # A module namespace stand-in exposing the boom adapter by name, so the
        # driver's call-time getattr resolves it.
        class _Facade:
            _ss_boom = staticmethod(_boom)

        run_steps((Step("boomstep", "_ss_boom", critical=False),), sctx, cast("object", _Facade))

        # Observable: the swallow is now a typed degradation in the payload.
        results = cast("dict[str, object]", sctx.results)
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert len(degradations) == 1
        assert degradations[0]["step"] == "boomstep"
        assert degradations[0]["error_class"] == "RuntimeError"
        assert degradations[0]["message"] == "step exploded"
        assert results["degraded_steps"] == 1
        # Invariant: a non-critical failure does NOT append to errors, so it
        # cannot flip success (success = len(errors) == 0 downstream).
        assert sctx.errors == []

    def test_critical_step_failure_still_reraises(self) -> None:
        sctx = self._make_ctx()

        def _boom(_sctx: SessionStartContext) -> None:
            raise RuntimeError("critical exploded")

        class _Facade:
            _ss_boom = staticmethod(_boom)

        import pytest

        with pytest.raises(RuntimeError, match="critical exploded"):
            run_steps((Step("crit", "_ss_boom", critical=True),), sctx, cast("object", _Facade))

    def test_clean_run_leaves_no_degradations_key(self) -> None:
        sctx = self._make_ctx()

        def _ok(_sctx: SessionStartContext) -> None:
            return None

        class _Facade:
            _ss_ok = staticmethod(_ok)

        run_steps((Step("ok", "_ss_ok"),), sctx, cast("object", _Facade))
        results = cast("dict[str, object]", sctx.results)
        assert "degradations" not in results
        assert "degraded_steps" not in results

    def test_embed_health_failure_is_degraded_and_later_steps_continue(self) -> None:
        sctx = self._make_ctx()
        sctx.config = TRWConfig()

        def _after(_sctx: SessionStartContext) -> None:
            cast("dict[str, object]", _sctx.results)["later_step_ran"] = True

        class _Facade:
            _ss_embed_health = staticmethod(_ss_embed_health)
            _ss_after = staticmethod(_after)

        with patch(
            "trw_mcp.state.memory_adapter.check_embeddings_status",
            side_effect=RuntimeError("health failed"),
        ):
            run_steps(
                (Step("embed_health", "_ss_embed_health"), Step("after", "_ss_after")),
                sctx,
                cast("object", _Facade),
            )

        results = cast("dict[str, object]", sctx.results)
        results["success"] = not sctx.errors
        assert results["success"] is True
        assert results["later_step_ran"] is True
        assert "embed_health" not in results
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert [item["step"] for item in degradations] == ["embed_health"]


class TestStepFunctionsThreadCollector:
    def test_surface_stamp_records_degradation_on_failure_and_fails_open(self) -> None:
        c = DegradationCollector()
        # Force the interior import/stamp path to explode.
        with patch(
            "trw_mcp.telemetry.artifact_registry.resolve_surface_registry",
            side_effect=RuntimeError("stamp boom"),
        ):
            out = step_surface_stamp(None, "sess-1", c)
        # Fail-open contract preserved: returns "".
        assert out == ""
        # Now observable via the threaded collector.
        assert len(c) == 1
        assert c.items[0]["step"] == "surface_stamp"
        assert c.items[0]["error_class"] == "RuntimeError"

    def test_surface_stamp_without_collector_still_fails_open(self) -> None:
        with patch(
            "trw_mcp.telemetry.artifact_registry.resolve_surface_registry",
            side_effect=RuntimeError("stamp boom"),
        ):
            assert step_surface_stamp(None, "sess-1") == ""

    def test_recall_failure_surfaces_degradation_and_warning_without_error(self) -> None:
        results = cast("dict[str, object]", {})
        errors: list[str] = []
        with patch(
            "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
            side_effect=RuntimeError("recall boom"),
        ):
            step_recall_learnings(
                "",
                cast("object", None),  # type: ignore[arg-type]
                cast("object", results),  # type: ignore[arg-type]
                errors,
            )
        # Recall stays fail-open: no error appended (would flip success).
        assert errors == []
        # Both the legacy warnings list AND the typed degradation are populated.
        warnings = cast("list[str]", results["warnings"])
        assert any("recall boom" in w for w in warnings)
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert degradations[0]["step"] == "recall"
        assert results["learnings"] == []


class TestFinalizePreservesSuccessContract:
    def test_finalize_success_true_when_no_errors_even_with_degradations(self) -> None:
        """A degradation recorded during finalize must not flip success."""
        results = cast("dict[str, object]", {})
        config = _make_light_config()
        # Force step_mark_session_started to raise so finalize records a
        # degradation on the fail-open path.
        with patch(
            "trw_mcp.tools._ceremony_helpers.step_mark_session_started",
            side_effect=RuntimeError("mark boom"),
        ):
            finalize_session_start(cast("object", results), config, {}, [])  # type: ignore[arg-type]
        assert results["success"] is True
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert any(d["step"] == "mark_session_started" for d in degradations)

    def test_finalize_success_false_only_when_errors_present(self) -> None:
        results = cast("dict[str, object]", {})
        config = _make_light_config()
        finalize_session_start(cast("object", results), config, {}, ["status: boom"])  # type: ignore[arg-type]
        assert results["success"] is False
        assert results["errors"] == ["status: boom"]


def _make_light_config() -> object:
    """Minimal config stub exposing the two attributes finalize reads."""

    class _Cfg:
        effective_ceremony_mode = "light"

    return _Cfg()
