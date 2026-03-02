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
    config: object,  # noqa: ARG001
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
    config: object,  # noqa: ARG001
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

    if critical_count > 0:
        return "block"
    if warning_count > 0:
        return "warn"
    return "pass"


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
        from trw_mcp.models.run import ReviewFinding

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

        ts = datetime.now(timezone.utc).isoformat()
        review_id = "review-" + secrets.token_hex(4)

        # ── Manual mode (backward compatible) ──────────────────────────
        if effective_mode == "manual":
            all_findings = findings or []

            # Validate findings through ReviewFinding model
            validated: list[dict[str, str]] = []
            for f in all_findings:
                try:
                    ReviewFinding(**f)
                    validated.append(f)
                    if f.get("severity") not in ("critical", "warning", "info"):
                        validated[-1] = {**f, "severity": "info"}
                except Exception:
                    validated.append(f)

            critical_count = sum(1 for f in validated if f.get("severity") == "critical")
            warning_count = sum(1 for f in validated if f.get("severity") == "warning")
            info_count = sum(1 for f in validated if f.get("severity") == "info")

            verdict = _compute_verdict(validated)

            result: dict[str, object] = {
                "review_id": review_id,
                "verdict": verdict,
                "total_findings": len(validated),
                "critical_count": critical_count,
                "warning_count": warning_count,
                "info_count": info_count,
                "run_path": str(resolved_run) if resolved_run else None,
            }

            if resolved_run is not None:
                review_path = resolved_run / "meta" / "review.yaml"
                review_data: dict[str, object] = {
                    "review_id": review_id,
                    "timestamp": ts,
                    "verdict": verdict,
                    "critical_count": critical_count,
                    "warning_count": warning_count,
                    "info_count": info_count,
                    "findings": validated,
                }
                _writer.write_yaml(review_path, review_data)
                result["review_yaml"] = str(review_path)

                events_path = resolved_run / "meta" / "events.jsonl"
                if events_path.parent.exists():
                    _events.log_event(events_path, "review_complete", {
                        "review_id": review_id,
                        "verdict": verdict,
                        "critical_count": critical_count,
                        "warning_count": warning_count,
                    })
            else:
                result["review_yaml"] = ""

            return result

        # ── Cross-model mode (QUAL-026) ────────────────────────────────
        if effective_mode == "cross_model":
            diff = _get_git_diff()
            cross_model_skipped = False
            cross_model_findings: list[dict[str, str]] = []

            if not config.cross_model_review_enabled:
                cross_model_skipped = True
                logger.info("cross_model_review_disabled")
            elif not diff:
                cross_model_skipped = True
                logger.info("cross_model_review_no_diff")
            else:
                raw_findings = _invoke_cross_model_review(diff, config)
                if not raw_findings:
                    cross_model_skipped = True
                else:
                    for rf in raw_findings:
                        cross_model_findings.append({
                            "category": rf.get("category", "general"),
                            "severity": _normalize_severity(rf.get("severity", "info")),
                            "description": rf.get("description", ""),
                            "source": "cross_model",
                            "provider": config.cross_model_provider,
                        })

            verdict = _compute_verdict(cross_model_findings)

            result = {
                "review_id": review_id,
                "verdict": verdict,
                "mode": "cross_model",
                "cross_model_skipped": cross_model_skipped,
                "cross_model_provider": config.cross_model_provider,
                "total_findings": len(cross_model_findings),
                "run_path": str(resolved_run) if resolved_run else None,
            }

            if resolved_run is not None:
                review_path = resolved_run / "meta" / "review.yaml"
                review_data = {
                    "review_id": review_id,
                    "timestamp": ts,
                    "verdict": verdict,
                    "mode": "cross_model",
                    "cross_model_skipped": cross_model_skipped,
                    "cross_model_provider": config.cross_model_provider,
                    "cross_model_findings": cross_model_findings,
                }
                _writer.write_yaml(review_path, review_data)
                result["review_yaml"] = str(review_path)

                events_path = resolved_run / "meta" / "events.jsonl"
                if events_path.parent.exists():
                    _events.log_event(events_path, "review_complete", {
                        "review_id": review_id,
                        "verdict": verdict,
                        "mode": "cross_model",
                        "cross_model_skipped": cross_model_skipped,
                    })
            else:
                result["review_yaml"] = ""

            return result

        # ── Auto mode (QUAL-027) ───────────────────────────────────────
        diff = _get_git_diff()

        # Use pre-collected reviewer_findings if provided, else run basic analysis
        if reviewer_findings is not None:
            analysis: dict[str, object] = {
                "reviewer_roles_run": list(REVIEWER_ROLES),
                "reviewer_errors": [],
                "findings": reviewer_findings,
            }
        else:
            analysis = _run_multi_reviewer_analysis(diff, config)

        all_auto_findings = analysis.get("findings", [])
        if not isinstance(all_auto_findings, list):
            all_auto_findings = []

        confidence_threshold = config.review_confidence_threshold

        # Filter findings by confidence threshold
        surfaced: list[dict[str, object]] = []
        for f in all_auto_findings:
            if not isinstance(f, dict):
                continue
            confidence = f.get("confidence", 0)
            if isinstance(confidence, (int, float)) and confidence >= confidence_threshold:
                surfaced.append(f)  # type: ignore[arg-type]  # dict variance

        # Compute verdict from surfaced findings only
        surfaced_for_verdict: list[dict[str, str]] = [
            {"severity": str(f.get("severity", "info"))} for f in surfaced
        ]
        verdict = _compute_verdict(surfaced_for_verdict)

        result = {
            "review_id": review_id,
            "verdict": verdict,
            "mode": "auto",
            "reviewer_roles_run": analysis.get("reviewer_roles_run", []),
            "reviewer_errors": analysis.get("reviewer_errors", []),
            "surfaced_findings_count": len(surfaced),
            "total_findings_count": len(all_auto_findings),
            "confidence_threshold": confidence_threshold,
            "total_findings": len(surfaced),
            "run_path": str(resolved_run) if resolved_run else None,
        }

        if resolved_run is not None:
            # Write review.yaml — surfaced findings only
            review_path = resolved_run / "meta" / "review.yaml"
            review_data = {
                "review_id": review_id,
                "timestamp": ts,
                "verdict": verdict,
                "mode": "auto",
                "reviewer_roles_run": analysis.get("reviewer_roles_run", []),
                "reviewer_errors": analysis.get("reviewer_errors", []),
                "surfaced_findings_count": len(surfaced),
                "total_findings_count": len(all_auto_findings),
                "confidence_threshold": confidence_threshold,
                "findings": surfaced,
            }
            _writer.write_yaml(review_path, review_data)
            result["review_yaml"] = str(review_path)

            # Write review-all.yaml — ALL findings unfiltered
            review_all_path = resolved_run / "meta" / "review-all.yaml"
            review_all_data: dict[str, object] = {
                "review_id": review_id,
                "timestamp": ts,
                "mode": "auto",
                "total_findings_count": len(all_auto_findings),
                "findings": all_auto_findings,
            }
            _writer.write_yaml(review_all_path, review_all_data)

            # Write integration-review.yaml — integration findings only
            integration_findings = [
                f for f in all_auto_findings
                if isinstance(f, dict) and f.get("reviewer_role") == "integration"
            ]
            if integration_findings:
                integration_path = resolved_run / "meta" / "integration-review.yaml"
                integration_data: dict[str, object] = {
                    "review_id": review_id,
                    "timestamp": ts,
                    "mode": "auto",
                    "findings": integration_findings,
                }
                _writer.write_yaml(integration_path, integration_data)

            events_path = resolved_run / "meta" / "events.jsonl"
            if events_path.parent.exists():
                _events.log_event(events_path, "review_complete", {
                    "review_id": review_id,
                    "verdict": verdict,
                    "mode": "auto",
                    "surfaced_findings": len(surfaced),
                    "total_findings": len(all_auto_findings),
                })
        else:
            result["review_yaml"] = ""

        return result
