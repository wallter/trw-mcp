"""Wall-clock tracking fields (PRD-CORE-338)."""

from __future__ import annotations

from pydantic import Field


class _TimeFields:
    """Time-tracking domain mixin — mixed into _TRWConfigFields via MI."""

    #: FR02 kill switch: ``False`` makes every run untracked, so no surface
    #: adds time output (the phase re-entry fix and FR06's mismatch warning
    #: stay on, per the PRD's rollback plan).
    time_tracking_enabled: bool = True

    #: OQ-2 (lead decision, operator still open): an IANA zone name. When it is
    #: not UTC, a tracked run's ``trw_status`` time block also renders the ETA
    #: range in this zone as ``eta_local``. Every stored and compared time stays
    #: UTC; an unknown zone name renders nothing rather than guessing.
    display_timezone: str = Field(default="UTC", max_length=64)

    #: FR07: a joined member whose run recorded no event for strictly longer
    #: than this is reported ``activity_stale`` (advisory, like the other stalls).
    formation_activity_stall_seconds: int = Field(default=1800, ge=60)
