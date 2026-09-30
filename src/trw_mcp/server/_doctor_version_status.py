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

__all__ = ["stale_editable_metadata", "version_status_row"]


def stale_editable_metadata() -> list[str]:
    """Each package whose installed dist-info names another version than the source it runs (B71-111).

    An editable install records its version when installed; a later bump in the source leaves that
    record stale (canon TB-22: metadata 6.0.0/3.0.0 under a 7.0/4.0 source). Both packages now resolve
    their own version from the source, so this is a warning about pip/uv's view, not a runtime fault.
    """
    from importlib.metadata import PackageNotFoundError, version

    import trw_memory

    import trw_mcp

    stale: list[str] = []
    for dist, source in (("trw-mcp", trw_mcp.__version__), ("trw-memory", trw_memory.__version__)):
        try:
            recorded = version(dist)
        except PackageNotFoundError:  # trw-fail-silent-allow: not installed, so it has no metadata to be stale
            continue
        if recorded != source:
            stale.append(f"{dist}: metadata {recorded}, source {source}")
    return stale


def version_status_row(project_root: Path) -> tuple[Literal["PASS", "WARN"], str]:
    """Render the doctor row from ``collect_version_status()``. Never terminates anything."""
    from trw_mcp.server._subcommands_release import collect_version_status

    status = collect_version_status(project_root)
    if status["compatible"]:
        from trw_mcp.server._doctor_version_skew import read_daemon_skew

        skew = read_daemon_skew()
        if skew is not None:
            usable = "" if skew.incompatible else " (same major: memory still works)"
            return (
                "WARN",
                f"version-status reports compatible=true for the packages on disk, but the live memory daemon "
                f"(pid {skew.pid}) runs trw-memory {skew.daemon} while this client is {skew.client}{usable}; see "
                f"memory_daemon and memory_backend. Fix: {skew.remedy()}.",
            )
    if status["compatible"] and (stale := stale_editable_metadata()):
        return (
            "WARN",
            f"stale editable-install metadata ({'; '.join(stale)}). TRW reads the source version, but pip and "
            "uv still see the old one; reinstall with `uv pip install -e trw-memory -e trw-mcp`.",
        )
    if status["compatible"]:
        return "PASS", "version-status reports compatible=true."
    mismatches = ", ".join(status["mismatches"]) or "unspecified"
    return (
        "WARN",
        f"version-status reports compatible=false (mismatches: {mismatches}). "
        "Run `trw-mcp version-status --json` for detail; doctor and version-status read the "
        "same layer so this cannot be a stale doctor-only PASS.",
    )
