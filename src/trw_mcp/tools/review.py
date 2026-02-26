"""TRW review tool — structured code quality findings artifact.

PRD-QUAL-022: Accepts findings, computes verdict, writes review.yaml.
Extracted from ceremony.py for single-responsibility.
"""

from __future__ import annotations

import secrets
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


def register_review_tools(server: FastMCP) -> None:
    """Register review tools on the MCP server."""

    @server.tool()
    @log_tool_call
    def trw_review(
        findings: list[dict[str, str]] | None = None,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Review code quality and produce structured findings artifact (PRD-QUAL-022).

        Accepts a list of findings (category, severity, description) and computes
        a verdict (pass/warn/block). Writes review.yaml artifact to the run directory.

        Args:
            findings: List of dicts with category, severity, description keys.
            run_path: Explicit run path. Auto-detected if None.
        """
        from trw_mcp.models.run import ReviewFinding

        all_findings = findings or []

        # Validate findings through ReviewFinding model
        validated: list[dict[str, str]] = []
        for f in all_findings:
            try:
                rf = ReviewFinding(**f)
                validated.append(f)
                # Warn on unrecognized severity
                if rf.severity not in ("critical", "warning", "info"):
                    validated[-1] = {**f, "severity": "info"}
            except Exception:
                validated.append(f)  # Pass through on validation failure

        # Resolve run directory
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        # Count by severity
        critical_count = sum(1 for f in validated if f.get("severity") == "critical")
        warning_count = sum(1 for f in validated if f.get("severity") == "warning")
        info_count = sum(1 for f in validated if f.get("severity") == "info")

        # Compute verdict
        if critical_count > 0:
            verdict = "block"
        elif warning_count > 0:
            verdict = "warn"
        else:
            verdict = "pass"

        # Generate review ID
        ts = datetime.now(timezone.utc).isoformat()
        review_id = "review-" + secrets.token_hex(4)

        result: dict[str, object] = {
            "review_id": review_id,
            "verdict": verdict,
            "total_findings": len(validated),
            "critical_count": critical_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "run_path": str(resolved_run) if resolved_run else None,
        }

        # Write review.yaml artifact
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

            # Log review_complete event
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
