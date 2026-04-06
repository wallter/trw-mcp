"""Sync pipeline configuration fields — PRD-INFRA-051-FR07.

Covers backend sync connectivity and push/pull settings.
All fields default to disabled/offline so existing users see zero behavior change.
"""

from __future__ import annotations


class _SyncFields:
    """Sync domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Backend connectivity --
    backend_url: str = ""
    backend_api_key: str = ""

    # -- Push/Pull cadence --
    sync_interval_seconds: int = 300
    sync_push_batch_size: int = 100
    sync_push_timeout_seconds: float = 10.0

    # -- Feature gates --
    meta_tune_enabled: bool = False
    team_sync_enabled: bool = False
