"""Delivery gate and finalization helpers for ceremony.py — trw_deliver logic.

Extracted from _ceremony_helpers.py to keep modules under the 500-line gate.

Public API (re-exported by _ceremony_helpers.py):
- check_delivery_gates: orchestrate all delivery gate checks
- finalize_run: post-delivery finalization (currently no-op placeholder)
- copy_compliance_artifacts: copy review artifacts to compliance retention dir
- REVIEW_SCOPE_FILE_THRESHOLD: constant for review-scope block gate
- COMPLEXITY_DRIFT_MULTIPLIER: constant for complexity drift detection

Internal helpers (also re-exported for test access):
- _read_run_events, _count_file_modified, _read_run_yaml, _read_complexity_class
- _check_complexity_drift, _check_review_gate, _check_integration_review_gate
- _check_untracked_files, _check_review_file_count_gate
- _check_checkpoint_blocker_gate, _check_build_and_work_events
- _review_gate_mode_is_block, _review_nudge_for_run (re-exported from
  _delivery_review_gate; PRD-CORE-192)
"""

# Event-check facade imports are intentionally late to avoid a cycle.
# ruff: noqa: E402, I001

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config import (
    get_config as get_config,  # re-exported: _delivery_review_gate + tests resolve get_config through this facade
)
from trw_mcp.models.typed_dicts import (
    ComplianceArtifactsDict,
    DeliveryGatesDict,
    FinalizeRunResult,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

# PRD-CORE-184-FR03: task-type-aware deliver gate mode lives in a focused
# sibling. Re-exported here so callers/tests have a single import point.
from trw_mcp.tools._deliver_gate_mode import (
    apply_deliver_gate_mode as _apply_deliver_gate_mode,
)
from trw_mcp.tools._deliver_gate_mode import (
    resolve_deliver_gate_decision as resolve_deliver_gate_decision,
)
from trw_mcp.tools._delivery_build_gates import (
    _check_build_and_work_events as _check_build_and_work_events,
)
from trw_mcp.tools._delivery_build_gates import (
    _check_no_active_run_build_gate as _check_no_active_run_build_gate,
)

# PRD-CORE-192: review_gate_mode escalation + pre-deliver REVIEW nudge helpers
# live in a focused sibling. Re-exported here so callers/tests keep a single
# import point (_delivery_helpers). The sibling resolves get_config /
# _read_complexity_class through THIS facade so test monkeypatches propagate.
from trw_mcp.tools._delivery_review_gate import (
    _check_review_gate as _check_review_gate,
)
from trw_mcp.tools._delivery_review_gate import (
    _review_artifact_is_substantive as _review_artifact_is_substantive,
)
from trw_mcp.tools._delivery_review_gate import (
    _review_data_is_substantive as _review_data_is_substantive,
)
from trw_mcp.tools._delivery_review_gate import (
    _review_gate_mode_is_block as _review_gate_mode_is_block,
)
from trw_mcp.tools._delivery_review_gate import (
    _review_nudge_for_run as _review_nudge_for_run,
)

# PRD-CORE-213: acceptance-integrity gate helpers live in focused siblings.
# Re-exported here for a single import point. Both siblings only import leaf
# modules (persistence) at load time and defer everything else, so this facade
# import introduces no cycle. FR-group A (review provenance):
from trw_mcp.tools._prd_transition_gate import (
    check_transition_coherence as check_transition_coherence,
)
from trw_mcp.tools._prd_transition_gate import (
    detect_status_transitions as detect_status_transitions,
)
from trw_mcp.tools._prd_transition_gate import (
    evaluate_transition_gate as evaluate_transition_gate,
)
from trw_mcp.tools._review_provenance import (
    classify_review_independence as classify_review_independence,
)
from trw_mcp.tools._review_provenance import (
    review_receipt_satisfied as review_receipt_satisfied,
)

logger = structlog.get_logger(__name__)


# ── Deliver helpers ──────────────────────────────────────────────────────

# Threshold for review-scope block gate (R-01): file_modified count above
# which delivery is blocked when no review was run.
REVIEW_SCOPE_FILE_THRESHOLD = 5

# Multiplier for complexity drift detection (R-02/R-05): actual files must
# exceed planned_files * this factor AND exceed REVIEW_SCOPE_FILE_THRESHOLD.
COMPLEXITY_DRIFT_MULTIPLIER = 2


from trw_mcp.tools._delivery_event_checks import (
    _check_complexity_drift as _check_complexity_drift,
    _count_file_modified as _count_file_modified,
    _count_file_modified_current_session as _count_file_modified_current_session,
    _events_since_last_session_start as _events_since_last_session_start,
    _normalize_event_path as _normalize_event_path,
    _project_root_from_run as _project_root_from_run,
    _read_complexity_class as _read_complexity_class,
    _read_run_events as _read_run_events,
    _read_run_yaml as _read_run_yaml,
)


def _check_integration_review_gate(
    run_path: Path,
    reader: FileStateReader,
) -> tuple[str | None, str | None]:
    """Check integration review gate and return (block, warning) if found."""
    block: str | None = None
    warning: str | None = None

    integration_path = run_path / "meta" / "integration-review.yaml"
    if integration_path.exists():
        try:
            int_data = reader.read_yaml(integration_path)
            int_verdict = str(int_data.get("verdict", ""))
            if int_verdict == "block":
                raw_findings = int_data.get("findings", [])
                int_findings = raw_findings if isinstance(raw_findings, list) else []
                critical_list = [f for f in int_findings if isinstance(f, dict) and f.get("severity") == "critical"]
                block = (
                    f"Integration review verdict is 'block' with {len(critical_list)} critical finding(s). "
                    f"Delivery blocked. Fix critical integration issues before delivering."
                )
            elif int_verdict == "warn":
                warning = "Integration review has warnings. Review findings before merging."
        except Exception:  # justified: fail-open, integration review check must not block delivery
            logger.warning("maintenance_integration_review_failed", exc_info=True)

    return block, warning


def _check_untracked_files(run_path: Path) -> str | None:
    """Check for untracked source/test files and return warning if found."""
    try:
        import subprocess

        git_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],  # noqa: S607 — git is a well-known VCS tool; all args are static literals, no user input
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(run_path.parent.parent.parent),  # project root
        )
        if git_result.returncode == 0:
            untracked = [
                f
                for f in git_result.stdout.strip().splitlines()
                if f
                and (f.endswith((".py", ".ts", ".tsx")))
                and ("/src/" in f or "/tests/" in f or f.startswith(("src/", "tests/")))
            ]
            if untracked:
                return (
                    f"{len(untracked)} untracked source/test file(s) detected. "
                    f"These won't be included in commits: {', '.join(untracked[:5])}"
                    + (f" (+{len(untracked) - 5} more)" if len(untracked) > 5 else "")
                )
    except Exception:  # justified: fail-open, untracked file detection is advisory only
        logger.debug("untracked_file_check_failed", exc_info=True)

    return None


def _check_review_file_count_gate(
    run_path: Path,
    events: list[dict[str, object]],
    session_id: str | None = None,
) -> str | None:
    """Block delivery when >REVIEW_SCOPE_FILE_THRESHOLD file_modified events and no review (R-01).

    Uses session-scoped counting: only ``file_modified`` events after the last
    ``session_start`` boundary are counted. This prevents stale events from
    previous sessions (which may have accumulated many file modifications)
    from blocking delivery in a new session that only changed a few files.

    Uses pre-read ``events`` list (shared with other gate checks).

    Fail-open: if anything goes wrong, returns None.
    """
    try:
        review_path = run_path / "meta" / "review.yaml"
        if _review_artifact_is_substantive(review_path, FileStateReader()):
            return None

        file_modified_count = _count_file_modified_current_session(
            events,
            _project_root_from_run(run_path),
            session_id,
        )

        if file_modified_count > REVIEW_SCOPE_FILE_THRESHOLD:
            return (
                f"Delivery blocked: {file_modified_count} files modified but no review was run. "
                f"Tasks modifying >{REVIEW_SCOPE_FILE_THRESHOLD} files require trw_review() before delivery. "
                "Run trw_review() or /trw-audit before delivering."
            )
    except Exception:  # justified: fail-open — review scope gate must not block delivery on errors
        logger.warning("review_file_count_gate_failed", exc_info=True)

    return None


def _check_checkpoint_blocker_gate(
    run_path: Path,
    reader: FileStateReader,
) -> str | None:
    """Warn when last checkpoint message contains 'blocker' keyword (R-07).

    Reads checkpoints.jsonl, checks the LAST entry. If its message field
    contains 'blocker' (case-insensitive), returns a warning. Otherwise None.

    Fail-open: if anything goes wrong reading checkpoints, returns None.
    """
    try:
        checkpoints_path = run_path / "meta" / "checkpoints.jsonl"
        if not reader.exists(checkpoints_path):
            return None

        checkpoints = reader.read_jsonl(checkpoints_path)
        if not checkpoints:
            return None

        last_checkpoint = checkpoints[-1]
        message = str(last_checkpoint.get("message", ""))

        if "blocker" in message.lower():
            return f"Last checkpoint mentions a blocker: '{message}'. Verify the blocker is resolved before delivering."
    except Exception:  # justified: fail-open — checkpoint blocker gate must not block delivery on errors
        logger.warning("checkpoint_blocker_gate_failed", exc_info=True)

    return None


def _check_instruction_tool_parity_gate(run_path: Path) -> str | None:
    """R-08: Check instruction-tool parity — soft warning gate (PRD-CORE-135).

    Reads AGENTS.md from the project root and compares tool mentions against
    the effective tool exposure list from config. Returns a warning string
    if unexposed tools are mentioned, None if clean.

    Fail-open: returns None on any error so delivery is not blocked.
    """
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state.claude_md._tool_manifest import (
            check_instruction_tool_parity,
            resolve_exposed_tools,
        )

        config = get_config()
        mode = config.tool_resolution_mode
        if mode == "all":
            # Full eligible surface exposed — no parity mismatch possible
            return None

        exposed = resolve_exposed_tools(mode=mode)

        # Walk up from run_path to find project root (parent of .trw/)
        project_root = run_path
        for parent in run_path.parents:
            if (parent / ".trw").is_dir():
                project_root = parent
                break

        return check_instruction_tool_parity(project_root, exposed)
    except Exception:  # justified: fail-open, soft warning gate must not block delivery
        logger.warning("instruction_parity_gate_failed", exc_info=True)
        return None


def check_delivery_gates(
    run_path: Path | None,
    reader: FileStateReader,
    trw_dir: Path | None = None,
    session_id: str | None = None,
) -> DeliveryGatesDict:
    """Check review/build gates and premature delivery guard.

    Returns a dict with any warnings/advisories found:
      - review_block: verdict=block + critical findings on STANDARD/COMPREHENSIVE (hard block)
      - review_warning: critical review findings present (MINIMAL/light, soft)
      - review_advisory: no review was run
      - review_scope_block: >5 files modified without review (R-01, hard block)
      - checkpoint_blocker_warning: last checkpoint mentions 'blocker' (R-07, soft gate)
      - build_gate_warning: no successful build check found
      - warning: premature delivery (only ceremony events)
    """
    result: DeliveryGatesDict = {}

    if run_path is None:
        build_warning = _check_no_active_run_build_gate(trw_dir, reader, session_id=session_id)
        if build_warning:
            result["build_gate_warning"] = build_warning
        return result

    # Read shared data once — avoids reading events.jsonl 3x and run.yaml 2x
    events = _read_run_events(run_path, reader)
    run_data = _read_run_yaml(run_path, reader)

    # Review gate (PRD-QUAL-022). A verdict=block + critical findings on a
    # STANDARD/COMPREHENSIVE run is a HARD block (the primary truthfulness gate),
    # surfaced as review_block; MINIMAL/light complexity keeps the soft warning.
    review_block, review_warning, review_advisory = _check_review_gate(run_path, reader)
    if review_block:
        result["review_block"] = review_block
    elif review_warning:
        result["review_warning"] = review_warning
    elif review_advisory:
        result["review_advisory"] = review_advisory

    # PRD-CORE-192-FR04: pre-deliver REVIEW nudge — surfaced for any STANDARD+
    # run with no review.yaml, regardless of review_gate_mode, so the prompt is
    # prominent rather than buried in the gate warning above.
    review_nudge = _review_nudge_for_run(run_path, reader)
    if review_nudge:
        result["review_nudge"] = review_nudge

    # Integration review gate (PRD-INFRA-027-FR06)
    int_block, int_warning = _check_integration_review_gate(run_path, reader)
    if int_block:
        result["integration_review_block"] = int_block
    elif int_warning:
        result["integration_review_warning"] = int_warning

    # Review scope block — hard gate when >5 files modified without review (R-01)
    review_scope_block = _check_review_file_count_gate(run_path, events, session_id)
    if review_scope_block:
        result["review_scope_block"] = review_scope_block

    # Checkpoint blocker warning — soft gate (R-07)
    checkpoint_blocker = _check_checkpoint_blocker_gate(run_path, reader)
    if checkpoint_blocker:
        result["checkpoint_blocker_warning"] = checkpoint_blocker

    # Untracked files
    untracked_warning = _check_untracked_files(run_path)
    if untracked_warning:
        result["untracked_warning"] = untracked_warning

    # Build gate and work events (uses shared events list).
    #
    # Scope asymmetry (intentional): the build gate is SESSION-GLOBAL — it
    # passes the FULL ``events`` list so ANY recorded passing ``trw_build_check``
    # in this run's history satisfies it. The review-scope (R-01) and complexity
    # drift gates are SESSION-LOCAL (they slice via
    # ``_count_file_modified_current_session``). This is deliberate: a build
    # check is run-global validation evidence whose validity does not expire at
    # a session boundary, whereas review-scope/drift are about THIS session's
    # change surface. The ``allow_unverified`` override still gates the whole
    # cascade, so this never weakens truthfulness — it only avoids forcing a
    # redundant re-run of a still-valid build across a session boundary.
    build_warning, premature_warning = _check_build_and_work_events(events)
    # PRD-CORE-205-FR05: content-bound build staleness. When the latest per-run
    # BuildReceipt's bound bytes changed after the check, surface a content-stale
    # warning even if timestamps did not order the edit after the build. Prefer
    # the content-bound reason over the timestamp-only message when both fire.
    from trw_mcp.tools._delivery_build_gates import build_receipt_content_stale_warning

    content_stale_warning = build_receipt_content_stale_warning(run_path)
    if content_stale_warning:
        build_warning = content_stale_warning
    if build_warning:
        result["build_gate_warning"] = build_warning
    if premature_warning:
        result["warning"] = premature_warning

    # PRD-CORE-184-FR03: task-type-aware deliver gate mode. Promote the
    # advisory build_gate_warning to a structural block when the configured
    # mode + the run's task_type require it. Fail-open on any error so the
    # gate never wedges delivery.
    if build_warning:
        _apply_deliver_gate_mode(result, run_data)

    # Complexity drift detection (R-02 + R-05, uses shared events + run_data)
    drift_warning = _check_complexity_drift(run_data, events, session_id)
    if drift_warning:
        result["complexity_drift_warning"] = drift_warning

    # Instruction-tool parity (R-08, soft warning — PRD-CORE-135-FR03)
    instruction_parity = _check_instruction_tool_parity_gate(run_path)
    if instruction_parity:
        result["instruction_parity_warning"] = instruction_parity

    return result


def finalize_run(*_args: object, **_kwargs: object) -> FinalizeRunResult:
    """Post-delivery finalization — currently a no-op placeholder."""
    return {}


def copy_compliance_artifacts(
    run_path: Path | None,
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
    writer: FileStateWriter,
) -> ComplianceArtifactsDict:
    """Copy review artifacts to compliance retention directory (INFRA-027-FR05)."""
    result: ComplianceArtifactsDict = {}
    if run_path is None:
        return result
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    compliance_dir = trw_dir / config.compliance_dir / "reviews" / str(now.year) / f"{now.month:02d}" / run_path.name
    artifacts = ["review.yaml", "review-all.yaml", "integration-review.yaml"]
    copied = []
    for artifact_name in artifacts:
        src = run_path / "meta" / artifact_name
        if reader.exists(src):
            try:
                data = reader.read_yaml(src)
                writer.ensure_dir(compliance_dir)
                writer.write_yaml(compliance_dir / artifact_name, data)
                copied.append(artifact_name)
            except Exception:  # justified: fail-open, compliance artifact copy is best-effort
                logger.warning("maintenance_compliance_copy_failed", exc_info=True)

    if copied:
        result["compliance_artifacts_copied"] = copied
        result["compliance_dir"] = str(compliance_dir)

    return result
