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

import pytest
import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._ceremony_degradations import DegradationCollector, SessionStartStepError, record_into
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

    def test_critical_step_failure_degrades_the_payload_and_never_escapes(self) -> None:
        """PRD-CORE-263-FR01 / DR-001 — was ``..._still_reraises``.

        The old assertion encoded the pre-263 contract: a critical step re-raised
        out of ``trw_session_start``. That branch was ALSO unreachable, because
        all five critical step bodies swallowed first. Both halves are fixed
        here: the failure reaches the runner, and the runner degrades the payload
        rather than taking down the mandated first tool call.
        """
        sctx = self._make_ctx()

        def _boom(_sctx: SessionStartContext) -> None:
            raise RuntimeError("critical exploded")

        class _Facade:
            _ss_boom = staticmethod(_boom)

        run_steps((Step("crit", "_ss_boom", critical=True),), sctx, cast("object", _Facade))

        # The verdict is false (finalize computes success = len(errors) == 0)
        # and the reason names the step.
        assert len(sctx.errors) == 1
        assert "crit" in sctx.errors[0]
        assert "RuntimeError" in sctx.errors[0]
        results = cast("dict[str, object]", sctx.results)
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert degradations[0]["step"] == "crit"
        assert degradations[0]["error_class"] == "RuntimeError"

    def test_critical_step_error_reports_the_wrapped_cause_class(self) -> None:
        """PRD-CORE-263-FR01 — the wrapper must not eat the real error class."""
        sctx = self._make_ctx()

        def _boom(_sctx: SessionStartContext) -> None:
            raise SessionStartStepError("crit", ValueError("inner"))

        class _Facade:
            _ss_boom = staticmethod(_boom)

        run_steps((Step("crit", "_ss_boom", critical=True),), sctx, cast("object", _Facade))
        results = cast("dict[str, object]", sctx.results)
        degradations = cast("list[dict[str, object]]", results["degradations"])
        assert degradations[0]["error_class"] == "ValueError"
        assert "ValueError" in sctx.errors[0]

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
    def test_surface_stamp_raises_a_typed_step_error(self) -> None:
        """PRD-CORE-263-FR01 — was ``..._on_failure_and_fails_open``.

        ``surface_stamp`` is declared critical. Returning ``""`` made that flag
        unreachable AND made "the stamp failed" indistinguishable from "there was
        no run to stamp", which also returns ``""``.
        """
        c = DegradationCollector()
        with (
            patch(
                "trw_mcp.telemetry.artifact_registry.resolve_surface_registry",
                side_effect=RuntimeError("stamp boom"),
            ),
            pytest.raises(SessionStartStepError) as excinfo,
        ):
            step_surface_stamp(None, "sess-1", c)
        assert excinfo.value.step == "surface_stamp"
        assert isinstance(excinfo.value.cause, RuntimeError)

    def test_surface_stamp_without_collector_also_raises(self) -> None:
        """PRD-CORE-263-FR01 — the collector never governed the verdict."""
        with (
            patch(
                "trw_mcp.telemetry.artifact_registry.resolve_surface_registry",
                side_effect=RuntimeError("stamp boom"),
            ),
            pytest.raises(SessionStartStepError),
        ):
            step_surface_stamp(None, "sess-1")

    def test_recall_failure_raises_after_seeding_the_payload_keys(self) -> None:
        """PRD-CORE-263-FR01 — was ``..._degradation_and_warning_without_error``.

        The old assertion is the defect in test form: it pinned a recall failure
        to ``errors == []``, i.e. ``success: true`` on a session whose recall
        returned nothing. ``recall`` is declared critical, so the failure now
        reaches the runner. The NFR04 keys an existing consumer reads
        (``learnings`` / ``learnings_count``) are still seeded before the raise.
        """
        results = cast("dict[str, object]", {})
        errors: list[str] = []
        with (
            patch(
                "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
                side_effect=RuntimeError("recall boom"),
            ),
            pytest.raises(SessionStartStepError) as excinfo,
        ):
            step_recall_learnings(
                "",
                cast("object", None),  # type: ignore[arg-type]
                cast("object", results),  # type: ignore[arg-type]
                errors,
            )
        assert excinfo.value.step == "recall"
        assert results["learnings"] == []
        assert results["learnings_count"] == 0
        # The step itself still records nothing: one failure, one entry, and the
        # runner owns it (NFR03).
        assert errors == []
        assert "degradations" not in results


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


# ---------------------------------------------------------------------------
# PRD-CORE-263-NFR02 — no new fail-open wrapper without a degradation entry
# ---------------------------------------------------------------------------

#: The files PRD-CORE-263 owns and converted. The audit below is TOTAL over the
#: BROAD handlers in these files.
#:
#: ``state/_memory_recall.py`` is owned by FR07 but deliberately NOT listed: it
#: carries pre-existing handlers outside this PRD's behaviour slice, and a
#: totality claim over them would be a scope expansion wearing a test's clothes.
#: Its one FR07 handler is asserted directly in
#: ``tests/test_memory_connection_recovery.py``.
_NFR02_OWNED_FILES = (
    "tools/_ceremony_step_table.py",
    "tools/_ceremony_session_start_steps.py",
    "tools/_ceremony_profile_step.py",
    "tools/_sync_health.py",
    "tools/_pipeline_health.py",
    "tools/_ceremony_degradations.py",
)

#: The marker the repository's fail-silent ratchet honours, reason mandatory.
_ALLOW_MARKER = "trw-fail-silent-allow:"

#: Names that record a degradation through the collector.
_RECORDERS = frozenset({"record", "record_into", "_record_or_debug"})

#: Names that build an explicitly not-measured result.
_NOT_MEASURED = frozenset({"_unmeasured", "_not_measured"})


def _handler_disposition(handler: object, source_lines: list[str]) -> str | None:
    """Return the disposition of one ``except`` handler, or ``None`` for a swallow."""
    import ast

    assert isinstance(handler, ast.ExceptHandler)
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return "reraise"
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in _RECORDERS:
                return "degradation"
            if name in _NOT_MEASURED:
                return "not_measured"
    # The marker may sit on the ``except`` line or the line above it.
    window = source_lines[max(0, handler.lineno - 2) : handler.lineno]
    for line in window:
        if _ALLOW_MARKER in line and line.split(_ALLOW_MARKER, 1)[1].strip():
            return "allow_marker"
    return None


def _is_broad(handler: object) -> bool:
    import ast

    assert isinstance(handler, ast.ExceptHandler)
    if handler.type is None:
        return True
    names = {node.id for node in ast.walk(handler.type) if isinstance(node, ast.Name)}
    return bool(names & {"Exception", "BaseException"})


def test_no_owned_handler_swallows_without_a_degradation_entry() -> None:
    """PRD-CORE-263-NFR02 — a broad handler must do one of four things.

    Re-raise (FR01's shape), record a degradation through the collector, return
    an explicitly not-measured value (FR02/FR03's shape — the PRD prescribes it,
    so a three-way rule that excluded it would contradict its own FRs), or carry
    the repository's fail-silent allow marker WITH a non-empty reason.

    Narrow typed handlers are out of scope by construction: this requirement is
    about the catch-everything shape, and a ``except ValueError`` that sets a
    variable the next line degrades on is not that shape.

    Attribution: restoring any converted swallow (for example
    ``_bandit_probe_config``'s ``return True, _BANDIT_STALE_DAYS``) turns this
    red naming the file and line.
    """
    import ast
    from pathlib import Path

    src_root = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    swallows: list[str] = []
    broad_seen = 0
    for rel in _NFR02_OWNED_FILES:
        path = src_root / rel
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.ExceptHandler) or not _is_broad(node):
                continue
            broad_seen += 1
            if _handler_disposition(node, lines) is None:
                swallows.append(f"{rel}:{node.lineno}")

    assert not swallows, (
        "broad handlers that swallow without re-raising, recording a degradation, "
        f"returning a not-measured value, or carrying an allow marker: {swallows}"
    )
    # Non-vacuity: the walk must actually find handlers to classify.
    assert broad_seen >= 10, f"expected the owned set to carry broad handlers, found {broad_seen}"


def test_the_handler_audit_fires_on_a_planted_swallow(tmp_path: object) -> None:
    """PRD-CORE-263-NFR02 — the audit predicate is not vacuous."""
    import ast

    planted = "try:\n    pass\nexcept Exception:\n    result = None\n"
    handler = next(n for n in ast.walk(ast.parse(planted)) if isinstance(n, ast.ExceptHandler))
    assert _is_broad(handler)
    assert _handler_disposition(handler, planted.splitlines()) is None

    marked = "try:\n    pass\nexcept Exception:  # trw-fail-silent-allow: genuinely open class\n    result = None\n"
    handler = next(n for n in ast.walk(ast.parse(marked)) if isinstance(n, ast.ExceptHandler))
    assert _handler_disposition(handler, marked.splitlines()) == "allow_marker"

    empty_reason = "try:\n    pass\nexcept Exception:  # trw-fail-silent-allow:\n    result = None\n"
    handler = next(n for n in ast.walk(ast.parse(empty_reason)) if isinstance(n, ast.ExceptHandler))
    assert _handler_disposition(handler, empty_reason.splitlines()) is None, (
        "a marker with an empty reason is malformed, not honoured"
    )
