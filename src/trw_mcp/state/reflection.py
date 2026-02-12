"""Reflection orchestration — event collection, learning extraction, record creation.

Extracted from tools/learning.py (Sprint 11) to separate the core
reflection pipeline from tool registration.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import NamedTuple

from trw_mcp.clients.llm import LLMClient
from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry, Reflection
from trw_mcp.state.analytics import (
    detect_tool_sequences,
    extract_learnings_from_llm,
    extract_learnings_mechanical,
    find_repeated_operations,
    find_success_patterns,
    generate_learning_id,
    has_existing_success_learning,
    is_error_event,
    save_learning_entry,
    surface_validated_learnings,
)
from trw_mcp.state.llm_helpers import llm_extract_learnings
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)

# Named caps for mechanical extraction
_MAX_ERROR_LEARNINGS = 5
_MAX_REPEATED_OPS = 3


class ReflectionInputs(NamedTuple):
    """Collected inputs for reflection processing."""

    events: list[dict[str, object]]
    run_id: str | None
    error_events: list[dict[str, object]]
    phase_transitions: list[dict[str, object]]
    repeated_ops: list[tuple[str, int]]
    success_patterns: list[dict[str, str]]
    tool_sequences: list[dict[str, object]]
    validated_learnings: list[dict[str, object]]


def collect_reflection_inputs(
    run_path: str | None,
    trw_dir: Path,
) -> ReflectionInputs:
    """Load events and categorize them for reflection processing.

    Args:
        run_path: Optional path to run directory.
        trw_dir: Path to .trw directory.

    Returns:
        ReflectionInputs with all categorized event data.
    """
    events: list[dict[str, object]] = []
    run_id: str | None = None

    if run_path:
        resolved = Path(run_path).resolve()
        events_path = resolved / "meta" / "events.jsonl"
        if _reader.exists(events_path):
            events = _reader.read_jsonl(events_path)

        run_yaml = resolved / "meta" / "run.yaml"
        if _reader.exists(run_yaml):
            state = _reader.read_yaml(run_yaml)
            run_id_val = state.get("run_id")
            if isinstance(run_id_val, str):
                run_id = run_id_val

    error_events = [e for e in events if is_error_event(e)]
    phase_transitions = [e for e in events if e.get("event") == "phase_transition"]
    repeated_ops = find_repeated_operations(events)
    success_patterns = find_success_patterns(events)
    tool_sequences = detect_tool_sequences(
        events,
        lookback=_config.reflect_sequence_lookback,
    )
    validated_learnings = surface_validated_learnings(
        trw_dir,
        q_threshold=_config.reflect_q_value_threshold,
        cold_start_threshold=_config.q_cold_start_threshold,
    )

    return ReflectionInputs(
        events=events,
        run_id=run_id,
        error_events=error_events,
        phase_transitions=phase_transitions,
        repeated_ops=repeated_ops,
        success_patterns=success_patterns,
        tool_sequences=tool_sequences,
        validated_learnings=validated_learnings,
    )


def generate_reflection_learnings(
    inputs: ReflectionInputs,
    trw_dir: Path,
) -> tuple[list[dict[str, str]], bool, int]:
    """Extract new learnings via LLM or mechanical fallback + success patterns.

    Args:
        inputs: Collected reflection inputs.
        trw_dir: Path to .trw directory.

    Returns:
        Tuple of (new_learnings, llm_used, positive_count).
    """
    new_learnings: list[dict[str, str]] = []
    llm_used = False

    llm = LLMClient(model=_config.llm_default_model)
    if inputs.events and _config.llm_enabled and llm.available:  # pragma: no cover
        llm_result = llm_extract_learnings(inputs.events, llm)
        if llm_result is not None:
            llm_used = True
            new_learnings = extract_learnings_from_llm(llm_result, trw_dir)

    if not llm_used:
        new_learnings = extract_learnings_mechanical(
            inputs.error_events,
            inputs.repeated_ops,
            trw_dir,
            max_errors=_MAX_ERROR_LEARNINGS,
            max_repeated=_MAX_REPEATED_OPS,
        )

    positive_count = _append_success_pattern_learnings(
        inputs.success_patterns,
        trw_dir,
        new_learnings,
    )

    return new_learnings, llm_used, positive_count


def _append_success_pattern_learnings(
    success_patterns: list[dict[str, str]],
    trw_dir: Path,
    new_learnings: list[dict[str, str]],
) -> int:
    """Append success pattern learnings to the learnings list.

    Args:
        success_patterns: Success patterns discovered during reflection.
        trw_dir: Path to .trw directory.
        new_learnings: List to append new learnings to (mutated in place).

    Returns:
        Number of positive learnings created.
    """
    positive_count = 0
    max_positive = _config.reflect_max_positive_learnings

    for pattern in success_patterns:
        if positive_count >= max_positive:
            break

        summary = pattern["summary"]
        if has_existing_success_learning(trw_dir, summary):
            continue

        learning_id = generate_learning_id()
        entry = LearningEntry(
            id=learning_id,
            summary=summary,
            detail=pattern.get("detail", ""),
            tags=["success", "pattern", "auto-discovered"],
            impact=0.5,
            recurrence=int(pattern.get("count", 1)),
            source_type="agent",
            source_identity="trw_reflect",
        )
        save_learning_entry(trw_dir, entry)
        new_learnings.append({"id": learning_id, "summary": entry.summary})
        positive_count += 1

    return positive_count


def create_reflection_record(
    inputs: ReflectionInputs,
    new_learnings: list[dict[str, str]],
    scope: str,
) -> Reflection:
    """Create a Reflection model from inputs and extracted learnings.

    Args:
        inputs: Collected reflection inputs.
        new_learnings: Extracted learning summaries.
        scope: Reflection scope — "session", "run", or "wave".

    Returns:
        Reflection model instance.
    """
    what_worked = _build_what_worked(inputs.phase_transitions, inputs.success_patterns)
    what_failed = _build_what_failed(inputs.error_events)
    repeated_patterns = _build_repeated_patterns(inputs.repeated_ops)

    reflection_id = generate_learning_id()
    return Reflection(
        id=reflection_id,
        run_id=inputs.run_id,
        scope=scope,
        timestamp=datetime.now(timezone.utc),
        events_analyzed=len(inputs.events),
        what_worked=what_worked,
        what_failed=what_failed,
        repeated_patterns=repeated_patterns,
        new_learnings=[item["id"] for item in new_learnings],
    )


def _build_what_worked(
    phase_transitions: list[dict[str, object]],
    success_patterns: list[dict[str, str]],
) -> list[str]:
    """Build what_worked list from phase transitions and success patterns."""
    transitions = [str(e.get("event")) for e in phase_transitions]
    patterns = [p["summary"] for p in success_patterns]
    return transitions + patterns


def _build_what_failed(error_events: list[dict[str, object]]) -> list[str]:
    """Build what_failed list from error events."""
    return [str(e.get("event")) for e in error_events[:_MAX_ERROR_LEARNINGS]]


def _build_repeated_patterns(repeated_ops: list[tuple[str, int]]) -> list[str]:
    """Build repeated_patterns list from operation tuples."""
    return [f"{op} ({count}x)" for op, count in repeated_ops[:_MAX_REPEATED_OPS]]


def persist_reflection(
    trw_dir: Path,
    reflection: Reflection,
    run_path: str | None,
    scope: str,
    learnings_count: int,
) -> None:
    """Persist reflection record and log event to run.

    Args:
        trw_dir: Path to .trw directory.
        reflection: Reflection model to persist.
        run_path: Optional run path for event logging.
        scope: Reflection scope.
        learnings_count: Number of learnings produced.
    """
    reflection_path = (
        trw_dir / _config.reflections_dir
        / f"{date.today().isoformat()}-{reflection.id}.yaml"
    )
    _writer.write_yaml(reflection_path, model_to_dict(reflection))

    if run_path:
        resolved_run = Path(run_path).resolve()
        run_events_path = resolved_run / "meta" / "events.jsonl"
        if run_events_path.parent.exists():
            _events.log_event(run_events_path, "reflection_complete", {
                "reflection_id": reflection.id,
                "scope": scope,
                "learnings_produced": learnings_count,
            })
