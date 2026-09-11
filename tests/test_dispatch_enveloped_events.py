"""Tagged-envelope NDJSON shape — parser + the agy spec that declares it.

The stream fixture below is REAL output captured on 2026-09-11 by running
``agy -p "Reply with exactly: OK" --output-format stream-json`` on this box
against agy 1.2.0, trimmed only in the ``init`` tool list. It is not invented:
the shape exists because a client actually emits it.
"""

from __future__ import annotations

import json

import pytest

from trw_mcp.dispatch._client_specs import client_spec_for
from trw_mcp.dispatch._normalize import normalize_output

# Real capture (tool list trimmed for width; every other byte is verbatim).
AGY_STREAM = "\n".join(
    [
        json.dumps(
            {
                "event": "init",
                "init": {"cwd": "/tmp", "tools": ["view_file"], "permission_mode": "request-review"},
            }
        ),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {"step_index": 1, "state": "ACTIVE", "text_delta": "OK"},
            }
        ),
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": "081f54c8",
                    "status": "SUCCESS",
                    "response": "OK\n",
                    "num_turns": 1,
                    "usage": {"input_tokens": 6353, "output_tokens": 81},
                },
            }
        ),
    ]
)


def test_agy_declares_the_enveloped_shape_and_the_flag_that_produces_it() -> None:
    spec = client_spec_for("agy")
    assert spec.output_shape == "enveloped_ndjson_events"
    assert spec.structured_output_argv == ("--output-format", "stream-json")


def test_real_agy_stream_yields_the_terminal_answer_and_payload() -> None:
    text, structured = normalize_output("agy", AGY_STREAM)
    assert text == "OK"
    assert structured is not None
    # The whole terminal payload is preserved, not just the answer string.
    assert structured["status"] == "SUCCESS"
    assert structured["usage"] == {"input_tokens": 6353, "output_tokens": 81}


def test_intermediate_deltas_are_not_concatenated_onto_the_answer() -> None:
    """The terminal payload carries the answer whole; re-adding deltas doubles it."""
    text, _ = normalize_output("agy", AGY_STREAM)
    assert text.count("OK") == 1


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("plain text, no JSON at all", "non-JSON line"),
        ('{"event": "init", "init": {}}', "no terminal envelope"),
        ('{"text": "flat"}', "untagged (this is the ndjson_events shape, not ours)"),
        ('{"event": 7, "7": {}}', "non-string event name"),
        ('{"event": "result", "result": "not-an-object"}', "terminal payload not a dict"),
        ("{not json}", "malformed JSON"),
    ],
)
def test_unparseable_streams_degrade_to_cleaned_text_and_no_payload(raw: str, why: str) -> None:
    text, structured = normalize_output("agy", raw)
    assert structured is None, f"should not claim a payload: {why}"
    assert text == raw.strip()


def test_a_terminal_envelope_with_no_answer_field_still_returns_its_payload() -> None:
    raw = json.dumps({"event": "result", "result": {"status": "SUCCESS"}})
    text, structured = normalize_output("agy", raw)
    assert structured == {"status": "SUCCESS"}
    assert text == raw.strip()


def test_the_last_terminal_envelope_wins() -> None:
    raw = "\n".join(
        [
            json.dumps({"event": "result", "result": {"response": "first"}}),
            json.dumps({"event": "result", "result": {"response": "second"}}),
        ]
    )
    text, _ = normalize_output("agy", raw)
    assert text == "second"


def test_normalization_never_raises_on_hostile_input() -> None:
    for raw in ("", "\x00\x01", '{"event": "result"}', "{" * 500):
        text, _ = normalize_output("agy", raw)
        assert isinstance(text, str)
