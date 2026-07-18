"""trw_learn type-alias coercion (Potemkin defect C, sub_zAfRqZYYq2KtF72d).

``trw_learn(type='gotcha')`` was rejected with "'gotcha' is not a valid
MemoryType" even though the tool docstring and the trw-deliver / trw-ceremony
skills present "gotchas" as first-class durable content to record. The fix is
a small, justified alias map at the trw-mcp tool boundary that coerces an
advertised-but-non-enum type name to a valid ``MemoryType`` value (with a
logged coercion) before enum validation — WITHOUT widening trw-memory's enum.
"""

from __future__ import annotations

import structlog
from structlog.testing import capture_logs

from trw_mcp.tools._learning_module_helpers import (
    _LEARN_TYPE_ALIASES,
    _coerce_learn_type,
    _note_run_path_compat,
    _validate_learn_enums,
)


def test_gotcha_alias_coerces_to_valid_enum() -> None:
    """'gotcha' maps to a valid MemoryType value (the submission's case)."""
    coerced = _coerce_learn_type("gotcha")
    assert coerced in {"incident", "pattern", "convention", "hypothesis", "workaround"}
    # And the coerced value now passes enum validation.
    assert _validate_learn_enums(type=coerced, confidence="unverified", protection_tier="normal") is None


def test_valid_enum_passes_through_unchanged() -> None:
    """A real MemoryType value is never rewritten."""
    for valid in ("incident", "pattern", "convention", "hypothesis", "workaround"):
        assert _coerce_learn_type(valid) == valid


def test_unknown_type_passes_through_unchanged_for_validator_to_reject() -> None:
    """A genuinely unknown type is left intact so the enum validator still
    produces an honest rejection (we only alias advertised vocabulary)."""
    assert _coerce_learn_type("complete-nonsense") == "complete-nonsense"


def test_alias_map_is_small_and_advertised_only() -> None:
    """Substrate-First: the alias map stays small and every key maps to a
    valid MemoryType value."""
    valid = {"incident", "pattern", "convention", "hypothesis", "workaround"}
    assert _LEARN_TYPE_ALIASES, "alias map must not be empty"
    assert len(_LEARN_TYPE_ALIASES) <= 6, "keep the alias map small and justified"
    assert "gotcha" in _LEARN_TYPE_ALIASES
    for src, dst in _LEARN_TYPE_ALIASES.items():
        assert dst in valid, f"alias {src!r} -> {dst!r} must target a valid MemoryType"
        assert src not in valid, f"alias key {src!r} must NOT shadow a real enum value"


def test_coercion_emits_debug_log() -> None:
    """The coercion is observable, not silent — log the alias mapping."""
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    with capture_logs() as logs:
        _coerce_learn_type("gotcha")
    events = [e for e in logs if e.get("event") == "learn_type_alias_coerced"]
    assert len(events) == 1
    assert events[0]["requested"] == "gotcha"
    assert events[0]["resolved"] == _LEARN_TYPE_ALIASES["gotcha"]


def test_trw_learn_tool_accepts_gotcha_end_to_end() -> None:
    """The MCP tool persists a type='gotcha' learning instead of rejecting it."""
    from tests.conftest import extract_tool_fn, make_test_server

    learn_fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    result = learn_fn(
        summary="watch out: editable install resolves to MAIN repo not the worktree",
        detail="set PYTHONPATH=$PWD/src or your edits are not exercised",
        type="gotcha",
    )
    assert isinstance(result, dict)
    assert result["status"] != "rejected", result


def test_note_run_path_compat_emits_debug_log_when_passed() -> None:
    """P2-7: run_path is accept-and-log ONLY (never validated against a run
    directory). The debug event fires so the accepted-but-storage-inert value
    is observable rather than silently dropped."""
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    with capture_logs() as logs:
        _note_run_path_compat("/repo/.trw/runs/some-task/some-run")
    events = [e for e in logs if e.get("event") == "learn_run_path_accepted_for_compat"]
    assert len(events) == 1
    assert events[0]["run_path"] == "/repo/.trw/runs/some-task/some-run"


def test_note_run_path_compat_silent_when_none() -> None:
    """No run_path supplied -> no compat log noise."""
    with capture_logs() as logs:
        _note_run_path_compat(None)
    assert not [e for e in logs if e.get("event") == "learn_run_path_accepted_for_compat"]


def test_project_alias_coerces_to_convention() -> None:
    """'project' maps to 'convention' (feedback sub_5qbmT6WPNoP58rlv item 8)."""
    assert "project" in _LEARN_TYPE_ALIASES
    assert _LEARN_TYPE_ALIASES["project"] == "convention"
    coerced = _coerce_learn_type("project")
    assert coerced == "convention"
    assert _validate_learn_enums(type=coerced, confidence="unverified", protection_tier="normal") is None


def test_project_alias_coercion_emits_debug_log() -> None:
    """The 'project' coercion is observable via the same logged-coercion path."""
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    with capture_logs() as logs:
        _coerce_learn_type("project")
    events = [e for e in logs if e.get("event") == "learn_type_alias_coerced"]
    assert len(events) == 1
    assert events[0]["requested"] == "project"
    assert events[0]["resolved"] == "convention"


def test_trw_learn_tool_accepts_run_path_for_compat() -> None:
    """trw_learn accepts a run_path kwarg without failing the call
    (feedback sub_5qbmT6WPNoP58rlv item 8)."""
    from tests.conftest import extract_tool_fn, make_test_server

    learn_fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    result = learn_fn(
        summary="prefer path-limited git commit to dodge the shared-index race",
        detail="git add + separate commit lets a concurrent agent swallow staged files",
        type="project",
        run_path="/repo/.trw/runs/some-task/some-run",
    )
    assert isinstance(result, dict)
    assert result["status"] != "rejected", result
