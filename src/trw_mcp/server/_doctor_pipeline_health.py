"""The ``pipeline_health`` row of ``trw-mcp doctor`` (PRD-CORE-300-FR05 slice S3b).

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling for the
eLOC gate. Reuses the same fail-open helper the ``trw-mcp telemetry
pipeline-health`` CLI verb calls, so the doctor row and the CLI can never
report two different verdicts for the same store.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig

__all__ = ["pipeline_health_row"]


def pipeline_health_row(_target: Path, _config: TRWConfig) -> tuple[str, str]:
    """``(status, message)``: WARN when degraded, PASS otherwise.

    The doctor's own per-check ``try/except`` in ``_doctor_core`` already
    isolates a crash here into a FAIL row, so this row never needs its own
    catch-all — :func:`safe_pipeline_health` still has one, for the CLI's sake.
    """
    from trw_mcp.tools._telemetry_cli import safe_pipeline_health

    health = safe_pipeline_health()
    if bool(health.get("degraded")):
        advisory = str(health.get("advisory") or "pipeline degraded")
        return "WARN", f"{advisory} (run `trw-mcp telemetry pipeline-health` for detail)"
    return "PASS", "pipeline health: no degraded signal"
