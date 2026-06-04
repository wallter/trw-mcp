"""Tests for PRD-CORE-184 FR01/FR02 — TaskType taxonomy + heuristic detector.

The detector MUST be heuristic-only (no LLM call) to avoid the iter-6
classification-as-priming harm (-24/-26pp on coding). These tests assert the
priority-ordered signal resolution and the absence of any LLM dependency.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest

from trw_mcp.models.task_profile_types import TaskType
from trw_mcp.tools._task_type_detection import (
    DetectionResult,
    detect_task_type,
)

# ── FR01: taxonomy ──────────────────────────────────────────────────────────


def test_task_type_taxonomy_values() -> None:
    """FR01: all seven canonical task types are present in the Literal."""
    values = set(get_args(TaskType))
    assert values == {
        "coding",
        "research",
        "docs",
        "eval",
        "rca",
        "planning",
        "unknown",
    }


# ── FR02: explicit override wins ────────────────────────────────────────────


def test_detect_task_type_explicit_override() -> None:
    """Explicit task_type beats every other signal."""
    result = detect_task_type(
        task_name="implement user authentication",
        run_type="implementation",
        task_type="rca",
    )
    assert result.task_type == "rca"
    assert result.detection_method == "explicit_override"


def test_detect_task_type_explicit_override_invalid_falls_through() -> None:
    """An explicit task_type that is not a valid TaskType is ignored."""
    result = detect_task_type(
        task_name="research competitive landscape",
        run_type="research",
        task_type="not-a-real-type",
    )
    # falls through to run_type mapping
    assert result.task_type == "research"
    assert result.detection_method != "explicit_override"


# ── FR02: run_type mapping ──────────────────────────────────────────────────


def test_detect_task_type_run_type_implementation() -> None:
    result = detect_task_type(task_name="", run_type="implementation")
    assert result.task_type == "coding"
    assert result.detection_method == "run_type"


def test_detect_task_type_run_type_research() -> None:
    result = detect_task_type(task_name="", run_type="research")
    assert result.task_type == "research"
    assert result.detection_method == "run_type"


def test_detect_task_type_run_type_wins_over_empty_description() -> None:
    """FR02 acceptance: empty description + implementation run_type -> coding."""
    result = detect_task_type(task_name="", run_type="implementation")
    assert result.task_type == "coding"


# ── FR02: description keyword scan ──────────────────────────────────────────


def test_detect_task_type_debug_keyword() -> None:
    """rca keywords win over the implementation run_type mapping."""
    result = detect_task_type(
        task_name="debug memory leak stacktrace",
        run_type="implementation",
    )
    assert result.task_type == "rca"
    assert result.detection_method == "keyword"


def test_detect_task_type_docs_keyword() -> None:
    result = detect_task_type(task_name="write the README documentation", run_type="unknown_type")
    assert result.task_type == "docs"


def test_detect_task_type_eval_keyword() -> None:
    result = detect_task_type(task_name="benchmark the new scorer campaign", run_type="unknown_type")
    assert result.task_type == "eval"


def test_detect_task_type_planning_keyword() -> None:
    result = detect_task_type(task_name="groom the sprint roadmap backlog", run_type="unknown_type")
    assert result.task_type == "planning"


def test_detect_task_type_coding_keyword() -> None:
    result = detect_task_type(task_name="refactor the migrate helper", run_type="unknown_type")
    assert result.task_type == "coding"


def test_detect_task_type_research_keyword() -> None:
    result = detect_task_type(task_name="survey the competitive analysis", run_type="unknown_type")
    assert result.task_type == "research"


def test_keyword_scan_is_case_insensitive() -> None:
    result = detect_task_type(task_name="DEBUG the STACKTRACE", run_type="unknown_type")
    assert result.task_type == "rca"


# ── FR02: prd_scope signal ──────────────────────────────────────────────────


def test_prd_scope_leans_coding_for_feature_scope() -> None:
    result = detect_task_type(
        task_name="",
        run_type="unknown_type",
        prd_scope=["PRD-CORE-184: implement feature X"],
    )
    assert result.task_type == "coding"
    assert result.detection_method in {"keyword", "prd_scope"}


# ── FR02: fallback ──────────────────────────────────────────────────────────


def test_detect_task_type_no_signals() -> None:
    result = detect_task_type(task_name="", run_type="unknown_type")
    assert result.task_type == "unknown"
    assert result.detection_method == "fallback"


def test_detect_task_type_empty_inputs_no_exception() -> None:
    """NFR02: detection must never raise; empty inputs -> unknown."""
    result = detect_task_type(task_name="", run_type="")
    assert result.task_type == "unknown"


def test_detect_task_type_none_inputs_no_exception() -> None:
    result = detect_task_type(task_name="", run_type="implementation", prd_scope=None, task_type=None)
    assert result.task_type == "coding"


# ── FR02 / NFR04: no LLM dependency ─────────────────────────────────────────


def test_detect_task_type_no_llm_calls() -> None:
    """NFR04: the detector source must not reference any LLM / provider symbol."""
    import trw_mcp.tools._task_type_detection as detection_mod

    source = inspect.getsource(detection_mod).lower()
    for forbidden in ("import anthropic", "import openai", "llm(", "completion(", "ollama", "vllm"):
        assert forbidden not in source, f"detector references forbidden LLM symbol: {forbidden}"


def test_detection_result_carries_rationale() -> None:
    """FR05: detection result exposes a rationale string for the event log."""
    result = detect_task_type(task_name="debug stacktrace", run_type="implementation")
    assert isinstance(result, DetectionResult)
    assert result.rationale
    assert isinstance(result.rationale, str)


@pytest.mark.parametrize(
    ("task_name", "run_type", "expected"),
    [
        ("implement user authentication", "implementation", "coding"),
        ("research competitive landscape", "research", "research"),
        ("debug memory leak stacktrace", "implementation", "rca"),
        ("", "implementation", "coding"),
        ("", "unknown_type", "unknown"),
    ],
)
def test_detect_task_type_acceptance_matrix(task_name: str, run_type: str, expected: TaskType) -> None:
    """FR02 acceptance examples from the PRD."""
    assert detect_task_type(task_name=task_name, run_type=run_type).task_type == expected
