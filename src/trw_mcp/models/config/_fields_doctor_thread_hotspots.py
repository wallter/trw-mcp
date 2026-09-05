"""Doctor thread-hotspot WARN threshold fields (PRD-FIX-131 operator-visibility follow-up).

Own domain mixin rather than folding into ``_fields_boot_maintenance.py``: that
file already owns WAL-checkpoint and boot-deferral policy, and this pair has no
relationship to checkpoint scheduling -- it is a report-only threshold for a
single ``trw-mcp doctor`` row that reads no SQLite state at all.
"""

from __future__ import annotations

from pydantic import Field


class _DoctorThreadHotspotFields:
    """``trw-mcp doctor`` thread-hotspot WARN threshold -- mixed into _TRWConfigFields via MI."""

    # Measured incident (2026-09-05): three live trw-mcp servers each had ONE
    # worker thread at 70-85% CPU utilization over 2-2.5h lifetimes while
    # py-spy/gdb were unavailable (ptrace_scope=1, no sudo) and trw_deliver hung
    # for 1,800s with no visible cause. These two knobs are magnitude tunables
    # only -- neither disables the row (SKIP on non-Linux/unreadable /proc is a
    # platform fact the check reports, not a policy choice these fields make).
    doctor_thread_hotspot_share: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of a live trw-mcp server's process uptime its hottest thread's CPU time must "
            "reach for 'trw-mcp doctor' to WARN on the thread_hotspots row. 0.5 sits below every "
            "observed incident share (0.70-0.85) with headroom against a briefly busy-but-healthy "
            "thread (e.g. an embeddings backfill) that has not yet cleared half the process's "
            "lifetime. Read together with doctor_thread_hotspot_min_seconds -- BOTH must be "
            "exceeded, so a server that has only run for a few seconds cannot trip the row on share "
            "alone."
        ),
    )
    doctor_thread_hotspot_min_seconds: int = Field(
        default=300,
        ge=1,
        le=86400,
        description=(
            "Minimum CPU seconds a live trw-mcp server's hottest thread must have burned before "
            "'trw-mcp doctor' will WARN on the thread_hotspots row, even when "
            "doctor_thread_hotspot_share is also exceeded. Set well below the observed incident "
            "magnitude (thousands of seconds) so a genuinely new hotspot is caught long before it "
            "approaches an hours-long incident, while a server up for under five minutes cannot "
            "trip a WARN purely from startup work."
        ),
    )


__all__ = ["_DoctorThreadHotspotFields"]
