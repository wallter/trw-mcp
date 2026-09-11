"""Behavior tests for per-client output normalization."""

from __future__ import annotations

import json

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
