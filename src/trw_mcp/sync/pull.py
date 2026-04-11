"""Sync pull -- fetch intelligence state from backend -- PRD-INFRA-053.

Follows the fail-open pattern from trw-memory/sync/remote.py:
never raises, returns PullResult or None on all paths.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class PullResult(BaseModel):
    """Result of a pull operation."""

    state: dict[str, Any] | None = None
    etag: str | None = None
    sync_hints: dict[str, Any] | None = None
    team_learnings: list[dict[str, Any]] | None = None
    status_code: int = 0


class SyncPuller:
    """Pull intelligence state from backend. Never raises."""

    def __init__(
        self, backend_url: str, api_key: str, timeout: float = 5.0
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def pull_intel_state(
        self,
        etag: str | None = None,
        since_seq: int = 0,
        model_family: str = "",
        trw_version: str = "",
    ) -> PullResult | None:
        """GET /v1/intel/state. Returns None on 304 or error."""
        import httpx

        try:
            headers: dict[str, str] = {
                "Authorization": f"Bearer {self._api_key}",
            }
            if etag:
                headers["If-None-Match"] = f'"{etag}"'

            params: dict[str, Any] = {"since_seq": since_seq}
            if model_family:
                params["model_family"] = model_family
            if trw_version:
                params["trw_version"] = trw_version

            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(
                    f"{self._backend_url}/v1/intel/state",
                    headers=headers,
                    params=params,
                )

                if resp.status_code == 304:
                    logger.debug("sync_pull_not_modified")
                    return None

                resp.raise_for_status()
                data = resp.json()

                return PullResult(
                    state=data,
                    etag=data.get("etag"),
                    sync_hints=data.get("sync_hints"),
                    team_learnings=data.get("team_learnings"),
                    status_code=resp.status_code,
                )
        except Exception:  # justified: boundary, remote sync pull failures must not break local workflows
            logger.warning("sync_pull_error", exc_info=True)
            return None
