"""TRW compliance enforcement tool — PRD-QUAL-003.

Audits session compliance against FRAMEWORK.md behavioral requirements:
recall at session start, events logged, reflection at completion,
checkpoints for long sessions, CHANGELOG updates, CLAUDE.md sync.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.compliance import (
    ComplianceDimension,
    ComplianceMode,
    ComplianceReport,
    ComplianceStatus,
    DimensionResult,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._paths import resolve_project_root, resolve_run_path, resolve_trw_dir
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events_logger = FileEventLogger(_writer)


def _load_run_events(
    run_path: str | None,
) -> tuple[list[dict[str, object]], str]:
    """Load events and run_id from a run path.

    Handles both explicit and auto-detected run paths, with graceful
    fallback on errors.

    Args:
        run_path: Explicit run path, or None for auto-detection.

    Returns:
        Tuple of (events list, run_id string).
    """
    events: list[dict[str, object]] = []
    run_id = ""
    try:
        resolved_path = resolve_run_path(run_path)
        events_path = resolved_path / "meta" / _config.events_file
        events = _reader.read_jsonl(events_path)
        run_yaml_path = resolved_path / "meta" / "run.yaml"
        if _reader.exists(run_yaml_path):
            run_data = _reader.read_yaml(run_yaml_path)
            run_id = str(run_data.get("run_id", ""))
    except (StateError, OSError):
        if run_path is not None:
            logger.warning("compliance_run_path_error", run_path=run_path)
    return events, run_id


def register_compliance_tools(server: FastMCP) -> None:
    """Register compliance enforcement tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_compliance_check(
        run_path: str | None = None,
        mode: str = "advisory",
        strictness: str | None = None,
    ) -> dict[str, object]:
        """Audit session compliance against FRAMEWORK.md requirements.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
            mode: Check mode — "advisory" (warnings only) or "gate" (blocking).
            strictness: Override config strictness — "strict", "lenient", or "off".
        """
        resolved_strictness = strictness if strictness is not None else _config.compliance_strictness

        if resolved_strictness == "off":
            return {
                "overall_status": "exempt",
                "compliance_score": 1.0,
                "dimensions": [],
                "mode": mode,
                "message": "Compliance checking is disabled (strictness=off)",
            }

        # Load events (DRY: single path for both explicit and auto-detect)
        events, run_id = _load_run_events(run_path)
        trw_dir = resolve_trw_dir()
        project_root = resolve_project_root()

        # Validate mode for type narrowing
        validated_mode: ComplianceMode = "advisory" if mode != "gate" else "gate"

        # Run each dimension checker
        results: list[DimensionResult] = [
            _check_recall_compliance(events, _config, trw_dir),
            _check_event_compliance(events, _config),
            _check_reflection_compliance(events, _config, validated_mode),
            _check_checkpoint_compliance(events, _config),
            _check_changelog_compliance(events, _config, project_root),
            _check_claude_md_sync_compliance(events, _config),
            _check_framework_docs(project_root, _config),
        ]

        # Compute score
        score, applicable, passing = _compute_compliance_score(results)

        # Determine overall status
        overall = _determine_overall_status(
            score, validated_mode, resolved_strictness, _config,
        )

        timestamp = datetime.now(timezone.utc).isoformat()

        report = ComplianceReport(
            overall_status=ComplianceStatus(overall),
            compliance_score=score,
            dimensions=results,
            mode=validated_mode,
            timestamp=timestamp,
            run_id=run_id,
            applicable_count=applicable,
            passing_count=passing,
        )

        # Persist compliance history
        _persist_compliance_history(report, trw_dir, _config)

        # Log compliance event to run
        if run_path is not None:
            try:
                resolved_for_event = resolve_run_path(run_path)
                _events_logger.log_event(
                    resolved_for_event / "meta" / _config.events_file,
                    "compliance_check",
                    {
                        "score": score,
                        "overall_status": overall,
                        "mode": validated_mode,
                    },
                )
            except (StateError, OSError):
                pass

        logger.info(
            "compliance_check_complete",
            score=score,
            overall_status=overall,
            mode=validated_mode,
        )

        result: dict[str, object] = json.loads(report.model_dump_json())
        return result


def _check_recall_compliance(
    events: list[dict[str, object]],
    config: TRWConfig,
    trw_dir: Path,
) -> DimensionResult:
    """Check if trw_recall was invoked at session start.

    Args:
        events: List of event records from events.jsonl.
        config: TRW configuration.
        trw_dir: Path to .trw/ directory.

    Returns:
        DimensionResult for the recall dimension.
    """
    recall_events = [
        e for e in events
        if str(e.get("event", "")) in ("recall_query", "recall_executed")
    ]

    if recall_events:
        return DimensionResult(
            dimension=ComplianceDimension.RECALL,
            status=ComplianceStatus.PASS,
            message=f"Recall invoked {len(recall_events)} time(s)",
        )

    # Check if learnings directory has receipt files (alternative evidence)
    receipts_dir = trw_dir / config.learnings_dir / config.receipts_dir
    has_receipts = receipts_dir.exists() and any(receipts_dir.iterdir())

    if has_receipts:
        return DimensionResult(
            dimension=ComplianceDimension.RECALL,
            status=ComplianceStatus.PASS,
            message="Recall evidence found via receipt files",
        )

    return DimensionResult(
        dimension=ComplianceDimension.RECALL,
        status=ComplianceStatus.FAIL,
        message="No trw_recall invocation detected at session start",
        remediation="Execute trw_recall('*', min_impact=0.7) at session start",
    )


def _check_event_compliance(
    events: list[dict[str, object]],
    config: TRWConfig,
) -> DimensionResult:
    """Check if structured events are being logged.

    Args:
        events: List of event records from events.jsonl.
        config: TRW configuration.

    Returns:
        DimensionResult for the events dimension.
    """
    if not events:
        return DimensionResult(
            dimension=ComplianceDimension.EVENTS,
            status=ComplianceStatus.FAIL,
            message="No events found in events.jsonl",
            remediation="Use trw_event() to log structured events during work",
        )

    # Count user-driven events (exclude the automatic run_init)
    user_events = [
        e for e in events
        if str(e.get("event", "")) != "run_init"
    ]

    if len(user_events) >= 1:
        return DimensionResult(
            dimension=ComplianceDimension.EVENTS,
            status=ComplianceStatus.PASS,
            message=f"{len(user_events)} event(s) logged (total: {len(events)})",
        )

    return DimensionResult(
        dimension=ComplianceDimension.EVENTS,
        status=ComplianceStatus.WARNING,
        message="Only system events found, no user-driven events",
        remediation="Use trw_event() to log progress during implementation",
    )


def _check_reflection_compliance(
    events: list[dict[str, object]],
    config: TRWConfig,
    mode: ComplianceMode,
) -> DimensionResult:
    """Check if trw_reflect was invoked.

    Args:
        events: List of event records from events.jsonl.
        config: TRW configuration.
        mode: Check mode (advisory or gate).

    Returns:
        DimensionResult for the reflection dimension.
    """
    reflection_events = [
        e for e in events
        if str(e.get("event", "")) in ("reflection_complete", "reflect_executed")
    ]

    if reflection_events:
        return DimensionResult(
            dimension=ComplianceDimension.REFLECTION,
            status=ComplianceStatus.PASS,
            message=f"Reflection invoked {len(reflection_events)} time(s)",
        )

    # In advisory mode, missing reflection is pending (might happen later)
    if mode == "advisory":
        return DimensionResult(
            dimension=ComplianceDimension.REFLECTION,
            status=ComplianceStatus.PENDING,
            message="No reflection detected yet (may occur at session end)",
            remediation="Execute trw_reflect after completing work",
        )

    return DimensionResult(
        dimension=ComplianceDimension.REFLECTION,
        status=ComplianceStatus.FAIL,
        message="No trw_reflect invocation detected",
        remediation="Execute trw_reflect after completing work",
    )


def _check_checkpoint_compliance(
    events: list[dict[str, object]],
    config: TRWConfig,
) -> DimensionResult:
    """Check if checkpoints are being created for long sessions.

    Args:
        events: List of event records from events.jsonl.
        config: TRW configuration.

    Returns:
        DimensionResult for the checkpoint dimension.
    """
    # Short sessions are exempt
    threshold = config.compliance_long_session_event_threshold
    if len(events) < threshold:
        return DimensionResult(
            dimension=ComplianceDimension.CHECKPOINT,
            status=ComplianceStatus.EXEMPT,
            message=f"Short session ({len(events)} events < {threshold} threshold)",
        )

    checkpoint_events = [
        e for e in events
        if str(e.get("event", "")) == "checkpoint"
    ]

    if checkpoint_events:
        return DimensionResult(
            dimension=ComplianceDimension.CHECKPOINT,
            status=ComplianceStatus.PASS,
            message=f"{len(checkpoint_events)} checkpoint(s) created",
        )

    return DimensionResult(
        dimension=ComplianceDimension.CHECKPOINT,
        status=ComplianceStatus.FAIL,
        message=f"Long session ({len(events)} events) with no checkpoints",
        remediation="Use trw_checkpoint() to save state periodically",
    )


def _is_changelog_exempt(events: list[dict[str, object]]) -> DimensionResult | None:
    """Check if changelog dimension is exempt based on event context.

    Args:
        events: List of event records from events.jsonl.

    Returns:
        DimensionResult with EXEMPT status if exempt, None otherwise.
    """
    phase_events = [
        e for e in events
        if str(e.get("event", "")) == "phase_enter"
    ]
    is_implementation = any(
        str(e.get("phase", "")).lower() in ("implement", "deliver")
        for e in phase_events
    )

    # No phase_enter events and no run_init → nothing to check
    has_run_init = any(str(e.get("event", "")) == "run_init" for e in events)
    if not is_implementation and not has_run_init:
        return DimensionResult(
            dimension=ComplianceDimension.CHANGELOG,
            status=ComplianceStatus.EXEMPT,
            message="No implementation phase detected — CHANGELOG not required",
        )

    # Research runs are exempt
    init_events = [e for e in events if str(e.get("event", "")) == "run_init"]
    for ie in init_events:
        if str(ie.get("run_type", "")).lower() == "research":
            return DimensionResult(
                dimension=ComplianceDimension.CHANGELOG,
                status=ComplianceStatus.EXEMPT,
                message="Research run — CHANGELOG update not required",
            )

    return None


def _check_changelog_compliance(
    events: list[dict[str, object]],
    config: TRWConfig,
    project_root: Path,
) -> DimensionResult:
    """Check if CHANGELOG.md has been updated for implementation runs.

    Args:
        events: List of event records from events.jsonl.
        config: TRW configuration.
        project_root: Path to project root directory.

    Returns:
        DimensionResult for the changelog dimension.
    """
    # Check exemptions first (extracted to reduce complexity)
    exempt_result = _is_changelog_exempt(events)
    if exempt_result is not None:
        return exempt_result

    changelog_path = project_root / config.compliance_changelog_filename
    if not changelog_path.exists():
        return DimensionResult(
            dimension=ComplianceDimension.CHANGELOG,
            status=ComplianceStatus.WARNING,
            message=f"{config.compliance_changelog_filename} not found in project root",
            remediation=f"Create {config.compliance_changelog_filename} following Keep a Changelog format",
        )

    try:
        content = changelog_path.read_text(encoding="utf-8")
        if "[unreleased]" in content.lower():
            return DimensionResult(
                dimension=ComplianceDimension.CHANGELOG,
                status=ComplianceStatus.PASS,
                message=f"{config.compliance_changelog_filename} exists with [Unreleased] section",
            )
        return DimensionResult(
            dimension=ComplianceDimension.CHANGELOG,
            status=ComplianceStatus.WARNING,
            message=f"{config.compliance_changelog_filename} exists but no [Unreleased] section found",
            remediation=f"Add [Unreleased] section to {config.compliance_changelog_filename}",
        )
    except OSError:
        return DimensionResult(
            dimension=ComplianceDimension.CHANGELOG,
            status=ComplianceStatus.ERROR,
            message=f"Failed to read {config.compliance_changelog_filename}",
        )


def _check_claude_md_sync_compliance(
    events: list[dict[str, object]],
    config: TRWConfig,
) -> DimensionResult:
    """Check if trw_claude_md_sync was invoked.

    Args:
        events: List of event records from events.jsonl.
        config: TRW configuration.

    Returns:
        DimensionResult for the claude_md_sync dimension.
    """
    sync_events = [
        e for e in events
        if str(e.get("event", "")) in ("claude_md_synced", "claude_md_sync_executed")
    ]

    if sync_events:
        return DimensionResult(
            dimension=ComplianceDimension.CLAUDE_MD_SYNC,
            status=ComplianceStatus.PASS,
            message=f"CLAUDE.md sync invoked {len(sync_events)} time(s)",
        )

    return DimensionResult(
        dimension=ComplianceDimension.CLAUDE_MD_SYNC,
        status=ComplianceStatus.PENDING,
        message="No CLAUDE.md sync detected (expected at delivery)",
        remediation="Execute trw_claude_md_sync at session delivery",
    )


# Expected FRAMEWORK.md section headers for the FRAMEWORK_DOCS dimension.
# These correspond to the Sprint 12 Track A additions plus core sections.
_FRAMEWORK_EXPECTED_SECTIONS: list[str] = [
    "ARCHITECTURE",
    "PHASE REVERSION",
    "REFACTORING WORKFLOW",
    "TESTING STRATEGY",
]


def _check_framework_docs(
    project_root: Path,
    config: TRWConfig,
) -> DimensionResult:
    """Check that FRAMEWORK.md contains expected section headers.

    Validates that key sections exist in FRAMEWORK.md to prevent
    documentation gaps from recurring (Sprint 12 self-referential fix).

    Args:
        project_root: Path to project root directory.
        config: TRW configuration.

    Returns:
        DimensionResult for the framework_docs dimension.
    """
    framework_path = project_root / "FRAMEWORK.md"
    if not framework_path.exists():
        return DimensionResult(
            dimension=ComplianceDimension.FRAMEWORK_DOCS,
            status=ComplianceStatus.WARNING,
            message="FRAMEWORK.md not found in project root",
            remediation="Create FRAMEWORK.md with required sections",
        )

    try:
        content = framework_path.read_text(encoding="utf-8")
    except OSError:
        return DimensionResult(
            dimension=ComplianceDimension.FRAMEWORK_DOCS,
            status=ComplianceStatus.ERROR,
            message="Failed to read FRAMEWORK.md",
        )

    # Extract section headers (## SECTION_NAME)
    found_headers: set[str] = set()
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped.startswith("## "):
            header = stripped[3:].strip().upper()
            found_headers.add(header)

    missing: list[str] = []
    for expected in _FRAMEWORK_EXPECTED_SECTIONS:
        if expected not in found_headers:
            missing.append(expected)

    if not missing:
        return DimensionResult(
            dimension=ComplianceDimension.FRAMEWORK_DOCS,
            status=ComplianceStatus.PASS,
            message=f"All {len(_FRAMEWORK_EXPECTED_SECTIONS)} expected sections found in FRAMEWORK.md",
        )

    return DimensionResult(
        dimension=ComplianceDimension.FRAMEWORK_DOCS,
        status=ComplianceStatus.FAIL,
        message=f"Missing FRAMEWORK.md sections: {', '.join(missing)}",
        remediation=f"Add sections to FRAMEWORK.md: {', '.join(missing)}",
    )


def _compute_compliance_score(
    results: list[DimensionResult],
) -> tuple[float, int, int]:
    """Compute compliance score from dimension results.

    Args:
        results: List of dimension check results.

    Returns:
        Tuple of (score, applicable_count, passing_count).
        Score is 0.0-1.0, exempt dimensions excluded from calculation.
    """
    applicable = [
        r for r in results
        if r.status != ComplianceStatus.EXEMPT
    ]

    if not applicable:
        return 1.0, 0, 0

    passing = [
        r for r in applicable
        if r.status in (ComplianceStatus.PASS, ComplianceStatus.PENDING)
    ]

    score = len(passing) / len(applicable)
    return score, len(applicable), len(passing)


def _determine_overall_status(
    score: float,
    mode: ComplianceMode,
    strictness: str,
    config: TRWConfig,
) -> str:
    """Determine overall compliance status from score and thresholds.

    Args:
        score: Compliance score (0.0-1.0).
        mode: Check mode (advisory or gate).
        strictness: Strictness level (strict, lenient, off).
        config: TRW configuration with threshold values.

    Returns:
        Overall ComplianceStatus value string.
    """
    if score >= config.compliance_pass_threshold:
        return ComplianceStatus.PASS.value

    if score >= config.compliance_warning_threshold:
        if mode == "advisory":
            return ComplianceStatus.WARNING.value
        # In gate mode with strict, warning becomes fail
        if strictness == "strict":
            return ComplianceStatus.FAIL.value
        return ComplianceStatus.WARNING.value

    # Below warning threshold
    if mode == "advisory":
        return ComplianceStatus.WARNING.value
    return ComplianceStatus.FAIL.value


def _persist_compliance_history(
    report: ComplianceReport,
    trw_dir: Path,
    config: TRWConfig,
) -> None:
    """Append compliance report to history JSONL file.

    Args:
        report: Compliance report to persist.
        trw_dir: Path to .trw/ directory.
        config: TRW configuration for path resolution.
    """
    compliance_dir = trw_dir / config.compliance_dir
    try:
        _writer.ensure_dir(compliance_dir)
        history_path = compliance_dir / config.compliance_history_file
        record: dict[str, object] = json.loads(report.model_dump_json())
        _writer.append_jsonl(history_path, record)
    except (StateError, OSError):
        logger.warning("compliance_history_write_failed", trw_dir=str(trw_dir))
