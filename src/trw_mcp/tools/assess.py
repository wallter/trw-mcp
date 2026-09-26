"""trw_assess — batch-first, opt-in access to a calibrated typed-decision backend (trw-jev).

Off by default on two independent axes: the tool is reachable only when ``assess_enabled``
admits the ``assess_support`` pack onto the session's surface (mirrors ``comms_enabled``), and
even then every outcome is a ``disabled`` failure — no network, no egress — unless the backend is
enabled by one of (first explicit wins, see ``trw_memory.decisions._enablement``): the process env
``TRW_JEV_ENABLED``; project scope (``assess_enabled`` in the project's ``.trw/config.yaml``, or
``TRW_JEV_ENABLED`` in its ``.env``); or the operator's own ``~/.trw/config.yaml`` machine switch
(2026-09-23 operator decision — a project may now enable the backend, not only disable it). The
key stays per-repo regardless: ``OPENROUTER_API_KEY`` from the env or the project ``.env``, and the
base URL is still restricted to an allowlisted host — enabling from project scope never redirects
where the key is sent. Design and measurements: the trw-jev decision-backend research (PRD-CORE-288).

The surface is the wire protocol's own shape — one ``state``, a map of typed ``questions`` —
because that is the shape worth encouraging: every question about a state in ONE call is ~10x
cheaper per question than asking singly, and types mix freely. ``items`` extends the same call
to many items (the toolkit's embedded schema, which tracks solo-call answers within ~0.06).

Everything here is ADVISORY. The toolkit never raises for a provider problem; it returns a typed
failure per question. It DOES raise for a caller error (unknown type, over-cap options, noul
criteria not keyed true/false), because that is a bug to fix, not a condition to fall back on.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from fastmcp import Context, FastMCP
from trw_memory.decisions import redact_state, toolkit_from_env
from trw_memory.decisions.toolkit import AskResult, InvalidRequest

from trw_mcp.models.config import get_config
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths import find_active_run, resolve_project_root
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
from trw_mcp.telemetry.anonymizer import redact_secrets

logger = structlog.get_logger(__name__)

#: Budget for the whole call, retry included. Advisory tooling must never hang a caller on a beta endpoint.
_DECIDE_TIMEOUT_SECONDS = 10.0


def _redact_state(state: Any) -> Any:
    """Whole-value redaction with trw-mcp's redactor: what the toolkit applies to state, questions and items."""
    return redact_state(state, redact_secrets)


def _unique_key(candidate: str, taken: dict[str, Any]) -> str:
    """Disambiguate a redacted key deterministically so redaction never drops an entry (N2).

    Kept local rather than imported from trw-memory: the packages release separately and a
    six-line helper is not worth a minimum-version coupling at the seam.
    """
    if candidate not in taken:
        return candidate
    suffix = 2
    while f"{candidate}#{suffix}" in taken:
        suffix += 1
    return f"{candidate}#{suffix}"


def _redact_ids(mapping: dict[str, Any]) -> dict[str, Any]:
    """Re-key a per-question mapping by redacted ids without losing entries (release-verify N1/N2)."""
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        out[_unique_key(redact_secrets(key), out)] = value
    return out


def _log_assess_event(
    ctx: Context | None,
    *,
    question_ids: list[str],
    backend: str,
    model: str,
    latency_ms: float,
    outcomes: dict[str, Any],
) -> None:
    """Append an ``assess`` event to the active run. Never includes the state."""
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
            # model: `jev-latest` floats, so the served id is what lets a later audit see a model change.
            {
                "question_ids": question_ids,
                "backend": backend,
                "model": model,
                "latency_ms": latency_ms,
                "answers": outcomes,
            },
        )
    except Exception:  # trw:intentional fail-open — a run-event write must never break the advisory response
        logger.debug("decision_event_log_failed", exc_info=True)


def _provenance(calls: list[AskResult], *, attempted_backend: str) -> dict[str, Any]:
    """Model, usage, backend and latency across the calls made, each call counted once."""
    usage: dict[str, Any] = {}
    for call in calls:
        for key, value in call.usage.items():
            usage[key] = usage.get(key, 0) + value if isinstance(value, int | float) else value
    return {
        "model": next((c.model for c in calls if c.model), ""),
        "usage": usage,
        "backend": next((c.backend for c in calls if c.backend), attempted_backend),
        "latency_ms": round(sum(c.latency_ms for c in calls), 1),
    }


def register_assess_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_assess(
        questions: dict[str, Any],
        state: str | dict[str, Any],
        items: dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use when triaging, grading, routing or ranking. ONE call, every question (mix
        types); items={"key": state} screens many states with the same questions. state:
        facts plus known operator prefs, not your lean. Each question needs "instructions" (the question text) and,
        per type, "criteria": noul -> {"true": "..", "false": ".."} (exactly those two
        keys); choice -> {"opt-a": "rubric", "opt-b": "rubric"} (options live only here,
        never a separate "options" list); score -> ["low", "mid", "high"] (ordered levels).
        Example: {"q1": {"type": "noul", "instructions": "..", "criteria": {"true": "..",
        "false": ".."}}, "q2": {"type": "choice", "instructions": "..", "criteria": {"a": "..",
        "b": ".."}}, "q3": {"type": "score", "instructions": "..", "criteria": ["low", "high"]}}.
        Advisory: branch on probability/margin, never gate; near-tie carries "advice".
        """
        config = get_config()
        if not getattr(config, "assess_enabled", False):
            return {"status": "disabled"}

        project_root = resolve_project_root()
        kit = toolkit_from_env(
            dict(os.environ),
            redactor=redact_secrets,
            dotenv_path=project_root / ".env",
            project_root=project_root,
            timeout_s=_DECIDE_TIMEOUT_SECONDS,
        )
        # By class name: importing JevHttpJudge here would load httpx on the disabled path (PRD-CORE-295 FR01).
        attempted_backend = "jev" if type(kit.judge).__name__ == "JevHttpJudge" else "null"

        try:
            if items is not None:
                if not items:
                    raise InvalidRequest("items was given but is empty; omit it to ask about state alone")
                # state is the shared context for every item, whatever its shape (a dict is kept, not dropped).
                batch = kit.batch_items(items, questions, context=state)
                per_item = {k: r.to_wire() for k, r in batch.per_item.items()}
                # Every item in a chunk carries that chunk's provenance; report it once per call.
                calls = list({c: batch.per_item[k] for k, c in batch.chunk_of.items()}.values())
                rendered = _provenance(calls, attempted_backend=attempted_backend)
                _log_assess_event(
                    ctx,
                    question_ids=[redact_secrets(f"{k}.{q}") for k in items for q in questions],
                    backend=rendered["backend"],
                    model=rendered["model"],
                    latency_ms=rendered["latency_ms"],
                    outcomes={redact_secrets(k): _redact_ids(o) for k, o in per_item.items()},
                )
                return {"status": batch.status, "items": per_item, "unanswered": batch.unanswered, **rendered}
            result = kit.ask(state, questions)
        except InvalidRequest as exc:
            # The toolkit (trw-memory's decisions._models — the deep module where DecisionQuestion is
            # parsed) already built a message naming the field and a worked example; redact and pass through.
            raise ValueError(redact_secrets(str(exc))) from exc

        rendered = {
            "status": result.status,
            "outcomes": result.to_wire(),
            **_provenance([result], attempted_backend=attempted_backend),
        }
        _log_assess_event(
            ctx,
            question_ids=[redact_secrets(q) for q in questions],
            backend=rendered["backend"],
            model=rendered["model"],
            latency_ms=result.latency_ms,
            outcomes=_redact_ids(rendered["outcomes"]),
        )
        return rendered


__all__ = ["register_assess_tools"]
