"""Sync pipeline configuration fields — PRD-INFRA-051-FR07.

Covers backend sync connectivity and push/pull settings.
All fields default to disabled/offline so existing users see zero behavior change.
"""

from __future__ import annotations

from pydantic import Field


class _SyncFields:
    """Sync domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Backend connectivity --
    backend_url: str = ""
    backend_api_key: str = Field(default="", json_schema_extra={"secret": True})

    # -- Push/Pull cadence --
    sync_interval_seconds: int = 300
    sync_push_batch_size: int = 100
    sync_push_timeout_seconds: float = 10.0

    # -- Pull settings (PRD-INFRA-053) --
    sync_pull_timeout_seconds: float = 5.0
    intel_cache_ttl_seconds: int = 3600
    intel_cache_enabled: bool = True

    # -- Feature gates --
    meta_tune_enabled: bool = False
    team_sync_enabled: bool = False
