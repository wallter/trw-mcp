"""Tests for nudge deduplication logic (PRD-CORE-103 Sprint 83 Task 4).

Tests cover:
- select_nudge_learning in _nudge_rules.py
- Nudge dedup wiring in _session_recall_helpers.py (append_ceremony_nudge)
- Event logging for nudge_shown events
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from trw_mcp.state._nudge_state import (
    CeremonyState,
    NudgeHistoryEntry,
    clear_nudge_history,
    is_nudge_eligible,
    read_ceremony_state,
    write_ceremony_state,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_trw_dir(tmp_path: Path) -> Path:
    """Create .trw/context/ directory and return the .trw path."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


def _make_candidate(lid: str, impact: float = 0.8) -> dict[str, object]:
    """Create a minimal learning candidate dict."""
    return {"id": lid, "summary": f"Learning {lid}", "impact": impact}


# ---------------------------------------------------------------------------
# select_nudge_learning — same-phase suppression
# ---------------------------------------------------------------------------


class TestSelectNudgeLearning:
    def test_same_phase_suppressed(self) -> None:
        """show L-a3Fq in IMPLEMENT, verify it's filtered out on second nudge in IMPLEMENT."""
        from trw_mcp.state._nudge_rules import select_nudge_learning

        state = CeremonyState(phase="implement")
        state.nudge_history["L-a3Fq"] = NudgeHistoryEntry(
            phases_shown=["implement"],
            turn_first_shown=1,
            last_shown_turn=1,
        )

        candidates = [_make_candidate("L-a3Fq"), _make_candidate("L-other")]
        selected, is_fallback = select_nudge_learning(state, candidates, "implement")

        # L-a3Fq should be suppressed, L-other returned instead
        assert selected is not None
        assert str(selected.get("id", "")) == "L-other"
        assert is_fallback is False

    def test_cross_phase_allowed(self) -> None:
        """show L-a3Fq in IMPLEMENT, verify it's eligible in VALIDATE."""
        from trw_mcp.state._nudge_rules import select_nudge_learning

        state = CeremonyState(phase="validate")
        state.nudge_history["L-a3Fq"] = NudgeHistoryEntry(
            phases_shown=["implement"],
            turn_first_shown=1,
            last_shown_turn=1,
        )

        candidates = [_make_candidate("L-a3Fq")]
        selected, is_fallback = select_nudge_learning(state, candidates, "validate")

        assert selected is not None
        assert str(selected.get("id", "")) == "L-a3Fq"
        assert is_fallback is False

    def test_fallback_least_recently_shown(self) -> None:
        """All candidates already shown -> fallback to oldest (least recently shown)."""
        from trw_mcp.state._nudge_rules import select_nudge_learning

        state = CeremonyState(phase="implement")
        # L-old was shown at turn 1, L-new at turn 10
        state.nudge_history["L-old"] = NudgeHistoryEntry(
            phases_shown=["implement"],
            turn_first_shown=1,
            last_shown_turn=1,
        )
        state.nudge_history["L-new"] = NudgeHistoryEntry(
            phases_shown=["implement"],
            turn_first_shown=10,
            last_shown_turn=10,
        )

        candidates = [_make_candidate("L-new"), _make_candidate("L-old")]
        selected, is_fallback = select_nudge_learning(state, candidates, "implement")

        # Should fall back to L-old (last_shown_turn=1, the oldest)
        assert selected is not None
        assert str(selected.get("id", "")) == "L-old"
        assert is_fallback is True

    def test_fallback_flag_in_surface_event(self) -> None:
        """Fallback flag is True when all candidates are already shown."""
        from trw_mcp.state._nudge_rules import select_nudge_learning

        state = CeremonyState(phase="implement")
        state.nudge_history["L-only"] = NudgeHistoryEntry(
            phases_shown=["implement"],
            turn_first_shown=5,
            last_shown_turn=5,
        )

        candidates = [_make_candidate("L-only")]
        selected, is_fallback = select_nudge_learning(state, candidates, "implement")

        assert selected is not None
        assert is_fallback is True

    def test_empty_candidates_returns_none(self) -> None:
        """Empty candidate list returns (None, False)."""
        from trw_mcp.state._nudge_rules import select_nudge_learning

        state = CeremonyState(phase="implement")
        selected, is_fallback = select_nudge_learning(state, [], "implement")

        assert selected is None
        assert is_fallback is False

    def test_is_nudge_eligible_empty_history(self) -> None:
        """Empty nudge history means everything is eligible."""
        state = CeremonyState()
        assert is_nudge_eligible(state, "L-anything", "implement") is True
        assert is_nudge_eligible(state, "L-anything", "validate") is True
        assert is_nudge_eligible(state, "L-anything", "deliver") is True

    def test_is_nudge_eligible_post_compaction(self, tmp_path: Path) -> None:
        """After clear_nudge_history, everything is eligible again."""
        trw_dir = _setup_trw_dir(tmp_path)

        # Set up state with history
        state = CeremonyState()
        state.nudge_history["L-a3Fq"] = NudgeHistoryEntry(
            phases_shown=["implement"],
            turn_first_shown=1,
            last_shown_turn=1,
        )
        write_ceremony_state(trw_dir, state)

        # Verify NOT eligible in implement
        loaded = read_ceremony_state(trw_dir)
        assert is_nudge_eligible(loaded, "L-a3Fq", "implement") is False

        # Clear (simulating compaction detection)
        clear_nudge_history(trw_dir)

        # Verify now eligible again
        loaded2 = read_ceremony_state(trw_dir)
        assert is_nudge_eligible(loaded2, "L-a3Fq", "implement") is True


# ---------------------------------------------------------------------------
# Fail-open on corrupt state
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_failopen_on_corrupt_state(self, tmp_path: Path) -> None:
        """Corrupt ceremony state file -> nudge still works (fail-open)."""
        from trw_mcp.state._nudge_rules import select_nudge_learning

        trw_dir = _setup_trw_dir(tmp_path)

        # Write corrupt JSON to ceremony state
        state_path = trw_dir / "context" / "ceremony-state.json"
        state_path.write_text("{{{invalid json!!", encoding="utf-8")

        # read_ceremony_state should return defaults (fail-open)
        state = read_ceremony_state(trw_dir)
        assert state.nudge_history == {}

        # select_nudge_learning should work fine with default state
        candidates = [_make_candidate("L-a3Fq")]
        selected, is_fallback = select_nudge_learning(state, candidates, "implement")

        assert selected is not None
        assert str(selected.get("id", "")) == "L-a3Fq"
        assert is_fallback is False


# ---------------------------------------------------------------------------
# Event logging — nudge_shown to events.jsonl
# ---------------------------------------------------------------------------


class TestNudgeEventLogging:
    def test_nudge_logs_learning_id_to_events_jsonl(self, tmp_path: Path) -> None:
        """Verify events.jsonl gets a nudge_shown event with learning_id."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        trw_dir = _setup_trw_dir(tmp_path)
        # Create a session-events path
        events_path = trw_dir / "context" / "session-events.jsonl"

        log_nudge_event(
            events_path=events_path,
            learning_id="L-a3Fq",
            phase="implement",
            is_fallback=False,
        )

        # Verify the event was written
        assert events_path.exists()
        lines = events_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "nudge_shown"
        assert event["learning_id"] == "L-a3Fq"
        assert event["phase"] == "implement"
        assert event["fallback"] is False
        assert "ts" in event  # timestamp present

    def test_nudge_event_with_fallback_flag(self, tmp_path: Path) -> None:
        """Verify fallback flag is correctly logged."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        trw_dir = _setup_trw_dir(tmp_path)
        events_path = trw_dir / "context" / "session-events.jsonl"

        log_nudge_event(
            events_path=events_path,
            learning_id="L-old",
            phase="validate",
            is_fallback=True,
        )

        lines = events_path.read_text(encoding="utf-8").strip().splitlines()
        event = json.loads(lines[0])
        assert event["fallback"] is True

    def test_nudge_event_failopen_on_write_error(self, tmp_path: Path) -> None:
        """log_nudge_event fails open if the events path is not writable."""
        from trw_mcp.tools._legacy_ceremony_nudge import log_nudge_event

        # Use a non-existent directory that will fail on write
        bad_path = tmp_path / "nonexistent" / "deep" / "events.jsonl"

        # Should not raise
        log_nudge_event(
            events_path=bad_path,
            learning_id="L-a3Fq",
            phase="implement",
            is_fallback=False,
        )


# ---------------------------------------------------------------------------
# Integration: append_ceremony_nudge with nudge learning dedup
# ---------------------------------------------------------------------------


class TestAppendCeremonyNudgeDedup:
    def test_compat_wrapper_still_adds_ceremony_status(self, tmp_path: Path) -> None:
        """append_ceremony_nudge remains a backwards-compatible status wrapper."""
        trw_dir = _setup_trw_dir(tmp_path)
        state = CeremonyState(session_started=True, phase="implement")
        write_ceremony_state(trw_dir, state)

        from trw_mcp.tools._legacy_ceremony_nudge import append_ceremony_nudge

        response: dict[str, object] = {"status": "ok"}

        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            result = append_ceremony_nudge(response.copy(), trw_dir=trw_dir)

        assert "ceremony_status" in result


# ---------------------------------------------------------------------------
# PRD-CORE-146-FR02: phase-crossing dedup regression
# ---------------------------------------------------------------------------


class TestPhaseCrossingDedupRegression:
    """PRD-CORE-146-FR02: dedup does not over-exclude on phase transition.

    Regression guard for bug L-SgB1: if every nudge_history entry records
    phases_shown=["deliver"] (because turn_first_shown=0 was a silent default
    and the only phase ever recorded was the shipping phase), selecting a
    nudge in a DIFFERENT phase must still return an eligible learning rather
    than trivially excluding everything.
    """

    def test_phase_crossing_dedup_regression(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        from trw_mcp.state.ceremony_nudge import _select_learning_injection_candidate

        trw_dir = _setup_trw_dir(tmp_path)

        # Pathological state: every prior nudge was recorded with
        # phases_shown=["deliver"] (the bug).
        state = CeremonyState(phase="research")
        state.nudge_history["L-a"] = NudgeHistoryEntry(
            phases_shown=["deliver"],
            turn_first_shown=0,
            last_shown_turn=0,
        )
        state.nudge_history["L-b"] = NudgeHistoryEntry(
            phases_shown=["deliver"],
            turn_first_shown=0,
            last_shown_turn=0,
        )
        write_ceremony_state(trw_dir, state)

        candidates = [
            {"id": "L-a", "summary": "Alpha finding", "impact": 0.8},
            {"id": "L-b", "summary": "Beta finding", "impact": 0.7},
        ]

        fake_ctx = SimpleNamespace(modified_files=["trw-mcp/src/trw_mcp/foo.py"])

        with (
            patch(
                "trw_mcp.state.recall_context.build_recall_context",
                return_value=fake_ctx,
            ),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=candidates,
            ),
            patch(
                "trw_mcp.state.learning_injection.infer_domain_tags",
                return_value=[],
            ),
        ):
            selected, target_label = _select_learning_injection_candidate(
                state,
                trw_dir,
                skip_phase_duplicates=True,
            )

        # Phase is "research" — "deliver" is NOT in current phase so dedup
        # does NOT exclude. At least one candidate must survive.
        assert selected is not None, (
            "phase-crossing dedup over-excluded: all candidates had "
            "phases_shown=['deliver'] and current phase is 'research'"
        )
        assert str(selected.get("id", "")) in {"L-a", "L-b"}
        assert target_label == "foo.py"


# ---------------------------------------------------------------------------
# iter-22 root-cause fix — end-to-end regression
# ---------------------------------------------------------------------------


class TestIter22FixEndToEnd:
    """End-to-end regression for the 2026-04-27 iter-22 root-cause fix.

    Closes the "not yet validated end-to-end" caveat in the
    HANDOFF-NEXT-INSTANCE.md commit f2d7f170a. The investigation
    (docs/research/trw-distill/ITER-22-NAIVE-INJECTION-INVESTIGATION-2026-04-27.md)
    localized the structural defect to ``_PATH_DOMAIN_MAP`` having no
    entries for external eval-corpus repos. Commit 56ca4b9c2 added 20
    repo-root entries to close the gap.

    These tests prove that the full pipeline behavior changes — not just
    the unit-level ``infer_domain_tags`` output. Specifically:

    1. ``_select_learning_injection_candidate`` now passes a non-None
       ``tags`` argument on the first attempt when the recall context's
       modified file is in a SWE-bench-shaped path (sphinx, pylint,
       astropy, ...). Previously these paths fell through to ``None``.
    2. With sphinx-tagged candidates available, the relevance ranker
       picks one of them rather than degenerating to impact-only.
    """

    def test_swebench_path_routes_tags_into_recall(self, tmp_path: Path) -> None:
        """The map extension flows through to the ranker: external repo
        paths now produce tagged recalls instead of falling to ``None``.
        """
        from types import SimpleNamespace

        from trw_mcp.state.ceremony_nudge import _select_learning_injection_candidate

        trw_dir = _setup_trw_dir(tmp_path)
        state = CeremonyState(phase="implement")
        write_ceremony_state(trw_dir, state)

        # Plausible sphinx-domain candidate that should surface.
        sphinx_candidate = {
            "id": "L-sphinx",
            "summary": "Sphinx domain registration gotcha",
            "impact": 0.8,
            "tags": ["sphinx", "documentation"],
        }
        # Capture the tags kwarg from each recall_learnings call.
        recall_call_tags: list[list[str] | None] = []

        def _capture_recall(*args: object, **kwargs: object) -> list[dict[str, object]]:
            recall_call_tags.append(kwargs.get("tags"))  # type: ignore[arg-type]
            return [sphinx_candidate]

        fake_ctx = SimpleNamespace(modified_files=["sphinx/domains/python.py"])

        with (
            patch(
                "trw_mcp.state.recall_context.build_recall_context",
                return_value=fake_ctx,
            ),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_capture_recall,
            ),
        ):
            selected, target_label = _select_learning_injection_candidate(
                state,
                trw_dir,
            )

        # Selector returned the sphinx candidate.
        assert selected is not None, "post-fix selector should not return None for a SWE-bench path"
        assert selected.get("id") == "L-sphinx"
        assert target_label == "python.py"
        # First recall attempt carried a non-empty tag list — pre-fix this
        # would have been None or empty for sphinx paths.
        assert recall_call_tags, "expected at least one recall_learnings call"
        first_tags = recall_call_tags[0]
        assert first_tags, (
            f"first recall attempt must carry tags after the map "
            f"extension (got {first_tags!r}); regression points to a "
            f"_PATH_DOMAIN_MAP truncation"
        )
        assert "sphinx" in first_tags, f"first recall must carry the sphinx tag (got {first_tags!r})"

    def test_pre_fix_collapse_simulated_via_empty_tags(
        self,
        tmp_path: Path,
    ) -> None:
        """Simulate the pre-fix world by patching ``infer_domain_tags`` to
        return an empty set (which is what sphinx/pylint/astropy paths
        produced before commit 56ca4b9c2). The selector should then call
        recall_learnings with ``tags=None`` on the first attempt — the
        exact failure mode the fix addresses.
        """
        from types import SimpleNamespace

        from trw_mcp.state.ceremony_nudge import _select_learning_injection_candidate

        trw_dir = _setup_trw_dir(tmp_path)
        state = CeremonyState(phase="implement")
        write_ceremony_state(trw_dir, state)

        # Off-domain TRW-framework candidate — what the impact-only
        # ranker would have surfaced for a SWE-bench task.
        framework_candidate = {
            "id": "L-9026cbce",
            "summary": "SQLite WAL corruption gotcha",
            "impact": 0.95,
            "tags": ["framework", "sqlite", "wal"],
        }
        recall_call_tags: list[list[str] | None] = []

        def _capture_recall(*args: object, **kwargs: object) -> list[dict[str, object]]:
            recall_call_tags.append(kwargs.get("tags"))  # type: ignore[arg-type]
            return [framework_candidate]

        fake_ctx = SimpleNamespace(modified_files=["sphinx/domains/python.py"])

        with (
            patch(
                "trw_mcp.state.recall_context.build_recall_context",
                return_value=fake_ctx,
            ),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                side_effect=_capture_recall,
            ),
            # Force the pre-fix degenerate behavior.
            patch(
                "trw_mcp.state.learning_injection.infer_domain_tags",
                return_value=set(),
            ),
        ):
            selected, _ = _select_learning_injection_candidate(state, trw_dir)

        # Pre-fix path: tags=None on first attempt. The off-domain
        # framework_candidate is what got surfaced — exactly the iter-22
        # failure mode the investigation identified.
        assert recall_call_tags
        assert recall_call_tags[0] is None, f"pre-fix simulation should pass tags=None (got {recall_call_tags[0]!r})"
        assert selected is not None and selected.get("id") == "L-9026cbce"
