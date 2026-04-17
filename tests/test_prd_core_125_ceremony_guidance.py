"""Tests for PRD-CORE-125: Ceremony Guidance Quality.

FR01: Tool description behavioral cues
FR02: MCP server instructions
FR03: Context-reactive nudge messages (checkpoint, session_start, deliver)
FR04: Status line Done/Next/Then format
FR05: trw_deliver self-reflection gate
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state._nudge_messages import (
    _build_done_next_then_status,
    _build_done_next_then_status_light,
    _context_reactive_message,
)
from trw_mcp.state._nudge_state import CeremonyState, NudgeContext, ToolName

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _trw_dir(tmp_path: Path) -> Path:
    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    return trw


# -------------------------------------------------------------------------
# FR01: Tool Description Behavioral Cues
# -------------------------------------------------------------------------


class TestFR01ToolDescriptionCues:
    """FR01: Tool docstrings include 'When to call' behavioral cues."""

    def test_fr01_trw_learn_docstring_has_when_to_call(self) -> None:
        """trw_learn docstring includes temporal anchoring cue."""
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("learning")
        tools = get_tools_sync(server)
        learn_tool = tools.get("trw_learn")
        assert learn_tool is not None
        desc = learn_tool.description or ""
        # AC01: Includes a "When to call" sentence with temporal anchoring
        assert "root cause" in desc.lower() or "before writing the fix" in desc.lower()
        assert "moment" in desc.lower() or "when" in desc.lower()

    def test_fr01_trw_checkpoint_docstring_has_when_to_call(self) -> None:
        """trw_checkpoint docstring includes temporal anchoring cue."""
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("orchestration")
        tools = get_tools_sync(server)
        checkpoint_tool = tools.get("trw_checkpoint")
        assert checkpoint_tool is not None
        desc = checkpoint_tool.description or ""
        # AC01: Temporal anchoring — when to call
        assert "milestone" in desc.lower() or "after each" in desc.lower()
        # References consequence of not calling
        assert "compact" in desc.lower() or "resume" in desc.lower()

    def test_fr01_trw_deliver_docstring_has_self_reflection(self) -> None:
        """trw_deliver docstring includes self-reflection prompt (AC02)."""
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("ceremony")
        tools = get_tools_sync(server)
        deliver_tool = tools.get("trw_deliver")
        assert deliver_tool is not None
        desc = deliver_tool.description or ""
        # AC02: Self-reflection prompt about trw_learn
        assert "trw_learn" in desc.lower() or "discovery" in desc.lower()

    def test_fr01_trw_prd_create_docstring_has_when_to_call(self) -> None:
        """trw_prd_create docstring includes temporal anchoring cue."""
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("requirements")
        tools = get_tools_sync(server)
        prd_tool = tools.get("trw_prd_create")
        assert prd_tool is not None
        desc = prd_tool.description or ""
        # AC01: Temporal anchoring — before writing code
        assert "before" in desc.lower()

    def test_fr01_tool_descriptions_under_200_words(self) -> None:
        """AC03: All updated tool descriptions stay under 200 words."""
        from tests.conftest import get_tools_sync, make_test_server

        server = make_test_server("learning", "orchestration", "ceremony", "requirements")
        tools = get_tools_sync(server)

        for tool_name in ("trw_learn", "trw_checkpoint", "trw_deliver", "trw_prd_create"):
            tool = tools.get(tool_name)
            assert tool is not None, f"Tool {tool_name} not found"
            desc = tool.description or ""
            word_count = len(desc.split())
            assert word_count <= 200, f"Tool {tool_name} description is {word_count} words (max 200)"


# -------------------------------------------------------------------------
# FR02: MCP Server Instructions
# -------------------------------------------------------------------------


class TestFR02ServerInstructions:
    """FR02: MCP server instructions include workflow shape and value framing."""

    def test_fr02_instructions_include_workflow_shape(self) -> None:
        """AC05: Instructions include workflow shape."""
        from trw_mcp.server._app import _DEFAULT_INSTRUCTIONS

        instr = _DEFAULT_INSTRUCTIONS.lower()
        # Must include some reference to the workflow phases
        assert "plan" in instr or "implement" in instr or "verify" in instr

    def test_fr02_instructions_include_empirical_value(self) -> None:
        """AC06: Instructions include at least one empirical value statement."""
        from trw_mcp.server._app import _DEFAULT_INSTRUCTIONS

        # Must reference measurable impact
        assert "30%" in _DEFAULT_INSTRUCTIONS or "solve" in _DEFAULT_INSTRUCTIONS.lower()

    def test_fr02_instructions_under_100_words(self) -> None:
        """AC07: Instructions stay under 100 words."""
        from trw_mcp.server._app import _DEFAULT_INSTRUCTIONS

        word_count = len(_DEFAULT_INSTRUCTIONS.split())
        assert word_count <= 100, f"Instructions are {word_count} words (max 100)"

    def test_fr02_instructions_include_essential_tools(self) -> None:
        """AC08: Instructions mention the three essential tool calls."""
        from trw_mcp.server._app import _DEFAULT_INSTRUCTIONS

        instr = _DEFAULT_INSTRUCTIONS.lower()
        assert "session_start" in instr
        assert "learn" in instr
        assert "deliver" in instr


# -------------------------------------------------------------------------
# FR03: Context-Reactive Nudge Messages
# -------------------------------------------------------------------------


class TestFR03NudgeMessages:
    """FR03: Improved context-reactive nudge messages."""

    def test_fr03_checkpoint_message_has_learn_reflection(self) -> None:
        """AC09: Checkpoint nudge includes self-reflection prompt about trw_learn."""
        ctx = NudgeContext(tool_name=ToolName.CHECKPOINT)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        # Must mention trw_learn or discovery recording
        assert "trw_learn" in msg.lower() or "discover" in msg.lower() or "learn" in msg.lower()

    def test_fr03_session_start_full_mode_has_framework_ref(self) -> None:
        """AC10: Session start nudge in full mode references FRAMEWORK.md."""
        ctx = NudgeContext(tool_name=ToolName.SESSION_START)
        state = CeremonyState()
        # Default (full mode) should reference FRAMEWORK
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "FRAMEWORK" in msg

    def test_fr03_session_start_light_mode_skips_framework(self) -> None:
        """AC10: Session start nudge in light mode skips FRAMEWORK.md reference."""
        ctx = NudgeContext(tool_name=ToolName.SESSION_START)
        state = CeremonyState()
        # Light mode via ceremony_mode parameter
        msg = _context_reactive_message(ctx, state, ceremony_mode="light")
        assert msg is not None
        assert "FRAMEWORK" not in msg

    def test_fr03_deliver_message_includes_learning_count(self) -> None:
        """AC11: Deliver nudge includes learning count for positive reinforcement."""
        ctx = NudgeContext(tool_name=ToolName.DELIVER)
        state = CeremonyState(learnings_this_session=3)
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "3" in msg

    def test_fr03_deliver_message_zero_learnings(self) -> None:
        """AC11: Deliver nudge with 0 learnings still works."""
        ctx = NudgeContext(tool_name=ToolName.DELIVER)
        state = CeremonyState(learnings_this_session=0)
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "0" in msg or "no" in msg.lower()

    def test_fr03_messages_use_why_pattern(self) -> None:
        """AC12: Messages use WHY pattern (consequence or value, not bare commands)."""
        tools_and_states = [
            (NudgeContext(tool_name=ToolName.CHECKPOINT), CeremonyState()),
            (NudgeContext(tool_name=ToolName.SESSION_START), CeremonyState()),
            (NudgeContext(tool_name=ToolName.DELIVER), CeremonyState(learnings_this_session=2)),
        ]
        for ctx, state in tools_and_states:
            msg = _context_reactive_message(ctx, state)
            assert msg is not None
            # Each message should contain some consequence/value word
            msg_lower = msg.lower()
            has_consequence = any(
                word in msg_lower
                for word in ("persist", "future", "session", "insight", "save", "approach", "compound")
            )
            assert has_consequence, f"Message for {ctx.tool_name} lacks WHY pattern: {msg!r}"


# -------------------------------------------------------------------------
# FR04: Status Line — Done/Next/Then Format
# -------------------------------------------------------------------------


class TestFR04StatusLine:
    """FR04: Status line uses Done/Next/Then format."""

    def test_fr04_full_mode_uses_done_next_then(self) -> None:
        """AC13: Full mode status line uses Done/Next/Then format."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=0,
            learnings_this_session=1,
        )
        line = _build_done_next_then_status(state)
        assert "Done:" in line
        assert "Next:" in line

    def test_fr04_next_includes_why(self) -> None:
        """AC14: Next step includes a one-line WHY."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=0,
        )
        line = _build_done_next_then_status(state)
        # The "Next:" line should have a dash separator before the WHY
        assert "Next:" in line
        # WHY is separated by " -- " or " - "
        next_part = ""
        for part in line.split("\n"):
            if "Next:" in part:
                next_part = part
                break
        # The next part must contain a rationale (after a dash)
        assert "\u2014" in next_part or " -- " in next_part or " - " in next_part, (
            f"Next step lacks WHY rationale: {next_part!r}"
        )

    def test_fr04_light_mode_under_100_chars(self) -> None:
        """AC15: Light mode status line is under 100 characters."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=0,
        )
        line = _build_done_next_then_status_light(state)
        assert len(line) <= 100, f"Light mode status line is {len(line)} chars (max 100): {line!r}"

    def test_fr04_full_mode_under_200_chars(self) -> None:
        """AC16: Full mode status line is under 200 characters."""
        # Test several states
        states = [
            CeremonyState(session_started=True, learnings_this_session=1),
            CeremonyState(session_started=True, checkpoint_count=1, build_check_result="passed"),
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                review_called=True,
            ),
        ]
        for state in states:
            line = _build_done_next_then_status(state)
            assert len(line) <= 200, f"Full mode status line is {len(line)} chars (max 200): {line!r}"

    def test_fr04_all_complete_shows_only_done(self) -> None:
        """When all steps complete, only Done line appears."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=True,
            deliver_called=True,
            phase="done",
        )
        line = _build_done_next_then_status(state)
        assert "Done:" in line
        assert "Next:" not in line

    def test_fr04_learn_count_in_done(self) -> None:
        """Done line includes learn(N) when learnings > 0."""
        state = CeremonyState(
            session_started=True,
            learnings_this_session=3,
        )
        line = _build_done_next_then_status(state)
        assert "learn(3)" in line

    def test_fr04_light_mode_uses_pipe_format(self) -> None:
        """Light mode uses pipe-separated single line."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=0,
        )
        line = _build_done_next_then_status_light(state)
        assert "|" in line

    def test_fr04_done_next_then_no_session_start(self) -> None:
        """When session not started, Next is session_start."""
        state = CeremonyState(session_started=False)
        line = _build_done_next_then_status(state)
        assert "Next:" in line
        assert "session_start" in line.lower()


# -------------------------------------------------------------------------
# FR05: trw_deliver Self-Reflection Gate
# -------------------------------------------------------------------------


class TestFR05DeliverSelfReflection:
    """FR05: trw_deliver checks learnings and adds reminder/reinforcement."""

    def test_fr05_zero_learnings_includes_reminder(self, tmp_path: Path) -> None:
        """AC17: When learnings == 0, deliver response includes reminder text."""
        from trw_mcp.state._nudge_state import write_ceremony_state

        trw = _trw_dir(tmp_path)
        state = CeremonyState(
            session_started=True,
            deliver_called=False,
            learnings_this_session=0,
        )
        write_ceremony_state(trw, state)

        from trw_mcp.tools.ceremony import _learning_reflection_message

        msg = _learning_reflection_message(0)
        assert "no discoveries" in msg.lower() or "no learning" in msg.lower() or "0" in msg
        assert "root cause" in msg.lower() or "consider" in msg.lower() or "next agent" in msg.lower()

    def test_fr05_positive_learnings_includes_reinforcement(self, tmp_path: Path) -> None:
        """AC18: When learnings > 0, deliver response includes positive reinforcement."""
        from trw_mcp.tools.ceremony import _learning_reflection_message

        msg = _learning_reflection_message(3)
        assert "3" in msg
        assert "persist" in msg.lower() or "future" in msg.lower() or "session" in msg.lower()

    def test_fr05_delivery_always_succeeds(self, tmp_path: Path) -> None:
        """AC19: The reminder is informational, not blocking."""
        from trw_mcp.tools.ceremony import _learning_reflection_message

        # Both cases should return strings, not raise
        msg_zero = _learning_reflection_message(0)
        msg_positive = _learning_reflection_message(5)
        assert isinstance(msg_zero, str)
        assert isinstance(msg_positive, str)


# -------------------------------------------------------------------------
# Backward Compatibility
# -------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Ensure existing behavior is preserved."""

    def test_existing_nudge_tests_still_pass(self) -> None:
        """compute_nudge still works with all existing state combinations."""
        from trw_mcp.state.ceremony_nudge import compute_nudge

        states = [
            CeremonyState(),
            CeremonyState(session_started=True),
            CeremonyState(session_started=True, files_modified_since_checkpoint=10),
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                review_called=True,
                deliver_called=True,
                phase="done",
            ),
        ]
        for state in states:
            result = compute_nudge(state, available_learnings=5)
            assert isinstance(result, str)

    def test_ceremony_scoring_not_affected(self) -> None:
        """Ceremony scoring reads ceremony-state.json, not status line text."""
        # The scorer reads CeremonyState fields, not the formatted string.
        # Verify the state schema hasn't changed.
        state = CeremonyState()
        assert hasattr(state, "session_started")
        assert hasattr(state, "checkpoint_count")
        assert hasattr(state, "build_check_result")
        assert hasattr(state, "deliver_called")
        assert hasattr(state, "learnings_this_session")
