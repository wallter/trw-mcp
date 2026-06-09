"""Session-start recall degraded-mode regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.exceptions import CanaryTamperError

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._ceremony_session_start_steps import step_recall_learnings
from trw_mcp.tools._recall_impl import execute_recall
from trw_mcp.tools._session_recall_helpers import perform_session_recalls


def test_perform_session_recalls_degrades_on_canary_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session-start recall is optional and returns a degraded envelope on tamper."""

    def _raise_tamper(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise CanaryTamperError("recall halted after canary tamper")

    monkeypatch.setattr(
        "trw_mcp.state.recall_factories.recall_baseline_high_impact",
        _raise_tamper,
    )

    learnings, auto_recalled, extra = perform_session_recalls(
        tmp_path,
        "*",
        TRWConfig(),
        FileStateReader(),
    )

    assert learnings == []
    assert auto_recalled == []
    assert extra["total_available"] == 0
    assert extra["recall_degraded"]["reason"] == "canary_tamper"


def test_step_recall_learnings_surfaces_degraded_metadata_without_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trw_session_start should remain successful when only optional recall degrades."""
    degraded = {
        "reason": "canary_tamper",
        "detail": "Session-start learning recall was skipped.",
        "exception_type": "CanaryTamperError",
    }

    monkeypatch.setattr(
        "trw_mcp.tools.ceremony.resolve_trw_dir",
        lambda: tmp_path,
        raising=False,
    )
    monkeypatch.setattr(
        "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
        lambda *_args, **_kwargs: ([], [], {"recall_degraded": degraded, "total_available": 0}),
    )

    results: dict[str, object] = {}
    errors: list[str] = []
    step_recall_learnings("*", TRWConfig(), results, errors)

    assert errors == []
    assert results["learnings"] == []
    assert results["learnings_count"] == 0
    assert results["total_available"] == 0
    assert results["recall_degraded"] == degraded


def test_step_recall_learnings_exception_goes_to_warnings_not_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recall failure must NOT flip session_start success.

    Recall is fail-open by contract. When ``perform_session_recalls`` raises,
    the failure is recorded under ``results['warnings']`` (for visibility) and
    NOT appended to ``errors`` (which would set ``success=False`` and mislead
    agents into retrying an otherwise-successful session_start).
    """
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony.resolve_trw_dir",
        lambda: tmp_path,
        raising=False,
    )

    def _boom(*_args: object, **_kwargs: object) -> tuple[list[object], list[object], dict[str, object]]:
        raise RuntimeError("recall backend unavailable")

    monkeypatch.setattr(
        "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
        _boom,
    )

    results: dict[str, object] = {}
    errors: list[str] = []
    step_recall_learnings("*", TRWConfig(), results, errors)

    # errors stays empty -> finalize_session_start computes success=True.
    assert errors == [], f"recall failure must not populate errors, got {errors}"
    warnings = results.get("warnings")
    assert isinstance(warnings, list) and len(warnings) == 1
    assert "recall: recall backend unavailable" in warnings[0]
    assert results["learnings"] == []
    assert results["learnings_count"] == 0


def test_finalize_success_true_when_only_recall_warned(tmp_path: Path) -> None:
    """End-to-end: a recall-only warning leaves finalize success=True."""
    from trw_mcp.tools._ceremony_session_start_steps import finalize_session_start

    results: dict[str, object] = {"warnings": ["recall: boom"], "learnings_count": 0}
    errors: list[str] = []
    finalize_session_start(results, TRWConfig(), {}, errors)  # type: ignore[arg-type]

    assert results["success"] is True
    assert results["warnings"] == ["recall: boom"]


def test_direct_recall_propagates_canary_tamper(
    tmp_path: Path,
) -> None:
    """Direct trw_recall remains fail-closed on canary tamper."""

    def _raise_tamper(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise CanaryTamperError("recall halted after canary tamper")

    with pytest.raises(CanaryTamperError):
        execute_recall(
            "anything",
            tmp_path,
            TRWConfig(),
            _adapter_recall=_raise_tamper,
            _adapter_update_access=lambda *_args, **_kwargs: None,
            _search_patterns=lambda *_args, **_kwargs: [],
            _rank_by_utility=lambda entries, *_args, **_kwargs: entries,
            _collect_context=lambda *_args, **_kwargs: {},
        )
