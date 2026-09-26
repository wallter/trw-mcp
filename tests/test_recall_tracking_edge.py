"""Edge-case tests for recall_tracking module.

Covers uncovered branches and boundary conditions NOT in test_recall_tracking.py:
- record_recall fail-open specifically on StateError
- PRD-FIX-144 NFR03: an unwritable logs directory leaves every tool response unchanged
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests._memory_store_fake import FakeMemoryStore
from tests._structlog_capture import captured_structlog  # noqa: F401  -- fixture, imported by name
from trw_mcp.exceptions import StateError
from trw_mcp.state.recall_tracking import (
    record_recall,
)


@pytest.fixture
def fake_memory_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
    """Override of ``tests._memory_fixtures.fake_memory_store`` pinned to "default".

    See ``tests/test_tools_learning_recall_modes.py`` for the same workaround:
    ``store_learning`` writes under the shared fixture's ``FAKE_NAMESPACE``,
    which ``FakeMemoryStore.recall()`` (only searches "default") never sees.
    """
    from trw_mcp.state import _store_selection

    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "default"))
    return store


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a temp .trw directory."""
    d = tmp_path / ".trw"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _patch_trw_dir(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect resolve_trw_dir to the temp directory."""
    monkeypatch.setattr(
        "trw_mcp.state.recall_tracking.resolve_trw_dir",
        lambda: trw_dir,
    )


# ---------------------------------------------------------------------------
# record_recall — StateError fail-open
# ---------------------------------------------------------------------------


class TestRecordRecallStateError:
    """record_recall is fail-open on any write failure, including StateError."""

    def test_state_error_returns_false(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """StateError during write returns False, not raised."""
        monkeypatch.setattr(
            "trw_mcp.state.recall_tracking._append_rows",
            MagicMock(side_effect=StateError("write failed")),
        )

        result = record_recall("L-fail", "query")
        assert result is False


# ---------------------------------------------------------------------------
# PRD-FIX-144 NFR03 — fail-open writes, diagnostics to structlog debug
# ---------------------------------------------------------------------------

#: Values that differ between two otherwise identical calls for reasons unrelated
#: to the logs directory: timings, call-count-driven ceremony nudges, and the
#: recall ranking score (and so tokens_used), which the first pass's access and
#: feedback writes to memory.db legitimately move.
_VOLATILE_KEYS = frozenset({"step_durations_ms", "ceremony_status", "nudge_content", "reversion_prompt", "tokens_used"})
_VOLATILE_ENTRY_KEYS = frozenset({"preference_score"})


def _drive_four_tools(server: Any, lid: str) -> dict[str, dict[str, Any]]:
    from tests.conftest import extract_tool_fn

    return {
        "trw_recall": extract_tool_fn(server, "trw_recall")(query="app.py startup"),
        # trw_code(mode="hint") wraps one BeforeEditHintResult per file in a
        # {"status","hints","count"} envelope; unwrap to the single hint so
        # this dict's shape (and _stable's per-entry cleaning) matches the
        # other three tools' flat responses. Keyed "trw_code_hint" (not the
        # tool's own name) purely as this dict's own label.
        "trw_code_hint": extract_tool_fn(server, "trw_code")(mode="hint", files="app.py")["hints"][0],
        "trw_build_check": extract_tool_fn(server, "trw_build_check")(tests_passed=True, test_count=2),
        # PRD-CORE-291 merged trw_learn_update into trw_learn's update mode.
        "trw_learn": extract_tool_fn(server, "trw_learn")(learning_id=lid, status="active"),
    }


def _stable(response: dict[str, Any]) -> dict[str, Any]:
    stable = {k: v for k, v in response.items() if k not in _VOLATILE_KEYS}
    if isinstance(stable.get("learnings"), list):
        stable["learnings"] = [
            {k: v for k, v in entry.items() if k not in _VOLATILE_ENTRY_KEYS} if isinstance(entry, dict) else entry
            for entry in stable["learnings"]
        ]
    return stable


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_unwritable_logs_leave_responses_unchanged(
    trw_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_structlog: list[dict[str, Any]],
    fake_memory_store: FakeMemoryStore,
) -> None:
    from tests.conftest import extract_tool_fn, make_test_server

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw_dir.parent))
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    server = make_test_server("learning", "code", "build")
    lid = extract_tool_fn(server, "trw_learn")(
        summary="app.py startup must load config first", detail="app.py reads config.", impact=0.7
    )["learning_id"]

    writable = _drive_four_tools(server, lid)
    logs = trw_dir / "logs"
    rows_before = {p.name: p.read_bytes() for p in logs.glob("*.jsonl")}
    assert {"recall_tracking.jsonl", "session_outcomes.jsonl"} <= set(rows_before)
    captured_structlog.clear()
    # The transition nudge is one-shot per session (PRD-CORE-294 FR04), so both passes must start from the same
    # selector state; otherwise the second pass differs because the nudge already fired, not because logs failed.
    from trw_mcp.state._ceremony_progress_state import read_ceremony_state, write_ceremony_state

    state = read_ceremony_state(trw_dir)
    assert state.transition_nudges, "the writable pass should have shown a transition nudge"
    state.transition_nudges.clear()
    write_ceremony_state(trw_dir, state)
    # Unwritable = no new file can be created AND no existing log can be appended.
    existing = [p for p in logs.iterdir() if p.is_file()]
    for path in existing:
        path.chmod(0o400)
    logs.chmod(0o500)
    try:
        unwritable = _drive_four_tools(server, lid)
    finally:
        logs.chmod(0o700)
        for path in existing:
            path.chmod(0o600)

    for tool, response in writable.items():
        assert set(unwritable[tool]) == set(response), tool
        assert _stable(unwritable[tool]) == _stable(response), tool
    # The new writes really failed (nothing appended) ...
    assert (logs / "recall_tracking.jsonl").read_bytes() == rows_before["recall_tracking.jsonl"]
    assert (logs / "session_outcomes.jsonl").read_bytes() == rows_before["session_outcomes.jsonl"]
    # ... and each was reported as its named debug event. trw_learn (update mode) no
    # longer writes to the logs directory at all (PRD-CORE-293 removed the
    # feedback-triggered outcome write), so it has no failed-write event here.
    by_event = {e.get("event"): e.get("log_level") for e in captured_structlog}
    for event in (
        "recall_record_failed",
        "before_edit_exposure_record_failed",
        "session_observation_record_failed",
    ):
        assert by_event.get(event) == "debug", (event, by_event.get(event))
