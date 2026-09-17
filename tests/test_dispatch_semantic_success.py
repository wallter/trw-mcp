"""An empty terminal-envelope answer must never read as a successful dispatch.

A recognized terminal envelope (``{"event": "result", "result": {...}}``) whose
answer field is absent, empty, whitespace-only or the wrong type used to fall
back to ``raw.strip()`` — the NDJSON diagnostic stream itself became the
"answer", so ``DispatchResult.ok`` was True, the background job settled
``succeeded``, and the MCP return then OMITTED raw_stdout/raw_stderr as
redundant. A live agy run that was denied a tool read has already been recorded
as a successful dispatch that way.

These tests pin the whole consumer chain, not just the parser: normalize ->
``DispatchResult.ok`` -> the MCP response shaping -> the background job status.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.dispatch._client_specs import client_spec_for
from trw_mcp.dispatch._jobs import DispatchJob, _reconcile_job
from trw_mcp.dispatch._normalize import normalize_output
from trw_mcp.dispatch._types import SUPPORTED_CLIENTS, DispatchClient, DispatchResult
from trw_mcp.tools.dispatch import _result_payload_capped

# Derived from the registry, not hardcoded: whichever clients declare the
# enveloped shape must all obey this contract (PRD-CORE-266-NFR04).
ENVELOPED_CLIENTS: tuple[DispatchClient, ...] = tuple(
    client for client in SUPPORTED_CLIENTS if client_spec_for(client).output_shape == "enveloped_ndjson_events"
)


def _terminal(**payload: object) -> tuple[str, dict[str, object]]:
    """Build a one-line terminal-envelope stream and the payload it carries."""
    body: dict[str, object] = {"status": "SUCCESS", "conversation_id": "081f54c8", **payload}
    return json.dumps({"event": "result", "result": body}), body


EMPTY_ANSWER_CASES = [
    pytest.param({}, id="answer-field-absent"),
    pytest.param({"response": ""}, id="answer-empty-string"),
    pytest.param({"response": "   \n\t "}, id="answer-whitespace-only"),
    pytest.param({"response": 7}, id="answer-int"),
    pytest.param({"response": None}, id="answer-null"),
    pytest.param({"response": {}}, id="answer-empty-object"),
    pytest.param({"response": [], "text": None, "content": 0}, id="every-answer-field-wrong-type"),
]


def test_the_registry_declares_at_least_one_enveloped_client() -> None:
    """Guards the parametrization below against becoming vacuous."""
    assert ENVELOPED_CLIENTS, "no client declares enveloped_ndjson_events; the cases below assert nothing"


@pytest.mark.parametrize("client", ENVELOPED_CLIENTS)
@pytest.mark.parametrize("answer", EMPTY_ANSWER_CASES)
def test_an_unusable_answer_yields_empty_text_and_keeps_the_payload(
    client: DispatchClient, answer: dict[str, object]
) -> None:
    raw, payload = _terminal(**answer)
    text, structured = normalize_output(client, raw)
    assert text == "", "the raw NDJSON is diagnostics, never the model's answer"
    assert structured == payload, "the terminal payload must still reach the caller as diagnostics"


@pytest.mark.parametrize("client", ENVELOPED_CLIENTS)
@pytest.mark.parametrize("answer", EMPTY_ANSWER_CASES)
def test_an_unusable_answer_makes_the_result_not_ok(client: DispatchClient, answer: dict[str, object]) -> None:
    raw, _ = _terminal(**answer)
    text, structured = normalize_output(client, raw)
    result = _result(client, text=text, structured=structured, raw_stdout=raw)
    assert result.ok is False, "a zero exit with no answer is not a success"


@pytest.mark.parametrize("client", ENVELOPED_CLIENTS)
def test_a_real_answer_is_unchanged_and_still_ok(client: DispatchClient) -> None:
    raw, payload = _terminal(response="OK\n")
    text, structured = normalize_output(client, raw)
    assert text == "OK"
    assert structured == payload
    assert _result(client, text=text, structured=structured, raw_stdout=raw).ok is True


@pytest.mark.parametrize("client", ENVELOPED_CLIENTS)
def test_a_later_answer_field_still_wins_over_an_empty_earlier_one(client: DispatchClient) -> None:
    """Only a fully unusable envelope goes empty; field precedence is untouched."""
    raw, _ = _terminal(response="   ", text="the real answer")
    text, _structured = normalize_output(client, raw)
    assert text == "the real answer"


@pytest.mark.parametrize("client", ENVELOPED_CLIENTS)
def test_the_last_terminal_envelope_still_wins_even_when_it_is_the_empty_one(client: DispatchClient) -> None:
    """A good answer followed by an empty terminal envelope must not be resurrected."""
    first, _ = _terminal(response="stale answer")
    last, last_payload = _terminal(response="")
    text, structured = normalize_output(client, f"{first}\n{last}")
    assert text == ""
    assert structured == last_payload


def test_the_mcp_return_keeps_raw_streams_when_the_answer_was_empty() -> None:
    """Failure is exactly when the operator needs stdout/stderr — they must survive shaping."""
    raw, _ = _terminal(response="")
    result = _result(
        ENVELOPED_CLIENTS[0],
        text="",
        structured=None,
        raw_stdout=raw,
        raw_stderr="permission denied: read_file",
    )
    payload = _result_payload_capped(result)
    assert payload["ok"] is False
    assert payload["raw_stdout"] == raw
    assert payload["raw_stderr"] == "permission denied: read_file"
    assert "raw_streams_omitted" not in payload


def test_a_background_job_with_an_empty_answer_settles_failed_not_succeeded(tmp_path: Path) -> None:
    raw, _ = _terminal(response="")
    job = _persisted_job(tmp_path, "emptyanswer", text="", raw_stdout=raw)
    reconciled = _reconcile_job(job, tmp_path, now=datetime.now(timezone.utc))
    assert reconciled.status == "failed"


def test_a_background_job_with_a_real_answer_still_settles_succeeded(tmp_path: Path) -> None:
    """Control: the same path must still reach ``succeeded`` for a real answer."""
    raw, _ = _terminal(response="OK")
    job = _persisted_job(tmp_path, "realanswer", text="OK", raw_stdout=raw)
    reconciled = _reconcile_job(job, tmp_path, now=datetime.now(timezone.utc))
    assert reconciled.status == "succeeded"


def _result(
    client: DispatchClient,
    *,
    text: str,
    structured: dict[str, object] | None,
    raw_stdout: str,
    raw_stderr: str = "",
) -> DispatchResult:
    """A clean, zero-exit, non-timed-out result — ``ok`` then turns on text alone."""
    return DispatchResult(
        client=client,
        argv_redacted=[client, "<prompt:12 chars>"],
        read_only_enforced=True,
        exit_code=0,
        timed_out=False,
        duration_s=0.4,
        text=text,
        raw_stdout=raw_stdout,
        raw_stderr=raw_stderr,
        structured=structured,
    )


def _persisted_job(jobs_dir: Path, job_id: str, *, text: str, raw_stdout: str) -> DispatchJob:
    """Write a running job record plus its result file, as the detached child would."""
    result = _result(ENVELOPED_CLIENTS[0], text=text, structured=None, raw_stdout=raw_stdout)
    result_path = jobs_dir / f"{job_id}.result.json"
    result_path.write_text(result.model_dump_json(), encoding="utf-8")
    return DispatchJob(
        job_id=job_id,
        client=ENVELOPED_CLIENTS[0],
        status="running",
        created_at=datetime.now(timezone.utc).isoformat(),
        pid=None,
        result_path=str(result_path),
        job_path=str(jobs_dir / f"{job_id}.json"),
    )
