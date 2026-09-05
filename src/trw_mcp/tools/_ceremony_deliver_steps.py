"""Deliver-tool step helpers — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Three step helpers used by ``trw_deliver``:

- ``unpack_gate_result`` — copy delivery-gate verdict keys to the typed
  result dict.
- ``step_clear_score`` — compute + persist PRD-HPO-MEAS-001 FR-5 CLEAR
  score for the closing session.
- ``log_deliver_complete`` — emit deliver_ok / deliver_failed /
  trw_deliver_complete log lines.

Extracted as DIST-243 batch 64 to push parent ``ceremony.py`` away from
the 717-LOC top-of-list violator position.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.state._helpers import read_jsonl_resilient
from trw_mcp.state._session_changelog import (
    SessionChangelogResult as SessionChangelogResult,
)
from trw_mcp.state._session_changelog import (
    build_session_changelog as build_session_changelog,
)
from trw_mcp.state._session_changelog import (
    write_session_changelog as write_session_changelog,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from trw_mcp.models.plan_acceptance import AcceptanceStatus
    from trw_mcp.models.typed_dicts import DeliverResultDict, DeliveryGatesDict

logger = structlog.get_logger(__name__)

_GATE_KEYS: tuple[str, ...] = (
    "review_block",
    "review_warning",
    "review_advisory",
    "review_nudge",
    "integration_review_block",
    "integration_review_warning",
    "untracked_warning",
    "build_gate_warning",
    "build_gate_block",
    "build_gate_override",
    "warning",
    "review_scope_block",
    "checkpoint_blocker_warning",
    "complexity_drift_warning",
)


def unpack_gate_result(gate_result: DeliveryGatesDict, results: DeliverResultDict) -> None:
    """Copy delivery-gate verdict keys from ``gate_result`` to ``results``.

    Each key in :data:`_GATE_KEYS` is conditionally promoted onto the typed
    result dict so callers see only populated fields.
    """
    for key in _GATE_KEYS:
        if key in gate_result:
            results[key] = gate_result[key]  # type: ignore[literal-required]


def step_clear_score(resolved_run: Path, results: DeliverResultDict) -> None:
    """PRD-HPO-MEAS-001 FR-5 — compute + persist CLEAR score for the run.

    One record per closed session; failure is fail-open so the scorer
    never blocks deliver completion.
    """
    try:
        from trw_mcp.scoring.clear import load_and_score_run

        session_id = str(resolved_run.name)
        clear_score = load_and_score_run(session_id, resolved_run)
        if clear_score is None:
            return
        clear_path = resolved_run / "meta" / "session_clear_score.json"
        clear_path.write_text(
            json.dumps(clear_score.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        results["clear_score"] = cast("dict[str, object]", clear_score.model_dump(mode="json"))
        logger.info(
            "clear_score_persisted",
            session_id=session_id,
            cost=clear_score.cost,
            latency=clear_score.latency,
            efficacy=clear_score.efficacy,
            assurance=clear_score.assurance,
            reliability=clear_score.reliability,
        )
    except Exception:  # justified: fail-open — CLEAR scoring must not block deliver
        logger.debug("clear_score_step_failed", exc_info=True)


def step_knowledge_sync(trw_dir: Path, results: DeliverResultDict) -> None:
    """PRD-FIX-COMPOUNDING-2 FR03 — auto-trigger knowledge-graph topic sync.

    After the core deliver logic succeeds, populate ``.trw/knowledge/`` from
    graph data when the entry count meets ``knowledge_sync_threshold``.
    ``execute_knowledge_sync`` already short-circuits below threshold, so the
    result is surfaced under the ``knowledge_sync`` key either way. Fail-open
    (NFR02): a sync failure must NOT fail the deliver — it is recorded as
    ``{"status": "failed", ...}`` instead.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state.knowledge_topology import execute_knowledge_sync

        config = get_config()
        sync_result = execute_knowledge_sync(trw_dir, config, dry_run=False)
        results["knowledge_sync"] = sync_result
    except Exception as exc:  # justified: fail-open — knowledge sync must not block deliver
        logger.warning("deliver_knowledge_sync_failed", error=str(exc), exc_info=True)
        results["knowledge_sync"] = {"status": "failed", "error": str(exc)}
        return

    # F5 suggestion 2: opportunistic, TIME-BOXED graph backfill on deliver.
    # Builds edges for entries still lacking them, capped by the configured
    # deadline so deliver latency stays bounded. Uses the singleton connection
    # (WAL gives reader/writer isolation). Fail-open like the topic sync above.
    if not config.deliver_graph_backfill_enabled:
        return
    try:
        from trw_mcp.state.memory_adapter import backfill_graph

        results["graph_backfill"] = backfill_graph(
            trw_dir,
            deadline_seconds=config.deliver_graph_backfill_deadline_seconds,
        )
    except Exception:  # justified: fail-open — graph backfill must not block deliver
        logger.warning("deliver_graph_backfill_failed", exc_info=True)


def step_session_changelog(resolved_run: Path, results: DeliverResultDict) -> None:
    """PRD-LOCAL-049 FR01/FR02/FR03 — write the session changelog artifact.

    Writes ``<resolved_run>/reports/session-changelog.md`` from durable run
    events + git evidence and records the path under ``session_changelog_path``.
    The package-changelog advisory (FR03) is surfaced under
    ``package_changelog_advisory`` only when the project policy enables it
    (``changelog_advisory_enabled``); v1 is advisory-only and NEVER fails
    delivery. Fail-open (CONSTITUTION §1): any failure logs and records
    ``{"status": "failed", ...}`` but never blocks deliver.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.tools import ceremony as _ceremony

        config = get_config()
        resolve_trw_dir_fn = vars(_ceremony)["resolve_trw_dir"]
        trw_dir = cast("Path", resolve_trw_dir_fn())
        advisory_enabled = bool(getattr(config, "changelog_advisory_enabled", False))
        changelog_filename = str(getattr(config, "compliance_changelog_filename", "CHANGELOG.md"))
        report_path, changelog = write_session_changelog(
            resolved_run,
            trw_dir,
            changelog_filename=changelog_filename,
            changelog_advisory_enabled=advisory_enabled,
        )
        results["session_changelog_path"] = str(report_path)
        if advisory_enabled:
            results["package_changelog_advisory"] = [
                {
                    "package_root": cov.package_root,
                    "changed_files": cov.changed_files,
                    "changelog_path": cov.changelog_path,
                    "changelog_updated": cov.changelog_updated,
                }
                for cov in changelog.package_changelog_advisory
            ]
    except Exception as exc:  # justified: fail-open — session changelog must not block deliver
        logger.warning("deliver_session_changelog_failed", error=str(exc), exc_info=True)
        results["session_changelog"] = {"status": "failed", "error": str(exc)}


#: PRD-CORE-249-FR05 markers for the managed remaining-work section in
#: ``{RUN_ROOT}/reports/final.md``. Same whole-line marker discipline as the
#: FR02 handoff block, so a human-authored final report survives around it.
REMAINING_WORK_START_MARKER = "<!-- trw:remaining-work:start -->"
REMAINING_WORK_END_MARKER = "<!-- trw:remaining-work:end -->"


def _render_remaining_work(rows: Sequence[AcceptanceStatus], handoff_path: str) -> str:
    """Render the managed ``## Remaining work handoff`` section.

    When nothing was deferred the section says so explicitly rather than being
    absent: an absent section is indistinguishable from a step that did not run,
    which is the ambiguity the un-suppressible escalation exists to remove.
    """
    from trw_mcp.tools._project_handoff import neutralise

    lines = [
        REMAINING_WORK_START_MARKER,
        "",
        "## Remaining work handoff",
        "",
    ]
    if rows:
        lines.append(f"These items were declared blocked and now live in `{handoff_path}`:")
        lines.append("")
        lines.append("| Gate | Class | Owner | Reason |")
        lines.append("| --- | --- | --- | --- |")
        for row in rows:
            owner = neutralise(row.owner) or "-"
            reason = neutralise(row.reason) or "-"
            lines.append(f"| {neutralise(row.gate_id)} | {neutralise(row.blocking_class)} | {owner} | {reason} |")
    else:
        lines.append("No work was deferred by this run.")
    lines.extend(["", REMAINING_WORK_END_MARKER])
    return "\n".join(lines)


def step_project_handoff(resolved_run: Path, results: DeliverResultDict) -> None:
    """PRD-CORE-249-FR02 + FR05 — durable handoff rows, and the run's own record.

    Runs after the gate cascade passed and the critical synchronous steps
    completed. Re-evaluates the plan-acceptance declaration (two small bounded
    file reads, so the gate itself stays free of side effects), merges every
    accepted-blocked row into the configured project handoff file keyed on
    ``(run_id, gate_id)``, and writes the managed ``## Remaining work handoff``
    section into ``reports/final.md``.

    Fail-open (NFR02), modelled on :func:`step_session_changelog`: a failure
    records ``project_handoff: {"status": "failed", ...}`` and delivery still
    succeeds. It is never absent-meaning-succeeded.
    """
    try:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._plan_acceptance_gate import evaluate_plan_acceptance
        from trw_mcp.tools._project_handoff import merge_marked_section, resolve_handoff_path, write_handoff_rows

        run_yaml = resolved_run / "meta" / "run.yaml"
        run_data = FileStateReader().read_yaml(run_yaml) if run_yaml.is_file() else {}
        run_id = str(run_data.get("run_id") or resolved_run.name)
        outcome = evaluate_plan_acceptance(resolved_run, run_data)
        accepted = list(outcome.accepted_blocked)
        unmet_ids = {status.gate_id for status in outcome.unmet}
        accepted_ids = {status.gate_id for status in accepted}
        # Everything enumerated that is neither unmet nor accepted-blocked was
        # declared satisfied — its row for THIS run is resolved and removed.
        resolved_ids = [g for g in outcome.enumerated if g not in unmet_ids and g not in accepted_ids]
        status = write_handoff_rows(run_id=run_id, accepted=accepted, resolved_gate_ids=resolved_ids)
        results["project_handoff"] = status
        handoff_path = str(status.get("path") or resolve_handoff_path())
        final_md = resolved_run / "reports" / "final.md"
        existing = final_md.read_text(encoding="utf-8") if final_md.is_file() else ""
        merged = merge_marked_section(
            existing,
            _render_remaining_work(accepted, handoff_path),
            REMAINING_WORK_START_MARKER,
            REMAINING_WORK_END_MARKER,
        )
        final_md.parent.mkdir(parents=True, exist_ok=True)
        final_md.write_text(merged, encoding="utf-8")
    except Exception as exc:  # justified: fail-open — the handoff write must not block deliver
        logger.warning("deliver_project_handoff_failed", error=str(exc), exc_info=True)
        results["project_handoff"] = {"status": "failed", "error": str(exc)}


def log_deliver_complete(
    *,
    resolved_run: Path | None,
    results: DeliverResultDict,
    errors: list[str],
    deferred_status: str,
    critical_elapsed: float,
) -> None:
    """Emit deliver_ok / deliver_failed / trw_deliver_complete log lines.

    Reads events.jsonl from the run dir for the events_logged field when
    available; missing/unreadable counts fall back to 0.
    """
    run_id = str(resolved_run.name) if resolved_run else ""
    events_jsonl = resolved_run / "meta" / "events.jsonl" if resolved_run else None
    # events.jsonl is read here only for the advisory events_logged count on the
    # deliver_ok line. The strict reader raises StateError on a torn concurrent
    # append, which would abort deliver-completion logging and break the
    # docstring's "unreadable counts fall back to 0" contract; the resilient
    # reader honors it by dropping the torn line (returns [] when missing).
    events_logged = len(read_jsonl_resilient(events_jsonl)) if events_jsonl else 0
    if not errors:
        logger.info(
            "deliver_ok",
            run_id=run_id,
            task=str(results.get("run_path", "")),
            events_logged=events_logged,
        )
    else:
        logger.warning("deliver_failed", run_id=run_id, errors=errors)
    if deferred_status == "skipped_already_running":
        logger.warning("deliver_deferred", reason="background_thread_running")
    logger.info(
        "trw_deliver_complete",
        critical_steps=results.get("critical_steps_completed"),
        deferred=deferred_status,
        critical_elapsed=critical_elapsed,
        errors=len(errors),
    )
