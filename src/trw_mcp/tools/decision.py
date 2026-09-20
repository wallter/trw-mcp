"""trw_decision — opt-in seam onto a calibrated decision backend (trw-jev slice 1).

Off by default on two independent axes: the tool is reachable only when
``decision_enabled`` admits the ``decision_support`` pack onto the session's
surface (mirrors ``comms_enabled``/``dispatch_tools_exposed``), and even then
it answers from :class:`trw_memory.decisions.NullJudge` — no network, no
egress — unless the operator has ALSO set ``TRW_JEV_ENABLED`` and an
``OPENROUTER_API_KEY`` (process env or the project's ``.env``). See the
trw-jev decision-backend design (PRD-CORE-288) for the design and wire
protocol this implements.

The decision itself is always ADVISORY: :class:`~trw_memory.decisions.DecisionJudge`
never raises, and this tool never blocks or gates on its answer — a caller
with no fallback for ``abstained`` has misused the tool.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

import structlog
from fastmcp import Context, FastMCP
from pydantic import TypeAdapter, ValidationError
from trw_memory.decisions import DecisionAnswer, DecisionQuestion, DecisionResult, JevHttpJudge, judge_from_env
from trw_memory.security.pii import strip_pii

from trw_mcp.models.config import get_config
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths import find_active_run, resolve_project_root
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
from trw_mcp.tools._feedback_redaction import _redact_pii

logger = structlog.get_logger(__name__)

#: Hard per-call timeout handed to the judge. Advisory tooling must never hang
#: a caller waiting on a third-party endpoint that is in beta.
_DECIDE_TIMEOUT_SECONDS = 10.0

_QUESTIONS_ADAPTER: TypeAdapter[dict[str, DecisionQuestion]] = TypeAdapter(dict[str, DecisionQuestion])


#: A bare ``Bearer <token>`` (no ``Authorization:`` prefix) that the shared
#: credential redactor leaves alone. Local until R2-014's single redactor lands.
_BARE_BEARER_RE = re.compile(
    r"\b(Bearer|Token)\s+(?:(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,}|[A-Za-z0-9._~+/=-]{16,})",
    re.IGNORECASE,
)

#: A dict key naming a secret: its whole value is dropped, whatever its shape.
_SECRET_KEY_RE = re.compile(
    r"^(?:.*[_-])?(?:pass(?:word|wd|phrase)?|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key"
    r"|client[_-]?secret|credentials?|authorization|auth[_-]?token|bearer)(?:[_-].*)?$",
    re.IGNORECASE,
)


def _is_secret_key(key: str) -> bool:
    """Segment-anchored secret-name match; camelCase (``accessToken``) is split to ``access_Token`` first."""
    return bool(_SECRET_KEY_RE.search(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)))


def _redact_text(text: str) -> str:
    """Credential shapes first (JWT, PEM, Authorization, connection strings, API keys), then PII."""
    return strip_pii(_BARE_BEARER_RE.sub(r"\1 <REDACTED:bearer>", _redact_pii(text)))


#: The value shapes ``_redact_state`` descends into; anything else (int, float,
#: bool, None) has no string content to leak and is passed through unchanged.
_CONTAINERS = (str, dict, list, tuple)


def _unique_key(candidate: str, taken: set[str]) -> str:
    """Disambiguate a redacted key that already exists, deterministically.

    Redaction is many-to-one (``alice@x``/``bob@x`` both become the same
    placeholder), so rewriting keys in place silently DROPS entries — for a
    ``choice`` question that means losing an option after Pydantic's
    ``min_length`` check already passed, and returning an answer labelled with
    something the caller never sent. Suffixing keeps the entry count equal to
    the input's, which is the property callers actually depend on.
    """
    if candidate not in taken:
        return candidate
    suffix = 2
    while f"{candidate}#{suffix}" in taken:
        suffix += 1
    return f"{candidate}#{suffix}"


def _redact_keys(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Redact a mapping's string keys, preserving the entry COUNT (see :func:`_unique_key`)."""
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        redacted[_unique_key(_redact_text(key), set(redacted))] = value
    return redacted


def _redact_state(state: Any) -> Any:
    """Recursively strip credentials and PII from every string leaf of ``state``.

    Every leaf — value OR dict key — goes through the trw-mcp credential
    redactor (the one guarding ``trw_submit_feedback`` egress) and then
    trw-memory's PII stripper. A value under a secret-named key (``password``,
    ``api_key``, ``accessToken``, ...) is replaced whole — string OR container
    (list, tuple, nested dict), since its shape alone may not look like a
    credential. This is deliberately fail-safe: a non-secret string under such a
    key (e.g. ``password_policy``) is dropped too; non-string scalars (counts)
    are kept. A tuple is rendered as a list, matching what JSON egress would do
    with it anyway. Two keys that redact to the same placeholder are kept apart
    by :func:`_unique_key`, so a dict never loses entries to redaction.
    """
    if isinstance(state, str):
        return _redact_text(state)
    if isinstance(state, dict):
        redacted: dict[Any, Any] = {}
        for key, value in state.items():
            if isinstance(key, str) and isinstance(value, _CONTAINERS) and _is_secret_key(key):
                new_value: Any = "<REDACTED:secret>"
            else:
                new_value = _redact_state(value) if isinstance(value, _CONTAINERS) else value
            new_key = (
                _unique_key(_redact_text(key), {k for k in redacted if isinstance(k, str)})
                if isinstance(key, str)
                else key
            )
            redacted[new_key] = new_value
        return redacted
    if isinstance(state, (list, tuple)):
        return [_redact_state(item) if isinstance(item, _CONTAINERS) else item for item in state]
    return state


def _redact_question(question: DecisionQuestion) -> DecisionQuestion:
    """Redact a validated question's ``instructions`` and ``criteria``.

    Both reach the POST body verbatim (``trw_memory.decisions._wire``), so an
    agent that pasted a token into the prompt text — not just into ``state`` —
    would egress it. ``model_copy`` is used rather than re-validation: the
    redactor only ever returns the same JSON shapes the models already accepted,
    and (per :func:`_unique_key`) the same NUMBER of criteria — an invariant
    re-checked here, because a dropped option would silently change the question
    asked rather than fail.
    """
    update: dict[str, Any] = {}
    if isinstance(question.instructions, _CONTAINERS):
        update["instructions"] = _redact_state(question.instructions)
    if question.criteria is not None:
        criteria = _redact_state(question.criteria)
        if len(criteria) != len(question.criteria):  # pragma: no cover - _unique_key makes this unreachable
            raise ValueError("redaction changed the criteria count; refusing to ask a different question")
        update["criteria"] = criteria
    return question.model_copy(update=update) if update else question


def _parse_questions(questions: dict[str, Any]) -> dict[str, DecisionQuestion]:
    """Redact the caller-supplied question IDs, validate, then redact the prose.

    Raises ``ValueError`` on a malformed question (unknown ``type``, missing
    ``instructions``, empty ``criteria``, ...) — fail fast, client-side,
    before any judge is constructed or any state is redacted. Question text AND
    the IDs egress just like ``state`` does (``build_payload`` uses each ID
    verbatim as a JSON key, and they are persisted in the run event), so they
    get the same redaction. IDs are redacted BEFORE validation so that a
    ``ValidationError`` message cannot carry an unredacted one either.
    """
    try:
        parsed = _QUESTIONS_ADAPTER.validate_python(_redact_keys(questions))
    except ValidationError as exc:
        # The error text embeds the offending input; scrub it like any other
        # question prose before it is reflected back to the caller.
        raise ValueError(f"invalid decision questions: {_redact_text(str(exc))}") from exc
    return {question_id: _redact_question(question) for question_id, question in parsed.items()}


def _answers_to_wire(answers: dict[str, DecisionAnswer]) -> dict[str, dict[str, Any]]:
    return {question_id: answer.model_dump() for question_id, answer in answers.items()}


def _log_decision_event(
    ctx: Context | None,
    *,
    question_ids: list[str],
    backend: str,
    latency_ms: float,
    answers: dict[str, dict[str, Any]],
) -> None:
    """Append a decision event to the active run, if one exists.

    Fail-open and NEVER includes the raw state — only question keys, backend,
    latency and the typed answers, mirroring the tool_invocation event shape
    other tools already write via ``FileEventLogger``.
    """
    try:
        run_dir = find_active_run(context=build_call_context(ctx))
        if run_dir is None:
            return
        events_path = run_dir / "meta" / "events.jsonl"
        if not events_path.parent.exists():
            return
        FileEventLogger(FileStateWriter()).log_event(
            events_path,
            "decision",
            {
                "question_ids": question_ids,
                "backend": backend,
                "latency_ms": latency_ms,
                "answers": answers,
            },
        )
    except Exception:  # trw:intentional fail-open — a run-event write must never break the advisory response
        logger.debug("decision_event_log_failed", exc_info=True)


def register_decision_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_decision(
        questions: dict[str, Any],
        state: str | dict[str, Any] | list[Any],
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use when you want a calibrated probability, not prose, for a
        noul/choice/score question about some state. ADVISORY ONLY — never
        gate on it; always keep your own fallback for an abstained answer.
        State is redacted before it leaves the machine.

        Output: status=disabled|answered|abstained, plus answers/model/usage,
        the backend used, and its latency_ms. Abstained means no answer, not
        a denial.
        """
        config = get_config()
        if not getattr(config, "decision_enabled", False):
            return {"status": "disabled"}

        parsed_questions = _parse_questions(questions)
        redacted_state = _redact_state(state)

        judge = judge_from_env(dotenv_path=resolve_project_root() / ".env")
        # Record the backend that was ATTEMPTED, so a failed Jev call is not
        # logged as "null" and hidden from the error-rate evidence (review P1).
        attempted_backend = "jev" if isinstance(judge, JevHttpJudge) else "null"
        started = time.monotonic()
        result: DecisionResult | None = judge.decide(
            redacted_state,
            parsed_questions,
            timeout_s=_DECIDE_TIMEOUT_SECONDS,
        )

        if result is None:
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            _log_decision_event(
                ctx,
                question_ids=list(parsed_questions.keys()),
                backend=attempted_backend,
                latency_ms=elapsed_ms,
                answers={},
            )
            return {
                "status": "abstained",
                "answers": {},
                "model": "",
                "usage": {},
                "backend": attempted_backend,
                "latency_ms": elapsed_ms,
            }

        wire_answers = _answers_to_wire(result.answers)
        _log_decision_event(
            ctx,
            question_ids=list(parsed_questions.keys()),
            backend=result.backend,
            latency_ms=result.latency_ms,
            answers=wire_answers,
        )
        return {
            "status": "answered",
            "answers": wire_answers,
            "model": result.model,
            "usage": result.usage,
            "backend": result.backend,
            "latency_ms": result.latency_ms,
        }


__all__ = ["register_decision_tools"]
