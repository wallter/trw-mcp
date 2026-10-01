"""Config singleton factory -- get_config, reload_config, _build_config.

Separated from _main.py to avoid circular imports: state modules
import get_config(), while _build_config() imports state modules.
"""

from __future__ import annotations

import os
import platform
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import structlog

from trw_mcp.exceptions import ConfigError
from trw_mcp.models.config._credentials import resolve_platform_api_key
from trw_mcp.models.config._local_only_guard import reject_local_only_env, reject_local_only_mapping
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._retired_keys import (
    config_key_sources,
    warn_retired_env_vars,
    warn_unrecognised_config_keys,
)

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

#: The key the singleton was built under: the resolved project root plus each
#: config file's stat stamp, taken BEFORE the files were read. ``None`` means
#: "nothing to compare against": no singleton yet, or one injected through
#: ``reload_config(config)``, which a file edit must never replace
#: (PRD-CORE-305-FR04).
_FreshnessKey = tuple[Path, tuple[tuple[Path, tuple[int, int] | None], ...]]
_built_from: _FreshnessKey | None = None

#: A transform applied to every file-backed build — the serve path's CLI
#: overrides (``--allow-unsigned``), which must survive a reload.
_override: Callable[[TRWConfig], TRWConfig] | None = None

#: Serializes building and replacing the singleton across request threads.
_build_lock = threading.RLock()

#: Set while the override runs, so a re-entrant ``get_config()`` fails clearly.
_in_override = threading.local()

#: The key of a config whose read overlapped an edit on every retry: it matches
#: no real key, so the next refresh rebuilds it.
_UNSETTLED: _FreshnessKey = (Path(), ())


def _stamp(path: Path) -> tuple[int, int] | None:
    """``(mtime_ns, size)`` of *path*, or ``None`` when it does not exist."""
    try:
        st = path.stat()
    # trw-fail-silent-allow: an unreadable or missing config file is the "absent" stamp, compared like any other; a later appearance is a change and triggers a rebuild
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _freshness_key() -> _FreshnessKey | None:
    """The project root and the stamps of the files a surface decision reads:
    the machine and project YAML layers, and the project ``.env`` the
    ``trw_assess`` backend cascade also consults. ``None`` when no project root
    resolves; the config is then built but not tracked."""
    from trw_mcp.state._paths import resolve_project_root

    try:
        root = resolve_project_root()
    # trw-fail-silent-allow: no resolvable project root (outside a checkout) means an untracked config, exactly the pre-FR04 behavior; _build_config applies its own loud fallback
    except Exception:
        return None
    files = (Path.home() / ".trw" / "config.yaml", root / ".trw" / "config.yaml", root / ".env")
    return root, tuple((path, _stamp(path)) for path in files)


def _apply_override(config: TRWConfig) -> TRWConfig:
    """Run the override on the built *config*, refusing a re-entrant ``get_config()``."""
    if _override is None:
        return config
    _in_override.active = True
    try:
        return _override(config)
    finally:
        _in_override.active = False


def _build_tracked() -> TRWConfig:
    """Build the singleton from the files, keyed by stamps taken before the read.

    A config is tracked only under a stamp that matched both before and after
    its read. An edit that lands during the read moves a stamp and the build is
    retried; if every retry overlaps an edit, the last config is published under
    ``_UNSETTLED``, which matches no real key, so the next refresh rebuilds.
    Caller holds the lock.
    """
    global _singleton, _built_from
    before = _freshness_key()
    tracked: _FreshnessKey | None = _UNSETTLED
    for _ in range(3):
        config = _build_config()
        after = _freshness_key()
        if after == before:
            tracked = before
            break
        before = after
    _singleton = _apply_override(config)
    _built_from = tracked
    return _singleton


def get_config() -> TRWConfig:
    """Return the shared TRWConfig singleton.

    First call creates the instance with config.yaml overrides merged.
    Subsequent calls return the same object.
    Use ``reload_config()`` to clear cached state, or
    ``refresh_config_if_changed()`` to rebuild only after a config file edit.

    Inside an install (``state._project_root_binding``, B71-118) it is the
    install target's config instead, built once per binding and cached in the
    install's own context: the singleton belongs to this process's project,
    which other threads share, so an install neither reads it nor replaces it.
    The CLI override (``set_config_override``) applies to that build too, so a
    serving process keeps its flags across an in-process install.
    """
    if getattr(_in_override, "active", False):
        raise ConfigError(
            "a config override must not call get_config(): it receives the built config and returns the config to use"
        )
    from trw_mcp.state._project_root_binding import install_cache

    cache = install_cache()
    if cache is not None:
        installed = cache.get("config")
        if not isinstance(installed, TRWConfig):
            installed = cache["config"] = _apply_override(_build_config())
        return installed
    config = _singleton
    if config is not None:
        return config
    with _build_lock:
        return _singleton if _singleton is not None else _build_tracked()


def set_config_override(override: Callable[[TRWConfig], TRWConfig] | None) -> None:
    """Apply *override* to every file-backed build from now on; ``None`` removes it.

    Contract: ``override(config) -> config`` receives the freshly built config
    and returns the one to use. It must not call :func:`get_config` (that raises
    ``ConfigError``). The caller owns the lifecycle: ``main()`` installs the
    serve path's CLI overrides and removes them in a ``finally``.
    ``reload_config()`` deliberately keeps the override, because a config
    rebuilt inside a serving process must keep that process's CLI flags. The cached config is cleared
    either way, so the next read applies the change.
    """
    global _override, _singleton, _built_from
    with _build_lock:
        _override = override
        _singleton = None
        _built_from = None


def refresh_config_if_changed() -> bool:
    """Rebuild the singleton when its project root or a config file it was built
    from changed; ``True`` if it did.

    A few ``stat`` calls and no YAML parse on the unchanged path, so a
    per-request caller (the surface-authority middleware) can afford it. An
    injected config is left alone, and so is everything inside an install. A stat fault is the absent stamp, never an
    error; a rebuild raises only what the build or the override raises (the
    middleware fails open on it), and the previous config stays installed.
    """
    from trw_mcp.state._project_root_binding import install_target

    if install_target() is not None:
        # Inside an install "the project" is the install's target, so the key
        # below would read as changed and rebuild the SERVER's singleton from the
        # target's files. An install builds its own config fresh per binding and
        # never reads the singleton, so there is nothing here for it to refresh.
        return False
    built_from = _built_from
    if _singleton is None or built_from is None or _freshness_key() == built_from:
        return False  # a root that stops resolving (key None) differs and rebuilds
    with _build_lock:
        if _built_from is None or _freshness_key() == _built_from:
            return False  # another thread rebuilt, or a config was injected meanwhile
        _build_tracked()
    logger.info("config_reloaded_on_change", previous_project_root=str(built_from[0]))
    return True


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

    The one layer reader, shared with the light pin read that store selection uses
    (``state._namespace_pin_read``, PRD-CORE-333 S3c), so the two cannot parse a layer
    differently. A file that does not parse or is not a mapping raises ``StateError``.
    """
    from trw_mcp.state._namespace_pin_read import read_config_layer

    return read_config_layer(config_path)


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
    # PRD-SEC-022-FR03: a tracked config.yaml is never a credential source for
    # backend_api_key either -- close the gap platform_api_key's drop (above)
    # already closes for itself. Only TRW_BACKEND_API_KEY (or the
    # platform_api_key fallback in resolved_backend_api_key) supplies it after
    # this drop. Never log the value, only the field name.
    dropped_backend_key = merged.pop("backend_api_key", None)
    if isinstance(dropped_backend_key, str) and dropped_backend_key:
        logger.warning(
            "tracked_config_secret_dropped",
            field="backend_api_key",
            detail=(
                "backend_api_key in a tracked config.yaml is ignored; set "
                "TRW_BACKEND_API_KEY or leave it empty so the platform_api_key "
                "fallback applies (PRD-SEC-022-FR03)."
            ),
        )
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


def _build_config_unguarded(project_config_path: Path | None = None) -> TRWConfig:
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
    # F5 follow-up (P1): unlike every other retired key, local_only fails
    # closed, not warn-and-ignore — pydantic-settings never surfaces this
    # env var into TRWConfig's model_validator (it isn't a declared field),
    # so this raw os.environ scan is the only place that catches it.
    reject_local_only_env(os.environ)
    try:
        from trw_mcp.state._paths import resolve_project_root

        if project_config_path is None:
            project_config_path = resolve_project_root() / ".trw" / "config.yaml"
        # ONE cascade, shared with the doctor. Two hand-rolled copies had already
        # drifted apart once; a third would drift again.
        merged = resolve_config_overrides(project_config_path, apply_env_exclusion=False)

        if merged:
            # F5 follow-up (P1): checked BEFORE warn_unrecognised_config_keys
            # and BEFORE the generic except-Exception fail-open branch below
            # can turn this refusal into a silent revert-to-defaults. A
            # TRWConfig model_validator also rejects local_only (defense in
            # depth for a direct TRWConfig(**kwargs) caller that bypasses
            # this loader), but that raise happens INSIDE the try below and
            # would otherwise be swallowed by the malformed-config fail-open
            # path -- this call is what actually stops that.
            reject_local_only_mapping(merged, source=".trw/config.yaml")
            # PRD-QUAL-131-FR04: TRWConfig is extra="ignore", so any key it does
            # not define is about to be dropped without a word. Say so BEFORE the
            # constructor swallows it. Checked against the whole merged set
            # rather than the env-filtered one, because a key being shadowed by a
            # TRW_* variable does not make it recognised.
            warn_unrecognised_config_keys(
                merged, TRWConfig.model_fields, key_sources=lambda: config_key_sources(project_config_path)
            )
            # Exclude keys overridden by a TRW_ env var (env wins). The
            # platform_api_key is resolved above and intentionally kept even
            # when TRW_PLATFORM_API_KEY is set (its env precedence is already
            # applied), so it is exempt from the generic TRW_* exclusion.
            filtered = exclude_env_shadowed_keys(merged)
            # B71-106: the operator's platform contact switch is read live from the files by
            # trw_memory.platform_contact; caching the file value here would veto a later re-enable.
            filtered.pop("platform_contact_enabled", None)
            if filtered:
                return TRWConfig(**filtered)  # type: ignore[arg-type]
    except ConfigError:
        # local_only's refusal (raised just above, or by TRWConfig's own
        # model_validator) is a deliberate security gate, not a malformed-
        # config failure -- it must never be silently swallowed by the
        # fail-open branch below, in strict mode or not.
        raise
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


def config_for_trw_dir(trw_dir: Path) -> TRWConfig:
    """The config a project's own ``.trw`` resolves to: its ``config.yaml`` over the machine
    layer, under the environment -- the same cascade as the process config, never the cached one.

    A platform send reads its policy (contact switch, consent flags) through this from the
    ``.trw`` its payload was read from (``state._platform_trust.send_policy``).
    """
    return _build_config_unguarded(trw_dir / "config.yaml")


def _build_config() -> TRWConfig:
    """The cascade in :func:`_build_config_unguarded`, then the platform meta-tune gate."""
    return apply_platform_meta_tune_gate(_build_config_unguarded())


def reload_config(config: TRWConfig | None = None) -> None:
    """Reset the config singleton for project-switching or testing.

    Clears the cached TRWConfig so the next ``get_config()`` call rebuilds
    it from ``.trw/config.yaml`` and environment variables.  Pass an explicit
    *config* to inject a pre-built instance (useful in tests).

    Inside an install it resets only that install's config; the process
    singleton, which other threads share, is untouched (B71-118).

    Args:
        config: Optional replacement config. If *None*, the next
            ``get_config()`` call creates a fresh default instance.
    """
    from trw_mcp.state._project_root_binding import install_cache

    cache = install_cache()
    if cache is not None:
        if config is None:
            cache.pop("config", None)
        else:
            cache["config"] = config
        return
    global _singleton, _built_from
    with _build_lock:
        _singleton = config
        _built_from = None


# Backward-compatible alias (deprecated, use reload_config instead).
_reset_config = reload_config
