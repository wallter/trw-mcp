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
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
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

logger = structlog.get_logger(__name__)


# ── Deliver helpers ──────────────────────────────────────────────────────

# Threshold for review-scope block gate (R-01): file_modified count above
# which delivery is blocked when no review was run.
REVIEW_SCOPE_FILE_THRESHOLD = 5

# Multiplier for complexity drift detection (R-02/R-05): actual files must
# exceed planned_files * this factor AND exceed REVIEW_SCOPE_FILE_THRESHOLD.
COMPLEXITY_DRIFT_MULTIPLIER = 2


def _read_run_events(run_path: Path, reader: FileStateReader) -> list[dict[str, object]]:
    """Read events.jsonl for a run, returning empty list on any error.

    Centralised helper — called once by ``check_delivery_gates`` and passed
    to individual gate functions so events.jsonl is read at most once.
    """
    events_path = run_path / "meta" / "events.jsonl"
    try:
        if reader.exists(events_path):
            return reader.read_jsonl(events_path)
    except Exception:  # justified: fail-open, event read must not block delivery
        logger.warning("run_events_read_failed", run_path=str(run_path), exc_info=True)
    return []


def _count_file_modified(events: list[dict[str, object]]) -> int:
    """Count ``file_modified`` events in a pre-read event list."""
    return sum(1 for ev in events if str(ev.get("event", "")) == "file_modified")


def _events_since_last_session_start(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return events after the last ``session_start``; returns all on no boundary."""
    last_session_idx = -1
    for i, ev in enumerate(events):
        if str(ev.get("event", "")) == "session_start":
            last_session_idx = i
    return events if last_session_idx < 0 else events[last_session_idx:]


def _count_file_modified_current_session(events: list[dict[str, object]]) -> int:
    """Count ``file_modified`` events in the current session only.

    Uses ``session_start`` as the session boundary marker. Events from
    previous sessions (before the last ``session_start``) are excluded.
    """
    session_events = _events_since_last_session_start(events)
    return _count_file_modified(session_events)


def _read_run_yaml(run_path: Path, reader: FileStateReader) -> dict[str, object]:
    """Read run.yaml, returning empty dict on any error."""
    run_yaml_path = run_path / "meta" / "run.yaml"
    try:
        if run_yaml_path.exists():
            return reader.read_yaml(run_yaml_path)
    except Exception:  # justified: fail-open, run.yaml read must not block delivery
        logger.warning("run_yaml_read_failed", run_path=str(run_path), exc_info=True)
    return {}


def _read_complexity_class(run_path: Path, reader: FileStateReader) -> str:
    """Read the complexity_class from run.yaml, or return empty string."""
    run_data = _read_run_yaml(run_path, reader)
    return str(run_data.get("complexity_class", ""))


def _check_complexity_drift(
    run_data: dict[str, object],
    events: list[dict[str, object]],
) -> str | None:
    """Detect when actual work scope significantly exceeds the initial classification.

    Uses pre-read ``run_data`` and ``events`` (shared with other gate checks)
    so events.jsonl is read only once per delivery.

    Fires a WARNING (not a block) when:
      - ``complexity_class`` is ``MINIMAL``
      - actual file_modified count > REVIEW_SCOPE_FILE_THRESHOLD
      - actual count > COMPLEXITY_DRIFT_MULTIPLIER * planned files

    Returns:
        A warning string if complexity drift is detected, or None.
    """
    try:
        complexity_class = str(run_data.get("complexity_class", ""))
        if complexity_class != "MINIMAL":
            return None

        signals = run_data.get("complexity_signals")
        if not isinstance(signals, dict):
            return None
        planned_files = int(str(signals.get("files_affected", 0)))

        actual_files = _count_file_modified_current_session(events)

        if actual_files > REVIEW_SCOPE_FILE_THRESHOLD and actual_files > COMPLEXITY_DRIFT_MULTIPLIER * planned_files:
            logger.info(
                "complexity_drift_detected",
                complexity_class=complexity_class,
                planned_files=planned_files,
                actual_files=actual_files,
            )
            return (
                f"Complexity drift detected: classified MINIMAL "
                f"({planned_files} files planned) but {actual_files} files "
                f"were modified. Consider re-evaluating — tasks of this scope "
                f"typically require STANDARD complexity with mandatory REVIEW phase."
            )

    except Exception:  # justified: fail-open, complexity drift check must not block delivery
        logger.warning("complexity_drift_check_failed", exc_info=True)

    return None


def _check_review_gate(
    run_path: Path,
    reader: FileStateReader,
) -> tuple[str | None, str | None, str | None]:
    """Check review gate and return (block, warning, advisory) if found.

    A ``review.yaml`` with ``verdict=block`` + critical findings is the deepest
    truthfulness gate — it exists to catch false completions. For STANDARD /
    COMPREHENSIVE runs that block verdict is promoted to a hard ``block`` so
    ``trw_deliver`` actually refuses to ship (overridable via
    ``allow_unverified`` — CONSTITUTION Deliver Gate Path 3). For MINIMAL /
    light complexity the historical advisory ``warning`` is retained so trivial
    work is not over-blocked.
    """
    # trw:intentional verdict=block on STANDARD+ runs must HARD-BLOCK deliver — a
    # block review is the primary truthfulness gate; downgrading it to a warning
    # (the pre-fix behavior) let critical-finding deliveries return success=True.
    block: str | None = None
    warning: str | None = None
    advisory: str | None = None

    review_path = run_path / "meta" / "review.yaml"
    if review_path.exists():
        try:
            review_data = reader.read_yaml(review_path)
            rv_verdict = str(review_data.get("verdict", ""))
            rv_critical = int(str(review_data.get("critical_count", 0)))
            if rv_verdict == "block" and rv_critical > 0:
                complexity_class = _read_complexity_class(run_path, reader)
                if complexity_class in ("STANDARD", "COMPREHENSIVE"):
                    block = (
                        f"Review verdict is 'block' with {rv_critical} critical finding(s) "
                        f"(complexity: {complexity_class}). Delivery blocked. Fix the critical "
                        "review findings before delivering, or — only for a documented "
                        "acceptable failure — retry with allow_unverified=true and a concrete "
                        "unverified_reason."
                    )
                else:
                    # MINIMAL/light complexity: keep the historical soft warning so
                    # trivial work is not over-blocked.
                    warning = (
                        f"Review has {rv_critical} critical findings. "
                        f"Delivery proceeding but review issues should be addressed."
                    )
        except Exception:  # justified: fail-open, review gate check must not block delivery
            logger.warning("maintenance_review_gate_failed", exc_info=True)
    else:
        # Check complexity — STANDARD+ tasks MUST have review (Sprint 68 enforcement)
        complexity_class = _read_complexity_class(run_path, reader)
        if complexity_class in ("STANDARD", "COMPREHENSIVE"):
            warning = (
                f"No trw_review was run before delivery (complexity: {complexity_class}). "
                "Review is MANDATORY for STANDARD+ tasks — adversarial audit catches "
                "false completions that self-review misses. "
                "Run trw_review() or /trw-audit before delivering."
            )
        else:
            advisory = "No trw_review was run before delivery. Consider running trw_review for quality assurance."

    return block, warning, advisory


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
        if review_path.exists():
            return None

        file_modified_count = _count_file_modified_current_session(events)

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
        mode = config.effective_tool_exposure_mode
        if mode == "all":
            # All tools exposed — no parity mismatch possible
            return None

        exposed = resolve_exposed_tools(
            mode=mode,
            custom_list=config.tool_exposure_list,
        )

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
        build_warning = _check_no_active_run_build_gate(trw_dir, reader)
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

    # Integration review gate (PRD-INFRA-027-FR06)
    int_block, int_warning = _check_integration_review_gate(run_path, reader)
    if int_block:
        result["integration_review_block"] = int_block
    elif int_warning:
        result["integration_review_warning"] = int_warning

    # Review scope block — hard gate when >5 files modified without review (R-01)
    review_scope_block = _check_review_file_count_gate(run_path, events)
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

    # Build gate and work events (uses shared events list)
    build_warning, premature_warning = _check_build_and_work_events(events)
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
    drift_warning = _check_complexity_drift(run_data, events)
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
