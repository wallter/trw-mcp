"""Autonomous update pipeline — PRD-INFRA-014 Phase 2C.

Checks for available updates on session start and optionally
installs them. Fail-open: network errors never block session start.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from trw_mcp.models.config import get_config

logger = logging.getLogger(__name__)

# Cache duration: check at most once per 24h
_VERSION_CACHE_HOURS = 24


def get_installed_version() -> str:
    """Return the currently installed trw-mcp version."""
    try:
        from trw_mcp import __version__

        return __version__
    except (ImportError, AttributeError):
        return "0.0.0"


def check_for_update() -> dict[str, object]:
    """Check if a newer version is available.

    Returns:
        {available: bool, current: str, latest: str, channel: str, advisory: str | None}
    Fail-open: returns available=False on any error.
    """
    cfg = get_config()
    current = get_installed_version()

    if not cfg.platform_url:
        return {
            "available": False,
            "current": current,
            "latest": current,
            "channel": cfg.update_channel,
            "advisory": None,
        }

    try:
        url = f"{cfg.platform_url.rstrip('/')}/v1/releases/latest?channel={cfg.update_channel}"
        headers: dict[str, str] = {}
        if cfg.platform_api_key:
            headers["Authorization"] = f"Bearer {cfg.platform_api_key}"
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as response:
            if 200 <= response.status < 300:
                data: Any = json.loads(response.read().decode("utf-8"))
                latest = str(data.get("version", current))
                available = _compare_versions(current, latest)
                advisory: str | None = (
                    f"TRW v{latest} available (you have v{current}). " if available else None
                )
                return {
                    "available": available,
                    "current": current,
                    "latest": latest,
                    "channel": cfg.update_channel,
                    "advisory": advisory,
                }
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, KeyError):
        logger.debug("Version check failed — proceeding with current version")

    return {
        "available": False,
        "current": current,
        "latest": current,
        "channel": cfg.update_channel,
        "advisory": None,
    }


def _compare_versions(current: str, latest: str) -> bool:
    """Return True if latest is newer than current using semver tuple comparison."""
    try:
        cur_parts = tuple(int(x) for x in current.split(".")[:3])
        lat_parts = tuple(int(x) for x in latest.split(".")[:3])
        return lat_parts > cur_parts
    except (ValueError, TypeError):
        return False
