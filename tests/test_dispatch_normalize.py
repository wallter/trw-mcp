"""Behavior tests for per-client output normalization."""

from __future__ import annotations

import json

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


def test_codex_plaintext_takes_trailing_lines() -> None:
    raw = "banner noise\nhook fired\n\nFinding: missing null check\nVerdict: P1"
    text, structured = normalize_output("codex", raw)
    assert "Finding: missing null check" in text
    assert "Verdict: P1" in text
    assert "banner noise" not in text
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


def test_agy_strips_ansi_and_takes_trailing_text() -> None:
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
