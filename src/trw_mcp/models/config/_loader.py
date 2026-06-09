"""Config singleton factory -- get_config, reload_config, _build_config.

Separated from _main.py to avoid circular imports: state modules
import get_config(), while _build_config() imports state modules.
"""

from __future__ import annotations

import structlog

from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)

# --- Singleton factory ---------------------------------------------------

_singleton: TRWConfig | None = None


def _normalize_meta_tune_overrides(overrides: dict[str, object]) -> dict[str, object]:
    """Keep legacy flat + nested SAFE-001 config keys compatible."""
    normalized = dict(overrides)
    legacy_enabled = normalized.get("meta_tune_enabled")
    nested = normalized.get("meta_tune")

    if isinstance(nested, dict):
        nested_meta_tune = {str(k): v for k, v in nested.items()}
    else:
        nested_meta_tune = {}

    if "enabled" not in nested_meta_tune and isinstance(legacy_enabled, bool):
        nested_meta_tune["enabled"] = legacy_enabled
    if nested_meta_tune:
        normalized["meta_tune"] = nested_meta_tune
        if isinstance(nested_meta_tune.get("enabled"), bool):
            normalized["meta_tune_enabled"] = nested_meta_tune["enabled"]
    return normalized


def get_config() -> TRWConfig:
    """Return the shared TRWConfig singleton.

    First call creates the instance with config.yaml overrides merged.
    Subsequent calls return the same object.
    Use ``reload_config()`` to clear cached state.
    """
    global _singleton
    if _singleton is None:
        _singleton = _build_config()
    return _singleton


def _deep_merge(base: dict[str, object], over: dict[str, object]) -> dict[str, object]:
    """Deep key-wise merge: values in *over* win, nested dicts merge recursively.

    The more specific layer (*over*) overrides the less specific (*base*) per
    key; nested mappings are merged rather than whole-object replaced
    (PRD-CORE-185 FR04).
    """
    merged: dict[str, object] = dict(base)
    for key, value in over.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(
                {str(k): v for k, v in existing.items()},
                {str(k): v for k, v in value.items()},
            )
        else:
            merged[key] = value
    return merged


def _read_yaml_overrides(config_path: object) -> dict[str, object]:
    """Read a ``config.yaml`` into a string-keyed dict, or ``{}`` on absence.

    Uses ``YAML(typ="safe")`` via ``FileStateReader`` (NFR05). Never raises;
    a missing or malformed file yields an empty mapping so the cascade collapses
    to the next layer.
    """
    from pathlib import Path

    from trw_mcp.state.persistence import FileStateReader

    path = config_path if isinstance(config_path, Path) else Path(str(config_path))
    if not path.exists():
        return {}
    overrides = FileStateReader().read_yaml(path)
    if not isinstance(overrides, dict):
        return {}
    return {str(k): v for k, v in overrides.items() if v is not None}


def _build_config() -> TRWConfig:
    """Build TRWConfig with the machine -> project -> env config cascade merged.

    Precedence (highest wins) -- PRD-CORE-185 FR04:
    1. Environment variables (``TRW_*``) -- checked explicitly
    2. ``.trw/config.yaml`` (project) -- passed as init kwargs
    3. ``~/.trw/config.yaml`` (machine defaults) -- merged BENEATH the project file
    4. Field defaults defined in TRWConfig

    The machine layer is additive and OPTIONAL: with no ``~/.trw/config.yaml``
    present the effective config is byte-identical to the prior project-only
    behavior (NFR02). The merge is a deep key-wise merge (project overrides
    machine per key); env still overrides both.

    Pydantic BaseSettings gives init kwargs *highest* priority, so we
    must exclude merged keys that have a corresponding ``TRW_*`` env var set to
    preserve the documented precedence.

    Gracefully falls back to defaults-only when:
    - Running outside a git repository (e.g. during ``pip install``)
    - config.yaml is missing or malformed
    - Any import or filesystem error occurs
    """
    import os
    from pathlib import Path

    try:
        from trw_mcp.state._paths import resolve_project_root

        machine_overrides = _read_yaml_overrides(Path.home() / ".trw" / "config.yaml")
        project_root = resolve_project_root()
        project_overrides = _read_yaml_overrides(project_root / ".trw" / "config.yaml")

        # Deep merge: machine is the base, project overrides per key.
        merged = _deep_merge(machine_overrides, project_overrides)
        if merged:
            # Exclude keys overridden by a TRW_ env var (env wins).
            filtered = {k: v for k, v in merged.items() if f"TRW_{k.upper()}" not in os.environ}
            if filtered:
                return TRWConfig(**_normalize_meta_tune_overrides(filtered))  # type: ignore[arg-type]
    except Exception:  # justified: fail-open, config file read failure falls back to defaults
        logger.debug("config_load_failed", exc_info=True)
    return TRWConfig()


def reload_config(config: TRWConfig | None = None) -> None:
    """Reset the config singleton for project-switching or testing.

    Clears the cached TRWConfig so the next ``get_config()`` call rebuilds
    it from ``.trw/config.yaml`` and environment variables.  Pass an explicit
    *config* to inject a pre-built instance (useful in tests).

    Args:
        config: Optional replacement config. If *None*, the next
            ``get_config()`` call creates a fresh default instance.
    """
    global _singleton
    _singleton = config


# Backward-compatible alias (deprecated, use reload_config instead).
_reset_config = reload_config
