"""TRW auto-checkpoint tools — counter, pre-compact checkpoint.

PRD-CORE-053: Tool call counting and automatic checkpoint creation.
Extracted from ceremony.py for single-responsibility.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import CheckpointResultDict, PreCompactResultDict
from trw_mcp.models.typed_dicts._orchestration import CheckpointRecordDict
from trw_mcp.state._call_context import build_call_context as _build_call_context
from trw_mcp.state._helpers import read_jsonl_resilient
from trw_mcp.state._paths import (
    find_active_run,
    resolve_pin_key,
    resolve_project_root,
)
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter
from trw_mcp.state.pre_compact_marker import write_pre_compact_marker
from trw_mcp.tools._checkpoint_obligations import (
    compute_pending_ceremony as _compute_pending_ceremony,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


# --- Auto-checkpoint state (PRD-CORE-053, Item 3 of PRD-FIX-030) ---


@dataclasses.dataclass(slots=True)
class _CheckpointState:
    """Mutable state for auto-checkpoint counter. Single-process only."""

    counter: int = 0


_checkpoint_state = _CheckpointState()


def _reset_tool_call_counter() -> None:
    """Reset the tool call counter (for testing)."""
    _checkpoint_state.counter = 0


# The ceremony-obligation list lives in _checkpoint_obligations (imported at the
# top of this module). It carried three flat consequence strings that no code
# consulted: build "required before delivery" is false for every non-coding task
# type under the shipped block_coding default, review "recommended" is false the
# other way under review_gate_mode=block, and deliver "required" is enforced
# nowhere. Each is now resolved through the same predicate the deliver path uses.


def _maybe_auto_checkpoint() -> CheckpointResultDict | None:
    """Increment tool call counter; create checkpoint at configured intervals.

    Returns checkpoint info dict if triggered, None otherwise.
    Best-effort: exceptions are swallowed.
    """
    try:
        cfg = get_config()
        if not cfg.auto_checkpoint_enabled:
            return None
        interval = cfg.auto_checkpoint_tool_interval
        if interval <= 0:
            return None

        _checkpoint_state.counter += 1
        if _checkpoint_state.counter % interval != 0:
            return None

        # PRD-FIX-085 FR01: pin-only is correct here -- auto-checkpoint
        # requires a pinned run; without one, the function is a no-op.
        run_dir = find_active_run()  # compat: legacy pin-only no-arg is intentional (PRD-FIX-085)
        if run_dir is None:
            return None

        count = _checkpoint_state.counter
        msg = f"auto-checkpoint after {count} tool calls"
        logger.info("auto_checkpoint_triggered", tool_call_count=count, threshold=interval)
        _do_checkpoint(run_dir, msg)
        logger.debug("checkpoint_created", tool_calls=count, run_dir=str(run_dir))
        return {"auto_checkpoint": True, "tool_calls": count}
    except Exception as _cp_exc:  # justified: fail-open, auto-checkpoint must not disrupt tool flow
        logger.warning("checkpoint_failed", run_id="", error=str(_cp_exc))
        logger.debug("auto_checkpoint_failed", exc_info=True)
        return None


def _do_checkpoint(run_dir: Path, message: str) -> None:
    """Append a checkpoint to the run's checkpoints.jsonl."""
    writer = FileStateWriter()
    events = FileEventLogger(writer)

    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    checkpoint_data: CheckpointRecordDict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "message": message,
    }
    writer.append_jsonl(checkpoints_path, cast("dict[str, object]", checkpoint_data))

    events_path = run_dir / "meta" / "events.jsonl"
    if events_path.parent.exists():
        events.log_event(events_path, "checkpoint", {"message": message})

    logger.info(
        "checkpoint_ok",
        run_id=run_dir.name,
        message=message[:80],
    )
    logger.debug("checkpoint_detail", run_dir=str(run_dir))


# PRD-FIX-061-FR07: implementation relocated to state/candidate_evidence.py
# (state/git_commit_workflow.py consumes it and state must not import tools).
# Re-exported here for back-compat with existing importers.
from trw_mcp.state.candidate_evidence import (  # noqa: E402
    record_candidate_evidence as record_candidate_evidence,
)


def _read_pre_compact_state(run_dir: Path, project_root: Path) -> dict[str, object]:
    """Read PRD scope, phase, and other state from run.yaml."""
    reader = FileStateReader()
    prd_scope: list[str] = []
    phase = ""

    run_yaml = run_dir / "meta" / "run.yaml"
    if run_yaml.exists():
        run_data = reader.read_yaml(run_yaml)
        if isinstance(run_data, dict):
            raw_scope = run_data.get("prd_scope", [])
            if isinstance(raw_scope, list):
                prd_scope = [str(s) for s in raw_scope]
            phase = str(run_data.get("phase", ""))

    return {
        "prd_scope": prd_scope,
        "phase": phase,
        "formation": _resolve_formation_line(run_dir),
    }


def _resolve_formation_line(run_dir: Path) -> str:
    """Name the formation this run belongs to, or state that there is none.

    PRD-CORE-265-FR02. What this replaces probed a retired ownership artifact
    under ``.trw/context/`` — a path that has never existed in this repository —
    and threaded the resulting empty string into a recovery line that therefore
    rendered ``not set`` on every compaction. "We checked and
    found nothing" and "we never checked" were the same sentence, and the
    reassuring one is the one that shipped: exactly the fallback defect in
    ``docs/documentation/wiring-defect-patterns.md``.

    Now there are three distinguishable answers and no silence: the formation
    and this run's member id, an explicit statement that no formation is active,
    or the parse error from a manifest that could not be read (NFR02 — an
    unreadable manifest is never reported as absence).
    """
    from trw_mcp import formation as _formation

    try:
        context = _formation.load(run_dir)
    except _formation.FormationError as exc:
        return f"unresolved — {exc}"
    except Exception as exc:  # justified: recovery capture must never fail on this line
        logger.debug("formation_resolution_degraded", run=str(run_dir), exc_info=True)
        return f"unresolved — {exc}"
    if context is None:
        return "none active"
    who = context.member_id or "orchestrator"
    return f"{context.manifest.formation_id} (this run: {who}); manifest {context.manifest_path}"


def _read_last_events(events_path: Path) -> list[str]:
    """Extract last 5 event types from events.jsonl."""
    import json

    last_5_events: list[str] = []
    if events_path.exists():
        lines = events_path.read_text().strip().split("\n")
        for line in lines[-5:]:
            try:
                evt = json.loads(line)
                last_5_events.append(str(evt.get("event_type", "")))
            except Exception:  # per-item error handling: skip malformed JSONL lines, scan-resilience
                logger.debug("jsonl_line_parse_failed", exc_info=True)
    return last_5_events


def _read_last_checkpoint_message(run_dir: Path) -> str:
    """Last checkpoint message from checkpoints.jsonl (PRD-CORE-165 FR-02).

    Pre-compaction recovery must surface the REAL last checkpoint message — that
    is what the next session reads to resume — not a generic hardcoded literal.
    Falls back to the generic string only when no checkpoint has been written yet.
    """
    import json

    fallback = "pre-compaction safety checkpoint"
    checkpoints_path = run_dir / "meta" / "checkpoints.jsonl"
    if not checkpoints_path.exists():
        return fallback
    try:
        # errors="replace" guards the read itself: a checkpoints.jsonl with
        # non-UTF-8 bytes would otherwise raise UnicodeDecodeError before the
        # try/except and crash pre-compact recovery instead of falling back.
        raw = checkpoints_path.read_text(encoding="utf-8", errors="replace")
        lines = [ln for ln in raw.strip().split("\n") if ln]
        if not lines:
            return fallback
        rec = json.loads(lines[-1])
        message = str(rec.get("message", "")).strip()
        return message or fallback
    except Exception:  # per-item error handling: malformed/undecodable JSONL -> safe fallback
        logger.debug("checkpoint_jsonl_parse_failed", exc_info=True)
        return fallback


def _read_failing_tests(project_root: Path) -> list[str]:
    """Extract failing tests from build-status.yaml."""
    reader = FileStateReader()
    failing_tests: list[str] = []

    build_status_path = project_root / ".trw" / "context" / "build-status.yaml"
    if build_status_path.exists():
        bs_data = reader.read_yaml(build_status_path)
        if isinstance(bs_data, dict):
            raw_ft = bs_data.get("failing_tests", [])
            if isinstance(raw_ft, list):
                failing_tests = [str(t) for t in raw_ft]
    return failing_tests


def _read_ceremony_state(project_root: Path) -> dict[str, object]:
    """Read ceremony state for recovery."""
    import json

    ceremony_state: dict[str, object] = {}
    ceremony_path = project_root / ".trw" / "context" / "ceremony-state.json"
    if ceremony_path.exists():
        try:
            ceremony_state = json.loads(ceremony_path.read_text(encoding="utf-8"))
        except Exception:  # justified: fail-open, ceremony state read
            logger.warning("ceremony_state_read_failed", exc_info=True)
    return ceremony_state


def _write_compact_state(
    project_root: Path,
    run_dir: Path,
    events_path: Path,
    prd_scope: list[str],
    phase: str,
    formation: str,
    failing_tests: list[str],
    ceremony_state: dict[str, object],
    directive: str = "",
    context_anchor: str = "",
    owner_pin_key: str = "",
) -> None:
    """Write pre_compact_state.json with enhanced checkpoint metadata.

    PRD-CORE-165 FR-01: ``directive`` + ``context_anchor`` are caller-supplied
    (they live in the harness conversation, not trw state, so they cannot be
    auto-derived). They are persisted only when non-empty so the next session's
    recovery readback can surface them; the run-derived in-flight position
    (``last_checkpoint`` + ``last_5_events``) is already persisted unconditionally.

    PRD-CORE-258-FR10: ``owner_pin_key`` names the session that compacted, so
    only that session is armed by this marker and only it may clear it. It is a
    PIN KEY, not a FastMCP ``session_id`` — the pin key is the one identifier the
    MCP server and a shell hook can both observe. An empty value writes no owner
    field at all, which keeps the fail-safe blanket behaviour an ownerless
    marker has always had.
    """
    _evt_text = events_path.read_text().strip() if events_path.exists() else ""
    # run_dir + events let the obligation consequences be resolved against the
    # real deliver gate rather than asserted; both are already in hand here.
    pending_ceremony = _compute_pending_ceremony(
        ceremony_state,
        run_dir=run_dir,
        events=read_jsonl_resilient(events_path),
    )

    state_data: dict[str, object] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": "mcp_tool",
        "run_path": str(run_dir),
        "phase": phase,
        "events_logged": len(_evt_text.split("\n")) if _evt_text else 0,
        "last_checkpoint": _read_last_checkpoint_message(run_dir),
        "prd_scope": prd_scope,
        "formation": formation,
        "last_5_events": _read_last_events(events_path),
        "failing_tests": failing_tests,
        "ceremony_state": ceremony_state,
        "pending_ceremony": pending_ceremony,
    }
    if directive:
        state_data["directive"] = directive
    if context_anchor:
        state_data["context_anchor"] = context_anchor
    if owner_pin_key:
        state_data["owner_pin_key"] = owner_pin_key
        # Diagnostic ONLY, and deliberately never consulted by the arming
        # decision: the PreCompact hook runs in a process unrelated to the
        # server, so a pid comparison could never match on that path.
        state_data["owner_pid"] = os.getpid()
    # PRD-CORE-258-FR04: the marker's path and shape have exactly one owner.
    write_pre_compact_marker(state_data, trw_dir=project_root / ".trw")


def _write_compact_instructions(
    cfg: object,
    project_root: Path,
    run_dir: Path,
    phase: str,
    prd_scope: list[str],
    formation: str,
    failing_tests: list[str],
    ceremony_state: dict[str, object],
) -> Path:
    """Write compact_instructions.txt with ceremony recovery guidance."""
    template = cfg.compact_instructions_template  # type: ignore[attr-defined]
    if not template:
        template = (
            "Preserve exactly:\n"
            "- TRW phase: {phase}\n"
            "- TRW run_id: {run_id}\n"
            "- TRW PRD scope: {prd_scope}\n"
            "- Last checkpoint: {last_checkpoint}\n"
            "- TRW formation: {formation}\n"
            "- Failing tests: {failing_tests}\n"
            "DO NOT summarize run artifacts — reference their file paths only.\n"
            "Reference .trw/context/pre_compact_state.json for full state.\n"
            "\n"
            "CEREMONY OBLIGATIONS (complete these before session ends):\n"
            "{ceremony_pending}"
        )
    pending_ceremony = _compute_pending_ceremony(
        ceremony_state,
        run_dir=run_dir,
        events=read_jsonl_resilient(run_dir / "meta" / "events.jsonl"),
    )
    instructions = template.format(
        phase=phase,
        run_id=str(run_dir.name),
        prd_scope=", ".join(prd_scope) if prd_scope else "none",
        last_checkpoint=_read_last_checkpoint_message(run_dir),
        formation=formation or "unresolved — the formation facade returned no answer",
        failing_tests=", ".join(failing_tests) if failing_tests else "none",
        ceremony_pending="\n".join(f"- {s}" for s in pending_ceremony) if pending_ceremony else "- all complete",
    )
    instructions_path = project_root / ".trw" / "context" / "compact_instructions.txt"
    instructions_path.write_text(instructions)
    return instructions_path


def register_checkpoint_tools(server: FastMCP) -> None:
    """Register checkpoint tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_pre_compact_checkpoint(
        directive: str = "",
        context_anchor: str = "",
        ctx: Context | None = None,
    ) -> PreCompactResultDict:
        """Capture a safety checkpoint before the context window compacts.

        Use when the PreCompact hook fires or compaction looks near. directive and
        context_anchor (what you are mid-flight on, and where) cannot be derived
        from run state; the next trw_session_start surfaces them so the session
        resumes exactly.

        Output: status ("success"/"skipped"/"failed"), reason or error, run path,
        artifact paths.
        """
        # Best-effort by design: sub-step failures land in ``status`` rather than
        # raising, so an imminent compaction is never made worse by an exception.
        # Both caller-supplied fields are optional and backward-compatible.
        cfg = get_config()
        if not cfg.auto_checkpoint_pre_compact:
            return {"status": "skipped", "reason": "auto_checkpoint_pre_compact disabled"}

        try:
            # PRD-CORE-141 FR03/FR05: ctx-aware find_active_run suppresses
            # scan fallback for fresh sessions.
            run_dir = find_active_run(context=_build_call_context(ctx))
            if run_dir is None:
                return {"status": "skipped", "reason": "no_active_run"}

            _do_checkpoint(run_dir, "pre-compaction safety checkpoint")

            project_root = resolve_project_root()
            events_path = run_dir / "meta" / "events.jsonl"

            # Read state snapshots
            state_dict = _read_pre_compact_state(run_dir, project_root)
            prd_scope: list[str] = cast("list[str]", state_dict["prd_scope"])
            phase: str = cast("str", state_dict["phase"])
            formation: str = cast("str", state_dict["formation"])

            failing_tests = _read_failing_tests(project_root)
            ceremony_state = _read_ceremony_state(project_root)

            # Write artifacts
            _write_compact_state(
                project_root,
                run_dir,
                events_path,
                prd_scope,
                phase,
                formation,
                failing_tests,
                ceremony_state,
                directive=directive,
                context_anchor=context_anchor,
                owner_pin_key=resolve_pin_key(ctx),
            )
            instructions_path = _write_compact_instructions(
                cfg,
                project_root,
                run_dir,
                phase,
                prd_scope,
                formation,
                failing_tests,
                ceremony_state,
            )

            result: PreCompactResultDict = {
                "status": "success",
                "run_path": str(run_dir),
                "compact_instructions_path": str(instructions_path),
                "prd_scope": prd_scope,
                "failing_tests": failing_tests,
            }
            if directive:
                result["directive"] = directive
            if context_anchor:
                result["context_anchor"] = context_anchor
            return result
        except Exception as exc:  # justified: boundary, compact instructions generation may fail on I/O
            return {"status": "failed", "error": str(exc)}
