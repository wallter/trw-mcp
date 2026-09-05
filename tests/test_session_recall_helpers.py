"""Session-start recall degraded-mode regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.exceptions import CanaryTamperError

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._ceremony_degradations import SessionStartStepError
from trw_mcp.tools._ceremony_session_start_steps import step_recall_learnings
from trw_mcp.tools._recall_impl import execute_recall
from trw_mcp.tools._session_recall_helpers import perform_session_recalls


def test_perform_session_recalls_propagates_canary_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD-CORE-263-FR01 / DEF-01 attribution.

    ``recall`` is declared ``critical`` in the session-start step table, so a
    canary-tamper failure here must reach the caller like any other exception
    instead of being swallowed into a degraded-but-empty envelope (the pre-fix
    shape this test used to assert, which made the critical branch unreachable
    for the one recall failure most worth stopping on). Reverting the DEF-01
    fix (restoring the ``except Exception`` canary-tamper carve-out) turns this
    red because ``perform_session_recalls`` would return normally instead of
    raising.
    """

    def _raise_tamper(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise CanaryTamperError("recall halted after canary tamper")

    monkeypatch.setattr(
        "trw_mcp.state.recall_factories.recall_baseline_high_impact",
        _raise_tamper,
    )

    with pytest.raises(CanaryTamperError):
        perform_session_recalls(
            tmp_path,
            "*",
            TRWConfig(),
            FileStateReader(),
        )


def test_step_recall_learnings_canary_tamper_raises_typed_step_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD-CORE-263-FR01 / DEF-01 attribution (end of the chain).

    A canary-tamper failure surfaced from ``perform_session_recalls`` must
    raise :class:`SessionStartStepError` from ``step_recall_learnings`` — the
    SAME path a generic ``RuntimeError`` already takes — so the runner's
    critical branch marks ``success: False`` with a typed reason naming
    ``CanaryTamperError`` rather than reading a green ``recall_degraded`` block
    with ``errors == []``.
    """
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony.resolve_trw_dir",
        lambda: tmp_path,
        raising=False,
    )

    def _raise_tamper(*_args: object, **_kwargs: object) -> tuple[list[object], list[object], dict[str, object]]:
        raise CanaryTamperError("recall halted after canary tamper")

    monkeypatch.setattr(
        "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
        _raise_tamper,
    )

    results: dict[str, object] = {}
    errors: list[str] = []
    with pytest.raises(SessionStartStepError) as exc_info:
        step_recall_learnings("*", TRWConfig(), results, errors)

    assert exc_info.value.step == "recall"
    assert isinstance(exc_info.value.cause, CanaryTamperError)
    # NFR04: the keys an existing consumer reads are still seeded before the raise.
    assert results["learnings"] == []
    assert results["learnings_count"] == 0
    assert "recall_degraded" not in results


def test_step_recall_learnings_exception_raises_typed_step_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recall failure raises a typed step error for the runner to classify.

    PRD-CORE-263-FR01: ``recall`` is declared ``critical`` in the session-start
    step table. The step no longer decides whether its own failure is fatal —
    that decision belongs solely to the runner (``run_steps`` in
    ``_ceremony_step_table.py``), which holds the ``critical`` flag. So the step
    body raises ``SessionStartStepError`` instead of swallowing the exception
    into ``results['warnings']`` with ``errors`` left empty (the pre-fix shape
    this test used to assert, which made the critical branch unreachable).
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
    with pytest.raises(SessionStartStepError) as exc_info:
        step_recall_learnings("*", TRWConfig(), results, errors)

    assert exc_info.value.step == "recall"
    assert "recall backend unavailable" in str(exc_info.value.cause)
    # NFR04: the keys an existing consumer reads are still seeded before the
    # raise, even though the step no longer decides fatality itself.
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
