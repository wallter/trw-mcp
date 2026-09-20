"""Behavior tests for per-client output normalization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.dispatch._normalize import normalize_output


def test_claude_json_extracts_result() -> None:
    raw = json.dumps({"result": "The bug is on line 42.", "cost_usd": 0.01})
    text, structured = normalize_output("claude", raw)
    assert text == "The bug is on line 42."
    assert structured is not None and structured["cost_usd"] == 0.01


def test_claude_malformed_json_falls_back_to_raw() -> None:
    raw = "not json at all\n"
    text, structured = normalize_output("claude", raw)
    assert text == "not json at all"
    assert structured is None


def test_claude_json_without_result_key_falls_back() -> None:
    raw = json.dumps({"error": "boom"})
    text, structured = normalize_output("claude", raw)
    # falls back to raw text but still returns the parsed dict as structured
    assert text == raw.strip()
    assert structured == {"error": "boom"}


def test_grok_json_extracts_text_field() -> None:
    raw = json.dumps({"text": "PONG", "stopReason": "end_turn"})
    text, structured = normalize_output("grok", raw)
    assert text == "PONG"
    assert structured is not None and structured["stopReason"] == "end_turn"


def test_a_cancelled_grok_turn_is_a_stop_not_an_answer() -> None:
    """W3 live probe 2026-09-19: a dontAsk turn asked to write ends cancelled.

    grok spells it ``stopReason``; while only ``stop_reason`` was listed, the turn
    read as a completed answer and its text promised a file it never wrote --
    the X-19 defect class, one camelCase key later.
    """
    from trw_mcp.dispatch._normalize import _structured_stop

    cancelled = {"text": "I'll create probe6.txt for you.", "stopReason": "cancelled"}
    assert _structured_stop(cancelled) is True
    assert _structured_stop({"text": "PONG", "stopReason": "end_turn"}) is False


@pytest.mark.parametrize(
    "payload",
    [
        {"stopReason": "cancelled"},
        {"finishReason": "length"},
        {"errorType": "rate_limit"},
        {"isError": True},
    ],
)
def test_camel_case_status_fields_are_read_like_their_snake_case_twins(payload: dict[str, object]) -> None:
    from trw_mcp.dispatch._normalize import _structured_stop

    assert _structured_stop(payload) is True


# W3's captured payload, ids scrubbed. The two fields that matter are the
# camelCase status and the non-empty text: exit 0 plus text that PROMISES a write
# is exactly the shape that read as a finished answer before the aliases landed.
_GROK_CANCELLED: dict[str, object] = {
    "num_turns": 1,
    "stopReason": "cancelled",
    "text": "I'll create `probe3.txt` with the word HELLO.",
    "total_cost_usd": 0.01230188,
}


def test_a_cancelled_grok_run_is_named_a_stop_not_a_silent_success() -> None:
    """The dispatch-level pin: exit 0, non-empty text, and the turn was cancelled."""
    from trw_mcp.dispatch._normalize import classify_silence

    assert (
        classify_silence(
            text=str(_GROK_CANCELLED["text"]),
            raw_stderr="",
            structured=_GROK_CANCELLED,
            exit_code=0,
            timed_out=False,
        )
        == "auth_or_content_stop"
    )


def test_a_completed_grok_run_is_not_a_stop() -> None:
    """Anti-false-positive control for the test above."""
    from trw_mcp.dispatch._normalize import classify_silence

    assert (
        classify_silence(
            text="PONG",
            raw_stderr="",
            structured={"text": "PONG", "stopReason": "end_turn"},
            exit_code=0,
            timed_out=False,
        )
        is None
    )


def test_codex_plaintext_preserves_unstructured_output() -> None:
    raw = "banner noise\nhook fired\n\nFinding: missing null check\nVerdict: P1"
    text, structured = normalize_output("codex", raw)
    assert "Finding: missing null check" in text
    assert "Verdict: P1" in text
    assert text == raw.strip()
    assert structured is None


def test_codex_json_lines_parsed() -> None:
    raw = '{"type":"start"}\n{"type":"final","message":"All good."}\n'
    text, structured = normalize_output("codex", raw)
    assert text == "All good."
    assert structured is not None and structured["type"] == "final"


def test_codex_strips_ansi() -> None:
    raw = "\x1b[32mGreen banner\x1b[0m\nactual answer here"
    text, _ = normalize_output("codex", raw)
    assert "\x1b[" not in text
    assert "actual answer here" in text


def test_opencode_ndjson_concatenates_assistant_text() -> None:
    raw = (
        '{"role":"assistant","text":"First part. "}\n'
        '{"role":"assistant","text":"Second part."}\n'
        '{"role":"tool","text":"ignored tool output"}\n'
    )
    text, structured = normalize_output("opencode", raw)
    assert text == "First part. Second part."
    assert "ignored tool output" not in text
    assert structured is not None


def test_opencode_non_json_falls_back_to_raw() -> None:
    raw = "plain text from opencode\n"
    text, structured = normalize_output("opencode", raw)
    assert text == "plain text from opencode"
    assert structured is None


def test_agy_strips_ansi_and_preserves_text() -> None:
    raw = "\x1b[1mboot\x1b[0m\nline one\nfinal answer line"
    text, structured = normalize_output("agy", raw)
    assert "\x1b[" not in text
    assert "final answer line" in text
    assert structured is None


def test_empty_input_returns_empty_text() -> None:
    for client in ("claude", "codex", "agy", "opencode"):
        text, structured = normalize_output(client, "")  # type: ignore[arg-type]
        assert text == ""
        assert structured is None


def test_plaintext_findings_are_not_truncated_by_line_count() -> None:
    raw = "\n\n".join(
        ["P1: first finding must survive"]
        + [f"P2: additional finding {index}" for index in range(8)]
        + ["Verdict: changes required"]
    )
    for client in ("codex", "agy", "opencode"):
        text, structured = normalize_output(client, raw)  # type: ignore[arg-type]
        assert text == raw
        assert structured is None


def test_event_output_without_extractable_answer_preserves_fallback() -> None:
    raw = '{"type":"start"}\nP1: first finding\n\nP2: second\nEvidence\nVerdict: changes required'
    for client in ("codex", "opencode"):
        text, _ = normalize_output(client, raw)  # type: ignore[arg-type]
        assert text == raw


def test_json_examples_in_plain_reviews_do_not_replace_findings() -> None:
    for client, example in (
        ("codex", '{"result":"ok"}'),
        ("opencode", '{"role":"assistant","text":"ok"}'),
    ):
        for prefix in ("P1: missing authorization check", "{malformed event"):
            raw = f"{prefix}\n{example}\nVerdict: changes required."
            text, structured = normalize_output(client, raw)  # type: ignore[arg-type]
            assert text == raw
            assert structured is None


@pytest.mark.parametrize(
    "client,answer",
    [
        ("codex", '{"type":"final","message":"Done"}'),
        ("opencode", '{"role":"assistant","text":"Done"}'),
    ],
)
@pytest.mark.parametrize(
    "unknown",
    [
        "P1: missing authorization",
        "{malformed",
        '{"finding":"P1: missing authorization"}',
    ],
)
@pytest.mark.parametrize("answer_first", [True, False])
def test_unknown_records_preserve_whole_stream(client: str, answer: str, unknown: str, answer_first: bool) -> None:
    raw = "\n".join([answer, unknown] if answer_first else [unknown, answer])
    text, structured = normalize_output(client, raw)  # type: ignore[arg-type]
    assert text == raw
    assert structured is None


def test_codex_preserves_multiple_answer_records() -> None:
    raw = '{"type":"final","message":"P1: missing authorization"}\n{"type":"final","message":"Done"}'
    text, _ = normalize_output("codex", raw)
    assert "P1: missing authorization" in text and "Done" in text


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('\x1b[32m{"result":"Finding"}\x1b[0m', "Finding"),
        ("\x1b[32mFinding\x1b[0m", "Finding"),
    ],
)
def test_claude_ansi_is_cleaned_before_parse_or_fallback(raw: str, expected: str) -> None:
    assert normalize_output("claude", raw)[0] == expected


@pytest.mark.parametrize(
    "client,record",
    [
        ("codex", {"type": "final", "message": "Done", "finding": "P1"}),
        ("opencode", {"role": "assistant", "text": "Done", "finding": "P1"}),
    ],
)
def test_unknown_fields_are_not_silently_discarded(client: str, record: dict[str, str]) -> None:
    raw = json.dumps(record)
    assert normalize_output(client, raw) == (raw, None)  # type: ignore[arg-type]


def test_parser_exception_fallback_is_ansi_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch import _normalize

    def broken_parser(raw: str) -> tuple[str, dict[str, object] | None]:
        raise ValueError("invalid envelope")

    monkeypatch.setitem(_normalize._NORMALIZERS, "single_json_object", broken_parser)
    assert normalize_output("claude", "\x1b[32mFinding\x1b[0m") == ("Finding", None)


# ── X-19: codex exec --json carries the turn's own verdict ──────────────────

_CODEX_FIXTURE = Path(__file__).parent / "fixtures" / "codex_exec_json_ok.jsonl"


def _measured_codex_stream() -> str:
    return "\n".join(
        line for line in _CODEX_FIXTURE.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    )


def test_the_measured_codex_json_stream_yields_its_answer_and_a_completed_turn() -> None:
    from trw_mcp.dispatch._normalize import classify_silence

    text, structured = normalize_output("codex", _measured_codex_stream())
    assert text == "OK"
    assert structured is not None and structured["stop_reason"] == "completed"
    assert classify_silence(text=text, raw_stderr="", structured=structured, exit_code=0, timed_out=False) is None


def _stream(*events: dict[str, object]) -> str:
    return "\n".join(json.dumps(e) for e in events)


_STARTED = ({"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"})
_MESSAGE = {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Here is a review."}}


# The failure shapes below are SYNTHETIC: codex's schema names turn.failed and error,
# but neither was observed in the one approved probe, so any of them must stop.
@pytest.mark.parametrize(
    "tail",
    [
        ({"type": "turn.failed", "error": {"message": "content policy"}},),
        ({"type": "error", "message": "401 Unauthorized"},),
        (),  # no turn.completed: the stream ended mid-turn
    ],
    ids=["turn.failed", "error", "incomplete"],
)
def test_a_codex_turn_that_did_not_complete_is_a_stop_even_with_answer_text(
    tail: tuple[dict[str, object], ...],
) -> None:
    from trw_mcp.dispatch._normalize import classify_silence

    text, structured = normalize_output("codex", _stream(*_STARTED, _MESSAGE, *tail))
    assert text == "Here is a review."
    verdict = classify_silence(text=text, raw_stderr="", structured=structured, exit_code=0, timed_out=False)
    assert verdict == "auth_or_content_stop"


def test_the_last_agent_message_is_the_answer() -> None:
    first = {"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": "Looking at the diff."}}
    last = {"type": "item.completed", "item": {"id": "b", "type": "agent_message", "text": "Final findings."}}
    tool = {"type": "item.completed", "item": {"id": "c", "type": "command_execution", "command": "ls"}}
    text, structured = normalize_output("codex", _stream(*_STARTED, first, tool, last, {"type": "turn.completed"}))
    assert text == "Final findings." and structured is not None and structured["stop_reason"] == "completed"


def test_output_with_no_codex_event_is_preserved_as_text() -> None:
    raw = "The review found two issues.\nSee above."
    text, structured = normalize_output("codex", raw)
    assert text == raw and structured is None


# Lane B review of X-19, M1: one non-event line must not turn the stream back into
# "raw text", whose non-empty JSON would read as a successful answer.
_BANNER = "WARNING: proceeding with read-only sandbox"


def test_a_non_event_line_in_a_complete_stream_is_a_stop_not_raw_text() -> None:
    """Lead decision: under --json a non-event stdout line is a parse failure, reported as a stop."""
    from trw_mcp.dispatch._normalize import classify_silence

    raw = _BANNER + "\n" + _measured_codex_stream()
    text, structured = normalize_output("codex", raw)
    assert text == "OK", "the answer is the agent_message, never the raw JSONL"
    assert structured is not None and structured["stop_reason"] == "malformed" and structured["ignored_lines"] == 1
    verdict = classify_silence(text=text, raw_stderr="", structured=structured, exit_code=0, timed_out=False)
    assert verdict == "auth_or_content_stop"


@pytest.mark.parametrize("failed", [False, True], ids=["truncated", "truncated+turn.failed"])
def test_a_stream_cut_by_the_runner_cap_is_never_a_success(failed: bool) -> None:
    from trw_mcp.dispatch._normalize import classify_silence
    from trw_mcp.dispatch._runner import _cap_output

    events = [*_STARTED, _MESSAGE]
    if failed:
        events.append({"type": "turn.failed", "error": {"message": "stopped"}})
    body = _stream(*events) + "\n" + '{"type":"item.completed","item":{"type":"agent_message","text":"' + "x" * 64
    capped = _cap_output(body + "y" * 10_000_000)
    assert "…[truncated " in capped
    text, structured = normalize_output("codex", capped)
    assert structured is not None and structured["stop_reason"] == ("failed" if failed else "incomplete")
    verdict = classify_silence(text=text, raw_stderr="", structured=structured, exit_code=0, timed_out=False)
    assert verdict == "auth_or_content_stop"


def test_every_agent_message_is_kept_even_though_the_last_is_the_answer() -> None:
    first = {"type": "item.completed", "item": {"id": "a", "type": "agent_message", "text": "Part one."}}
    last = {"type": "item.completed", "item": {"id": "b", "type": "agent_message", "text": "Part two."}}
    _, structured = normalize_output("codex", _stream(*_STARTED, first, last, {"type": "turn.completed"}))
    assert structured is not None and structured["messages"] == ["Part one.", "Part two."]


def test_an_event_line_with_trailing_prose_is_an_incomplete_stop_not_a_success() -> None:
    from trw_mcp.dispatch._normalize import classify_silence

    raw = '{"type": "turn.started"}\nThe review found two issues.'
    text, structured = normalize_output("codex", raw)
    assert structured is not None and structured["stop_reason"] == "incomplete"
    assert classify_silence(text=text, raw_stderr="", structured=structured, exit_code=0, timed_out=False) is not None


def test_codex_is_launched_with_json_events() -> None:
    from trw_mcp.dispatch._client_specs import client_spec_for

    spec = client_spec_for("codex")
    assert spec.structured_output_argv == ("--json",)
    assert spec.headless_json


def test_codex_turn_outcome_uses_stop_reason_not_the_run_status_word() -> None:
    """X-19 follow-up: the turn outcome is ``stop_reason``, never ``status``.

    ``status`` is the RUN-state vocabulary, whose writers must come from
    ``RunStatus`` (tests/test_run_status_vocabulary.py FR03 scans trw-mcp/src for a
    bare ``["status"] = "<run-status word>"`` and cannot tell a client payload from a
    run writer, so "failed"/"completed" here read as run writers). ``stop_reason`` is
    also what claude reports and what grok spells ``stopReason``, so every client
    shares one turn-outcome field and ``_STATUS_FIELDS`` needs no codex special case.
    """
    from trw_mcp.dispatch._normalize import _structured_stop

    _text, structured = normalize_output("codex", _measured_codex_stream())
    assert structured is not None
    assert structured["stop_reason"] == "completed"
    assert "status" not in structured
    assert _structured_stop(structured) is False

    for value in ("failed", "incomplete", "malformed"):
        assert _structured_stop({"stop_reason": value}) is True, value
