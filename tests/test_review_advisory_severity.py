"""XC-01: the advisory review-finding severity screen is logged only and never changes a verdict."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog.testing

from ._review_helpers_support import run_dir  # noqa: F401

_SECRET_TEXT = "the token parser drops the last byte"


def _kit(labels: dict[str, str], calls: list[dict[str, Any]]) -> Any:
    """A toolkit whose batch_items answers each item with the given choice, via trw-memory's real result types."""
    from trw_memory.decisions._models import ChoiceAnswer
    from trw_memory.decisions._results import AskResult

    def batch_items(items: dict[str, Any], questions: dict[str, Any], **_k: Any) -> Any:
        calls.append({"items": items, "questions": questions})
        per_item = {
            key: AskResult(
                outcomes={"severity": ChoiceAnswer(choice=labels[key], probabilities={labels[key]: 0.8, "info": 0.2})}
            )
            for key in items
        }
        return SimpleNamespace(per_item=per_item)

    return SimpleNamespace(batch_items=batch_items)


def _enable(monkeypatch: pytest.MonkeyPatch, kit: Any, built: list[int]) -> None:
    def fake_toolkit_from_env(*_a: Any, **_k: Any) -> Any:
        built.append(1)
        return kit

    monkeypatch.setattr("trw_mcp.tools._assess_enablement.assess_surfaced", lambda *_a, **_k: True)
    monkeypatch.setattr("trw_memory.decisions.toolkit_from_env", fake_toolkit_from_env)


def test_disabled_judge_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._review_advisory_severity import log_advisory_severity

    built: list[int] = []
    monkeypatch.setattr("trw_mcp.tools._assess_enablement.assess_surfaced", lambda *_a, **_k: False)
    monkeypatch.setattr("trw_memory.decisions.toolkit_from_env", lambda *_a, **_k: built.append(1))

    assert log_advisory_severity([{"severity": "critical", "description": "x"}]) is None
    assert log_advisory_severity([]) is None
    assert built == []


def test_enabled_screen_logs_labels_and_disagreements_but_no_finding_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._review_advisory_severity import log_advisory_severity

    calls: list[dict[str, Any]] = []
    built: list[int] = []
    _enable(monkeypatch, _kit({"0": "critical", "1": "info"}, calls), built)
    findings = [
        {"severity": "critical", "category": "correctness", "description": _SECRET_TEXT},
        {"severity": "warning", "category": "style", "description": "naming"},
    ]

    with structlog.testing.capture_logs() as logs:
        log_advisory_severity(findings, background=False)

    [event] = [e for e in logs if e["event"] == "review_finding_severity_advisory"]
    assert (event["findings"], event["answered"], event["disagreements"]) == (2, 2, 1)
    assert event["rows"]["0"]["reported"] == "critical" and event["rows"]["0"]["advisory"] == "critical"
    assert event["rows"]["1"]["reported"] == "warning" and event["rows"]["1"]["advisory"] == "info"
    assert _SECRET_TEXT not in repr(event)  # labels and numbers only; the text went to the (redacting) judge
    assert calls[0]["items"]["0"]["description"] == _SECRET_TEXT
    assert set(calls[0]["questions"]["severity"]["criteria"]) == {"critical", "warning", "info"}


def test_a_judge_failure_is_logged_and_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._review_advisory_severity import log_advisory_severity

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("judge unreachable")

    monkeypatch.setattr("trw_mcp.tools._assess_enablement.assess_surfaced", lambda *_a, **_k: True)
    monkeypatch.setattr("trw_memory.decisions.toolkit_from_env", boom)

    with structlog.testing.capture_logs() as logs:
        log_advisory_severity([{"severity": "info", "description": "x"}], background=False)

    assert [e["error_class"] for e in logs if e["event"] == "review_finding_severity_advisory_failed"] == [
        "RuntimeError"
    ]


def test_manual_review_verdict_ignores_a_disagreeing_advisory(monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    """Wiring: manual mode runs the screen, and a judge calling a critical finding 'info' leaves the verdict at block."""
    import trw_mcp.tools._review_advisory_severity as advisory
    from trw_mcp.tools._review_helpers import handle_manual_mode

    calls: list[dict[str, Any]] = []
    built: list[int] = []
    _enable(monkeypatch, _kit({"0": "info"}, calls), built)

    class _Inline(threading.Thread):
        def start(self) -> None:  # run the daemon thread's work inline so the test can read its log
            self.run()

    monkeypatch.setattr(advisory.threading, "Thread", _Inline)
    findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]

    with structlog.testing.capture_logs() as logs:
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")

    assert result["verdict"] == "block"
    assert "advisory" not in repr(result)
    [event] = [e for e in logs if e["event"] == "review_finding_severity_advisory"]
    assert event["disagreements"] == 1 and built == [1]


def test_a_startup_failure_never_aborts_the_review(monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    """Codex r1: an enablement check or thread start that raises is logged; the review still returns its verdict."""
    from trw_mcp.tools._review_helpers import handle_manual_mode

    def broken(*_a: Any, **_k: Any) -> bool:
        raise OSError("config unreadable")

    monkeypatch.setattr("trw_mcp.tools._assess_enablement.assess_surfaced", broken)
    findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]

    with structlog.testing.capture_logs() as logs:
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")

    assert result["verdict"] == "block"
    assert [e["error_class"] for e in logs if e["event"] == "review_finding_severity_advisory_failed"] == ["OSError"]
