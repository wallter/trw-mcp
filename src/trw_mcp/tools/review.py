"""TRW review tool — structured code quality findings artifact.

PRD-QUAL-022: Accepts findings, computes verdict, writes review.yaml.
PRD-QUAL-026: Cross-model review mode — routes diff to external model.
PRD-QUAL-027: Multi-agent parallel review — confidence-scored findings.
Extracted from ceremony.py for single-responsibility.
"""

from __future__ import annotations

import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.state._paths import find_active_run
from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_writer = FileStateWriter()
_events = FileEventLogger(_writer)

# Reviewer roles for multi-agent review (QUAL-027)
REVIEWER_ROLES: tuple[str, ...] = (
    "correctness",
    "security",
    "test-quality",
    "performance",
    "style",
    "spec-compliance",
)


def _get_git_diff() -> str:
    """Get git diff of HEAD, returning empty string on any error."""
    try:
        result = subprocess.run(
            ["git", "diff", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        logger.debug("review_git_diff", length=len(result.stdout))
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _normalize_severity(severity: str) -> str:
    """Map external severity labels to internal severity levels."""
    severity_lower = severity.lower().strip()
    if severity_lower in ("error", "critical", "high"):
        return "critical"
    if severity_lower in ("warning", "medium"):
        return "warning"
    return "info"


def _invoke_cross_model_review(
    diff: str,
    config: object,
) -> list[dict[str, str]]:
    """Invoke cross-model review via external provider.

    This function is the integration point for cross-model review.
    It attempts to call an external code-review service. Since the
    MCP server cannot synchronously call another MCP server, this
    returns an empty list with a preparation note.

    Args:
        diff: The git diff text to review.
        config: TRWConfig instance with cross_model_* fields.

    Returns:
        List of normalized finding dicts (empty until provider is configured).
    """
    if not diff:
        return []

    # Integration point: when code-review-mcp or another provider
    # is configured, this function will route the diff to it.
    # For now, return empty — the cross_model_skipped flag in the
    # caller communicates that no external review was performed.
    return []


def _run_multi_reviewer_analysis(
    diff: str,
    config: object,
) -> dict[str, object]:
    """Run structured multi-perspective code review analysis.

    When called without pre-collected reviewer_findings, performs
    basic structural analysis only. The actual multi-agent spawning
    is handled client-side by the /trw-review-pr skill.

    Args:
        diff: The git diff text to analyze.
        config: TRWConfig instance with review_* fields.

    Returns:
        Dict with reviewer_roles_run, findings, and errors.
    """
    result: dict[str, object] = {
        "reviewer_roles_run": list(REVIEWER_ROLES),
        "reviewer_errors": [],
        "findings": [],
    }

    if not diff:
        return result

    # Basic structural analysis: detect obvious patterns in the diff.
    # Full multi-agent analysis is handled client-side via subagents.
    findings: list[dict[str, object]] = []

    # Check for common issues detectable via diff text analysis
    lines = diff.split("\n")
    for i, line in enumerate(lines):
        # Detect TODO/FIXME/HACK comments in added lines
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            for marker in ("TODO", "FIXME", "HACK", "XXX"):
                if marker in stripped.upper():
                    findings.append({
                        "reviewer_role": "style",
                        "confidence": 60,
                        "category": "placeholder",
                        "severity": "info",
                        "description": f"Placeholder comment detected: {stripped[:80]}",
                        "line": i + 1,
                    })
                    break

    result["findings"] = findings
    return result


def _compute_verdict(findings: list[dict[str, str]]) -> str:
    """Compute review verdict from worst severity across findings."""
    critical_count = sum(1 for f in findings if f.get("severity") == "critical")
    warning_count = sum(1 for f in findings if f.get("severity") == "warning")
    logger.debug(
        "review_findings_count",
        count=len(findings),
        critical=critical_count,
        warnings=warning_count,
    )

    if critical_count > 0:
        return "block"
    if warning_count > 0:
        return "warn"
    return "pass"


def _persist_review(
    resolved_run: Path | None,
    review_data: dict[str, object],
    event_fields: dict[str, object],
) -> str:
    """Write review.yaml and log review_complete event.

    Shared helper for all three review modes (manual, cross_model, auto).

    Args:
        resolved_run: Run directory path, or None if no run active.
        review_data: Full review data dict to write to review.yaml.
        event_fields: Fields to include in the review_complete event.

    Returns:
        Path string to review.yaml, or empty string if no run.
    """
    if resolved_run is None:
        return ""

    review_path = resolved_run / "meta" / "review.yaml"
    _writer.write_yaml(review_path, review_data)

    events_path = resolved_run / "meta" / "events.jsonl"
    if events_path.parent.exists():
        _events.log_event(events_path, "review_complete", event_fields)

    return str(review_path)


def register_review_tools(server: FastMCP) -> None:
    """Register review tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_review(
        findings: list[dict[str, str]] | None = None,
        run_path: str | None = None,
        mode: str | None = None,
        reviewer_findings: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Review code quality and produce structured findings artifact (PRD-QUAL-022).

        Accepts a list of findings (category, severity, description) and computes
        a verdict (pass/warn/block). Writes review.yaml artifact to the run directory.

        Modes:
        - manual: findings=[...] provided directly (backward compatible)
        - auto: multi-reviewer analysis with confidence filtering (QUAL-027)
        - cross_model: routes diff to external model family (QUAL-026)

        Args:
            findings: List of dicts with category, severity, description keys.
            run_path: Explicit run path. Auto-detected if None.
            mode: Review mode — 'manual', 'auto', or 'cross_model'. Auto-detected.
            reviewer_findings: Pre-collected findings from subagent layer (QUAL-027).
        """
        from trw_mcp.models.config import get_config
        from trw_mcp.tools._review_helpers import (
            handle_auto_mode,
            handle_cross_model_mode,
            handle_manual_mode,
        )

        config = get_config()

        # Mode detection:
        # - findings=[...] explicitly passed -> manual (backward compat)
        # - mode explicitly set -> use that mode
        # - reviewer_findings provided (no mode) -> auto
        # - nothing provided -> manual (backward compat with old callers)
        if findings is not None:
            effective_mode = "manual"
        elif mode is not None:
            effective_mode = mode
        elif reviewer_findings is not None:
            effective_mode = "auto"
        else:
            effective_mode = "manual"

        # Resolve run directory
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        # Auto-update phase to REVIEW
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(resolved_run, Phase.REVIEW)

        ts = datetime.now(timezone.utc).isoformat()
        review_id = "review-" + secrets.token_hex(4)

        if effective_mode == "manual":
            return handle_manual_mode(
                findings or [], resolved_run, review_id, ts,
            )

        if effective_mode == "cross_model":
            return handle_cross_model_mode(
                config, resolved_run, review_id, ts,
            )

        # Auto mode (QUAL-027)
        return handle_auto_mode(
            config, resolved_run, review_id, ts, reviewer_findings,
        )


def __reload_hook__() -> None:
    """Reset module-level caches on mcp-hmr hot-reload."""
    global _writer, _events
    _writer = FileStateWriter()
    _events = FileEventLogger(_writer)
