"""Local intelligence cache -- PRD-INFRA-053.

Stored at .trw/intel-cache.json with atomic writes and TTL-based expiry.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_CACHE_FILE = "intel-cache.json"


class IntelligenceCache:
    """Local file cache for backend intelligence state.

    Stored at .trw/intel-cache.json with atomic writes and TTL-based expiry.
    """

    def __init__(self, trw_dir: Path, ttl_seconds: int = 3600) -> None:
        self._cache_path = trw_dir / _CACHE_FILE
        self._ttl_seconds = ttl_seconds

    def get_bandit_params(self) -> dict[str, float] | None:
        """Read cached bandit arm parameters. Returns None if expired/missing."""
        data = self._read_cache()
        if data is None:
            return None
        raw = data.get("bandit_params")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def get_attribution_results(self) -> dict[str, dict[str, Any]] | None:
        """Read cached attribution results."""
        data = self._read_cache()
        if data is None:
            return None
        raw = data.get("attribution_results")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def get_synthesis_overlay(self) -> dict[str, Any] | None:
        """Read cached synthesis overlay."""
        data = self._read_cache()
        if data is None:
            return None
        raw = data.get("synthesis_overlay")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def update(self, state: dict[str, Any], etag: str | None = None) -> None:
        """Atomically write new state to cache."""
        state["_meta"] = {
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "etag": etag or "",
            "ttl_seconds": self._ttl_seconds,
        }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._cache_path.parent),
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(state, f, sort_keys=True, indent=2, default=str)
                os.chmod(tmp_path, 0o600)
                os.rename(tmp_path, str(self._cache_path))
                logger.debug(
                    "intel_cache_write_success",
                    event_type="intel_cache_write_success",
                    etag=etag,
                    outcome="success",
                )
            except Exception:  # justified: cleanup, temp cache file cleanup must not mask the write failure
                logger.debug(
                    "intel_cache_write_failed",
                    event_type="intel_cache_write_failed",
                    path=str(self._cache_path),
                    outcome="error",
                    exc_info=True,
                )
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
        except Exception:  # justified: fail-open, cache persistence is best-effort for sync metadata
            logger.warning(
                "intel_cache_write_error",
                event_type="intel_cache_write_error",
                outcome="error",
                exc_info=True,
            )

    @property
    def is_fresh(self) -> bool:
        """Check if cache is within TTL."""
        data = self._read_cache()
        return data is not None

    @property
    def etag(self) -> str | None:
        """Return stored ETag, or None if cache invalid."""
        try:
            if not self._cache_path.exists():
                return None
            raw = json.loads(self._cache_path.read_text())
            meta = raw.get("_meta", {})
            if not isinstance(meta, dict):
                return None
            val = meta.get("etag")
            if not val:
                return None
            return str(val)
        except Exception:  # justified: fail-open, corrupt cache metadata falls back to a full sync
            logger.debug("intel_cache_etag_unavailable", exc_info=True)
            return None

    def _read_cache(self) -> dict[str, Any] | None:
        """Read and validate cache file. Returns None if missing/expired/corrupt."""
        try:
            if not self._cache_path.exists():
                return None
            raw = json.loads(self._cache_path.read_text())
            if not isinstance(raw, dict):
                return None
            meta = raw.get("_meta", {})
            if not isinstance(meta, dict):
                return None
            updated_at = meta.get("updated_at")
            if not updated_at:
                return None
            dt = datetime.fromisoformat(str(updated_at))
            age = (datetime.now(tz=timezone.utc) - dt).total_seconds()
            if age > self._ttl_seconds:
                logger.debug(
                    "intel_cache_expired",
                    age_seconds=age,
                    ttl_seconds=self._ttl_seconds,
                )
                return None
            return raw
        except Exception:  # justified: fail-open, corrupt cache data should trigger refresh rather than crash
            logger.warning("intel_cache_corrupt", exc_info=True)
            return None
