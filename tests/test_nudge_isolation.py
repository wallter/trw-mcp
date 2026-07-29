from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import trw_mcp.state.ceremony_progress as ceremony_progress
from trw_mcp.state._ceremony_progress_state import read_ceremony_state
from trw_mcp.tools import (
    _ceremony_helpers,
    _learn_impl,
    _orchestration_lifecycle,
    _recall_impl,
    _session_recall_helpers,
    ceremony,
    orchestration,
    requirements,
    review,
)
from trw_mcp.tools._ceremony_status import append_ceremony_status


def test_live_tool_modules_do_not_depend_on_legacy_nudge_wiring() -> None:
    modules = (
        ceremony,
        orchestration,
        requirements,
        review,
        _ceremony_helpers,
        _learn_impl,
        _orchestration_lifecycle,
        _recall_impl,
        _session_recall_helpers,
    )

    for module in modules:
        source = inspect.getsource(module)
        assert "state.ceremony_nudge" not in source
        assert "append_ceremony_nudge" not in source
        assert "NudgeContext" not in source


def test_live_ceremony_progress_is_isolated_from_legacy_nudge_backend() -> None:
    progress_source = inspect.getsource(ceremony_progress)
    assert "_nudge_state" not in progress_source
    assert "_ceremony_progress_state" in progress_source


def test_append_ceremony_status_adds_summary_when_state_exists(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)

    result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert result["status"] == "ok"
    assert isinstance(result["ceremony_status"], str)
    assert "checkpoints=0" in result["ceremony_status"]
    assert "learnings=0" in result["ceremony_status"]


def test_nudge_selection_cache_based(tmp_path: Path) -> None:
    from trw_mcp.sync.cache import IntelligenceCache

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    IntelligenceCache(trw_dir).update({"bandit_params": {"L-2": 1.9}})

    learnings = [
        {"id": "L-1", "summary": "Document the health check", "impact": 0.9},
        {
            "id": "L-2",
            "summary": "Retry failed queue workers",
            "nudge_line": "Retry the failed queue workers before closing the run.",
            "impact": 0.7,
            "domain": ["backend"],
            "phase_affinity": ["implement"],
        },
    ]
    recall_context = type(
        "RecallContext",
        (),
        {"inferred_domains": {"backend"}, "current_phase": "implement", "modified_files": []},
    )()

    with (
        patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=learnings),
        patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context),
    ):
        result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert result["nudge_content"] == "Retry the failed queue workers before closing the run."


def test_retired_learning_injection_symbols_are_absent() -> None:
    """PRD-CORE-241-FR08: neither retired entry point is importable any more."""
    import trw_mcp.state._ceremony_nudge_selectors as selectors
    import trw_mcp.state.ceremony_nudge as facade

    assert not hasattr(selectors, "select_learning_injection_content")
    assert not hasattr(facade, "select_learning_injection_content")
    assert not hasattr(facade, "compute_nudge_learning_injection")
    # The shared candidate selector the contextual arms depend on stays.
    assert hasattr(selectors, "_select_learning_injection_candidate")
    assert hasattr(facade, "select_contextual_nudge_content")


def test_contextual_messenger_live_branch(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\nnudge_messenger: contextual\n", encoding="utf-8")

    with patch(
        "trw_mcp.state.ceremony_nudge.select_contextual_nudge_content",
        return_value=("Contextual next-step nudge", None, "foo.py"),
    ):
        result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert result["nudge_content"] == "Contextual next-step nudge"


def test_contextual_messenger_records_impression_when_learning_is_shown(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\nnudge_messenger: contextual\n", encoding="utf-8")

    with patch(
        "trw_mcp.state.ceremony_nudge.select_contextual_nudge_content",
        return_value=("Contextual next-step nudge", "L-test123", "foo.py"),
    ):
        append_ceremony_status({"status": "ok"}, trw_dir)

    events = (trw_dir / "context" / "session-events.jsonl").read_text(encoding="utf-8")
    assert events.count('"event":"nudge_shown"') == 1


def test_contextual_action_messenger_live_branch(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\nnudge_messenger: contextual_action\n", encoding="utf-8")

    with patch(
        "trw_mcp.state.ceremony_nudge.select_contextual_nudge_content",
        return_value=("Contextual action-only nudge", None, "foo.py"),
    ):
        result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert result["nudge_content"] == "Contextual action-only nudge"


def test_minimal_messenger_records_synthetic_impression_and_counts(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\nnudge_messenger: minimal\n", encoding="utf-8")

    result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert isinstance(result.get("nudge_content"), str)
    state = read_ceremony_state(trw_dir)
    assert state.nudge_counts.get("session_start") == 1
    assert len(state.nudge_history) == 1
    entry = next(iter(state.nudge_history.values()))
    assert entry["turn_first_shown"] >= 1
    assert entry["last_shown_turn"] >= entry["turn_first_shown"]
    events = (trw_dir / "context" / "session-events.jsonl").read_text(encoding="utf-8")
    assert events.count('"event":"nudge_shown"') == 1


def test_contextual_action_messenger_records_synthetic_impression_and_counts(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\nnudge_messenger: contextual_action\n", encoding="utf-8")

    with patch(
        "trw_mcp.state.ceremony_nudge.select_contextual_nudge_content",
        return_value=("Contextual action-only nudge", None, "foo.py"),
    ):
        append_ceremony_status({"status": "ok"}, trw_dir)

    state = read_ceremony_state(trw_dir)
    # Ledger UF-023: a reactive nudge with neither a build failure nor a P0
    # finding names no ceremony step, so it is counted as an EMISSION but not
    # against a step. Pre-fix this landed on "session_start" purely because
    # _highest_priority_pending_step happened to return it at read time.
    assert state.nudge_counts == {}
    assert state.pool_nudge_counts.get("context") == 1
    assert len(state.nudge_history) == 1
    events = (trw_dir / "context" / "session-events.jsonl").read_text(encoding="utf-8")
    assert events.count('"event":"nudge_shown"') == 1


def test_standard_workflow_pool_records_synthetic_impression_and_counts(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\n", encoding="utf-8")

    with (
        patch("trw_mcp.state.ceremony_nudge._select_nudge_pool", return_value="workflow"),
        patch("trw_mcp.tools._ceremony_status._has_cached_learning_weights", return_value=False),
        patch("trw_mcp.state._nudge_content.load_pool_message", return_value="Workflow nudge"),
    ):
        result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert result["nudge_content"] == "Workflow nudge"
    state = read_ceremony_state(trw_dir)
    # Ledger UF-023: workflow-pool messages are phase-keyed prose that names no
    # ceremony step (load_pool_message("workflow", phase_hint=state.phase)), so
    # the emission ledger records it and the per-step ledger correctly does not.
    assert state.nudge_counts == {}
    assert state.pool_nudge_counts.get("workflow") == 1
    assert len(state.nudge_history) == 1
    events = (trw_dir / "context" / "session-events.jsonl").read_text(encoding="utf-8")
    assert events.count('"event":"nudge_shown"') == 1


def test_append_ceremony_status_uses_workspace_config_not_global_singleton(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\nnudge_messenger: minimal\n", encoding="utf-8")

    with patch("trw_mcp.models.config._loader.get_config", side_effect=AssertionError("should not use global config")):
        result = append_ceremony_status({"status": "ok"}, trw_dir)

    assert isinstance(result.get("nudge_content"), str)
