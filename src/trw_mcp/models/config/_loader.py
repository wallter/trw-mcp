"""Config singleton factory -- get_config, reload_config, _build_config.

Separated from _main.py to avoid circular imports: state modules
import get_config(), while _build_config() imports state modules.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

import structlog

from trw_mcp.models.config._credentials import resolve_platform_api_key
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._retired_keys import warn_retired_env_vars, warn_unrecognised_config_keys

logger = structlog.get_logger(__name__)


def _config_strict_mode() -> bool:
    """Return True when fail-closed config loading is requested (PRD-QUAL-110-FR01).

    Opt-in via ``TRW_CONFIG_STRICT=1`` (per PRD Open-Question recommendation:
    loud-warn always, fail-closed only when explicitly configured). In strict
    mode a malformed/invalid ``config.yaml`` re-raises instead of silently
    reverting to defaults, so operator security overrides can never be dropped
    without the process noticing.
    """
    return os.environ.get("TRW_CONFIG_STRICT", "").strip().lower() in ("1", "true", "yes", "on")


# --- Singleton factory ---------------------------------------------------

_singleton: TRWConfig | None = None


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


def resolve_config_overrides(project_config_path: Path, *, apply_env_exclusion: bool = True) -> dict[str, object]:
    """The one config cascade: machine layer, project layer, credential, env exclusion.

    Extracted because it was hand-rolled in three places that had already
    drifted apart. ``_build_config`` (production) did all four steps;
    ``_subcommands_doctor._resolve_target_config`` did two, so ``trw-mcp doctor``
    resolved a ``.trw/config.yaml`` value wherever a ``TRW_*`` variable shadowed
    it and the live server resolved the env value — the precedence inversion the
    comment below exists to prevent, in the diagnostic that is supposed to tell
    an operator what the server sees.

    Order, and every step matters:

    1. ``~/.trw/config.yaml`` is the base; the project file overrides per key.
    2. ``platform_api_key`` is DROPPED from the merged mapping (PRD-SEC-005-FR03:
       a git-tracked file is never a credential source) and then re-resolved
       through ``resolve_platform_api_key``.
    3. Keys shadowed by ``TRW_<KEY_UPPER>`` are excluded, because Pydantic
       ``BaseSettings`` gives init kwargs the HIGHEST priority — passing them
       through would invert the documented ``env > file`` precedence.
       ``platform_api_key`` is exempt: its env precedence is applied in step 2.

    Meta-tune key normalisation is NOT a step here — ``TRWConfig``'s own
    ``_normalize_meta_tune_compat`` model validator runs on every construction
    from this cascade's output, so a second copy here would only duplicate it.

    *apply_env_exclusion* exists for one caller: ``_build_config`` must warn about
    unrecognised keys against the merged-but-UNFILTERED set, because a key being
    shadowed by a ``TRW_*`` variable does not make it recognised
    (PRD-QUAL-131-FR04). It passes ``False``, warns, and applies
    :func:`exclude_env_shadowed_keys` itself. Every other caller wants the
    default.

    Raises whatever the underlying readers raise; callers decide their own
    fallback.
    """
    machine_overrides = _read_yaml_overrides(Path.home() / ".trw" / "config.yaml")
    merged = _deep_merge(machine_overrides, _read_yaml_overrides(project_config_path))
    merged.pop("platform_api_key", None)
    resolved_key = resolve_platform_api_key(project_config_path)
    if resolved_key:
        merged["platform_api_key"] = resolved_key
    if not apply_env_exclusion:
        return merged
    return exclude_env_shadowed_keys(merged)


def exclude_env_shadowed_keys(merged: dict[str, object]) -> dict[str, object]:
    """Drop keys a ``TRW_<KEY_UPPER>`` variable shadows, keeping ``platform_api_key``.

    Pydantic ``BaseSettings`` gives init kwargs the HIGHEST priority, so passing
    a shadowed key through inverts the documented ``env > file`` precedence.
    ``platform_api_key`` is exempt because its env precedence is already applied
    by the credential cascade.
    """
    return {k: v for k, v in merged.items() if k == "platform_api_key" or f"TRW_{k.upper()}" not in os.environ}


def _build_config_unguarded() -> TRWConfig:
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

    # A retired key's TRW_* alias is dropped as silently as its YAML key, with or
    # without a config file; the one retired-keys table covers both.
    warn_retired_env_vars(os.environ)
    try:
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root()
        project_config_path = project_root / ".trw" / "config.yaml"
        # ONE cascade, shared with the doctor. Two hand-rolled copies had already
        # drifted apart once; a third would drift again.
        merged = resolve_config_overrides(project_config_path, apply_env_exclusion=False)

        if merged:
            # PRD-QUAL-131-FR04: TRWConfig is extra="ignore", so any key it does
            # not define is about to be dropped without a word. Say so BEFORE the
            # constructor swallows it. Checked against the whole merged set
            # rather than the env-filtered one, because a key being shadowed by a
            # TRW_* variable does not make it recognised.
            warn_unrecognised_config_keys(merged, TRWConfig.model_fields)
            # Exclude keys overridden by a TRW_ env var (env wins). The
            # platform_api_key is resolved above and intentionally kept even
            # when TRW_PLATFORM_API_KEY is set (its env precedence is already
            # applied), so it is exempt from the generic TRW_* exclusion.
            filtered = exclude_env_shadowed_keys(merged)
            if filtered:
                return TRWConfig(**filtered)  # type: ignore[arg-type]
    except Exception as exc:
        # PRD-QUAL-110-FR01: fail LOUD, not silent. A malformed or invalid
        # config.yaml here means every operator hardening override is about to
        # be discarded — that MUST be visible. Was a DEBUG no-op (the dominant
        # silent-misconfiguration failure mode in the enterprise audit).
        logger.warning("config_load_failed", exc_info=True, strict=_config_strict_mode())
        # Loud stderr notice for operators tailing the process (logs may be
        # routed elsewhere or filtered below WARNING).
        print(
            "TRW: WARNING — .trw/config.yaml could not be loaded "
            f"({type(exc).__name__}); reverting to defaults and DISCARDING any "
            "config overrides. Set TRW_CONFIG_STRICT=1 to fail closed instead.",
            file=sys.stderr,
        )
        # FR01 fail-closed: in strict mode, re-raise so security-relevant
        # overrides are never silently dropped (opt-in; default stays fail-open).
        if _config_strict_mode():
            raise
    return TRWConfig()


_META_TUNE_SANDBOX_PLATFORM = "Linux"


def apply_platform_meta_tune_gate(config: TRWConfig, *, system: str | None = None) -> TRWConfig:
    """Force meta-tune OFF on any platform that cannot host its sandbox (PRD-FIX-137-FR01).

    SAFE-001's ``subprocess-seccomp-v1`` sandbox exists only on Linux, and the
    boot validator in ``meta_tune.boot_checks`` refuses ``platform != Linux``.
    Before this gate that refusal was raised from ``_build_middleware``, so a
    ``.trw/config.yaml`` written on a Linux box with ``meta_tune_enabled: true``
    killed the whole MCP server on a macOS checkout — ``--version``, ``doctor``
    and every tool included — over a feature the platform cannot run anyway.

    The safety property SAFE-001 protects is "no meta-tune without a sandbox".
    Disabling meta-tune preserves it exactly; aborting boot only adds collateral.
    So on a non-Linux host the effective config carries ``meta_tune.enabled=False``
    and a WARNING names the override, its cause, and the remedy. Linux is left
    untouched: there the fail-loud validator still runs, because a Linux operator
    who enabled meta-tune without the extra installed must be told, not silently
    downgraded (PRD-FIX-137-FR02).

    Mutates *config* in place (both the nested flag and the legacy mirror field,
    which the model validator keeps aligned only at construction) and returns it
    so call sites can wrap a constructor expression.
    """
    current = system if system is not None else platform.system()
    if current == _META_TUNE_SANDBOX_PLATFORM or not config.meta_tune.enabled:
        return config
    logger.warning(
        "meta_tune_disabled_unsupported_platform",
        platform=current,
        required_platform=_META_TUNE_SANDBOX_PLATFORM,
        detail=(
            "meta_tune.enabled=true in the resolved config, but the SAFE-001 sandbox "
            "is Linux-only; forcing meta_tune.enabled=false for this process. Set "
            "meta_tune_enabled: false (or TRW_META_TUNE_ENABLED=false) to silence this."
        ),
    )
    config.meta_tune = config.meta_tune.model_copy(update={"enabled": False})
    config.meta_tune_enabled = False
    return config


def _build_config() -> TRWConfig:
    """The cascade in :func:`_build_config_unguarded`, then the platform meta-tune gate."""
    return apply_platform_meta_tune_gate(_build_config_unguarded())


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
