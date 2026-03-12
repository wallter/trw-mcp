"""Config singleton factory -- get_config, _reset_config, _build_config.

Separated from _main.py to avoid circular imports: state modules
import get_config(), while _build_config() imports state modules.
"""

from __future__ import annotations

import structlog

from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger()

# --- Singleton factory ---------------------------------------------------

_singleton: TRWConfig | None = None


def get_config() -> TRWConfig:
    """Return the shared TRWConfig singleton.

    First call creates the instance with config.yaml overrides merged.
    Subsequent calls return the same object.
    Use ``_reset_config()`` in tests to clear cached state.
    """
    global _singleton
    if _singleton is None:
        _singleton = _build_config()
    return _singleton


def _build_config() -> TRWConfig:
    """Build TRWConfig with ``.trw/config.yaml`` overrides merged.

    Precedence (highest wins):
    1. Environment variables (``TRW_*``) -- checked explicitly
    2. ``.trw/config.yaml`` values -- passed as init kwargs
    3. Field defaults defined in TRWConfig

    Pydantic BaseSettings gives init kwargs *highest* priority, so we
    must exclude config.yaml keys that have a corresponding ``TRW_*``
    env var set to preserve the documented precedence.

    Gracefully falls back to defaults-only when:
    - Running outside a git repository (e.g. during ``pip install``)
    - config.yaml is missing or malformed
    - Any import or filesystem error occurs
    """
    import os

    try:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.state.persistence import FileStateReader

        project_root = resolve_project_root()
        config_path = project_root / ".trw" / "config.yaml"
        if config_path.exists():
            reader = FileStateReader()
            overrides = reader.read_yaml(config_path)
            if isinstance(overrides, dict):
                # Filter to non-None values with string keys,
                # excluding keys that have a TRW_ env var set
                filtered = {
                    str(k): v
                    for k, v in overrides.items()
                    if v is not None
                    and f"TRW_{str(k).upper()}" not in os.environ
                }
                if filtered:
                    return TRWConfig(**filtered)  # type: ignore[arg-type]
    except Exception:  # justified: fail-open, config file read failure falls back to defaults
        logger.debug("config_load_failed", exc_info=True)
    return TRWConfig()


def _reset_config(config: TRWConfig | None = None) -> None:
    """Reset the config singleton (test helper only).

    Args:
        config: Optional replacement config. If *None*, the next
            ``get_config()`` call creates a fresh default instance.
    """
    global _singleton
    _singleton = config
