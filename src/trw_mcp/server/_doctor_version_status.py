"""Doctor's own check that it agrees with ``version-status`` (PRD-FIX-149 FR07).

Belongs to the ``_subcommands_doctor.py`` facade; re-exported there.

Doctor and ``version-status`` once disagreed (doctor PASS while
``version-status`` said ``compatible: false``). Without a shared read, a
future stamping defect (PRD-INFRA-192's I1 is one measured instance) could
again leave doctor silent while ``version-status --json`` independently reports
``compatible: false``. This module closes that gap generally: it reads
``collect_version_status()`` -- the same call ``version-status`` itself makes --
and WARNs, naming the mismatch, whenever ``compatible`` is false. Silent (PASS)
on the common path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

__all__ = ["version_status_row"]


def version_status_row(project_root: Path) -> tuple[Literal["PASS", "WARN"], str]:
    """Render the doctor row from ``collect_version_status()``. Never terminates anything."""
    from trw_mcp.server._subcommands_release import collect_version_status

    status = collect_version_status(project_root)
    if status["compatible"]:
        return "PASS", "version-status reports compatible=true."
    mismatches = ", ".join(status["mismatches"]) or "unspecified"
    return (
        "WARN",
        f"version-status reports compatible=false (mismatches: {mismatches}). "
        "Run `trw-mcp version-status --json` for detail; doctor and version-status read the "
        "same layer so this cannot be a stale doctor-only PASS.",
    )
