"""Shared helpers for split impact calibration tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _utc(days_ago: int = 0) -> datetime:
    """Return a UTC datetime *days_ago* days in the past."""
    return datetime.now(timezone.utc) - timedelta(days=days_ago)
