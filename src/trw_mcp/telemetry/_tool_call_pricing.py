"""Pricing-table lookup + USD cost estimation for tool-call telemetry.

Belongs to the ``tool_call_timing.py`` facade. Re-exported there for
back-compat.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger(__name__)

_PRICING_CACHE: dict[str, object] | None = None
_PRICING_PATH_CACHE: Path | None = None
_PRICING_CONFIG_CACHE: str | None = None
_PRICING_CONFIG_SEEN = False
_PRICING_CACHE_KEY: tuple[str, int] | None = None


def _resolve_pricing_path(configured: str | None = None) -> Path | None:
    """Resolve the active pricing table path from config or package defaults."""
    try:
        from trw_mcp.models.config import get_config

        configured = configured if configured is not None else str(get_config().pricing_table_path).strip()
        if configured:
            candidate = Path(configured).expanduser()
            if not candidate.is_absolute():
                try:
                    from trw_mcp.state._paths import resolve_project_root

                    candidate = (resolve_project_root() / candidate).resolve()
                except Exception:
                    candidate = candidate.resolve()
            return candidate
    except Exception:  # justified: fail-open, config resolution must not break pricing fallback
        logger.debug("pricing_path_config_resolution_failed", exc_info=True)

    try:
        from importlib.resources import as_file
        from importlib.resources import files as _pkg_files

        pricing_traversable = _pkg_files("trw_mcp.data").joinpath("pricing.yaml")
        if not pricing_traversable.is_file():
            return None
        with as_file(pricing_traversable) as candidate:
            return Path(candidate)
    except Exception:  # justified: fail-open, callers handle missing pricing path
        logger.debug("pricing_path_package_resolution_failed", exc_info=True)
        return None


def _load_pricing() -> dict[str, object]:
    """Resolve + cache ``pricing.yaml`` from the bundled data package.

    Uses ``importlib.resources`` traversable API + ``as_file`` to survive
    MultiplexedPath / namespace-package layouts (plain ``Path(str(...))``
    fails when files() returns a multiplexed traversable).
    """
    global _PRICING_CACHE, _PRICING_PATH_CACHE, _PRICING_CONFIG_CACHE, _PRICING_CONFIG_SEEN, _PRICING_CACHE_KEY
    try:
        from trw_mcp.models.config import get_config

        configured = str(get_config().pricing_table_path).strip()
    except Exception:  # justified: fall back to package pricing when config is unavailable
        logger.debug("pricing_path_config_resolution_failed", exc_info=True)
        configured = ""
    resolved_path = _resolve_pricing_path(configured)
    try:
        cache_key = (str(resolved_path), resolved_path.stat().st_mtime_ns) if resolved_path else ("", 0)
    except OSError:
        cache_key = (str(resolved_path), -1)
    if _PRICING_CACHE is not None and cache_key == _PRICING_CACHE_KEY:
        return _PRICING_CACHE
    _PRICING_CONFIG_CACHE = configured
    _PRICING_CONFIG_SEEN = True
    try:
        if resolved_path is None or not resolved_path.is_file():
            logger.warning("pricing_yaml_missing_fallback", path=str(resolved_path or ""))
            resolved_path = _resolve_pricing_path("")
            if resolved_path is None or not resolved_path.is_file():
                raise FileNotFoundError("bundled pricing.yaml is unavailable")
        _PRICING_PATH_CACHE = resolved_path
        try:
            with resolved_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except Exception:
            if not configured:
                raise
            logger.warning("pricing_yaml_parse_fallback", path=str(resolved_path))
            fallback = _resolve_pricing_path("")
            if fallback is None or fallback == resolved_path:
                raise
            with fallback.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            _PRICING_PATH_CACHE = fallback
        if not isinstance(data, dict):
            logger.warning("pricing_yaml_malformed_fallback", path=str(_PRICING_PATH_CACHE))
            fallback = _resolve_pricing_path("")
            if fallback is None or fallback == resolved_path:
                raise ValueError("pricing table is malformed and no bundled fallback is available")
            with fallback.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                raise TypeError("bundled pricing table is malformed")
            _PRICING_PATH_CACHE = fallback
        _PRICING_CACHE = data
        _PRICING_CACHE_KEY = (str(_PRICING_PATH_CACHE), _PRICING_PATH_CACHE.stat().st_mtime_ns)
        return _PRICING_CACHE
    except Exception:  # justified: boundary, pricing lookup must never break tool calls
        logger.warning("pricing_yaml_load_failed", exc_info=True)
        _PRICING_CACHE = {"version": "error", "models": {}}
        _PRICING_CACHE_KEY = cache_key
        return _PRICING_CACHE


def _pricing_version() -> str:
    return str(_load_pricing().get("version", "unknown"))


#: Model ids already reported as absent from the pricing table. Bounds the
#: warning to one line per distinct id per process instead of one per call.
_UNPRICED_MODELS_SEEN: set[str] = set()


def _usd_cost_estimate(
    *,
    model_id: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Look up per-1K rates and return an estimated USD cost for the call.

    The incoming id is matched by *family* rather than by exact key. One model
    reaches this function under several spellings — a dated snapshot
    (``claude-haiku-4-5-20251001``, which clients/llm.py used to stamp for its
    default model), a ``[1m]`` long-context rendering, a
    Vertex ``@``-pin, or a Bedrock provider prefix. The previous exact
    ``models.get(model_id)`` matched none of those, so a priced model could
    report ``$0.00`` — indistinguishable from a genuinely free call.

    A model with no row still estimates ``0.0``, but now says so once per
    distinct id rather than silently.
    """
    if not model_id:
        return 0.0
    table = _load_pricing()
    models = table.get("models", {})
    if not isinstance(models, dict):
        return 0.0

    from trw_mcp.models.config import match_model_family

    key = match_model_family(model_id, models)
    entry = models.get(key) if key is not None else None
    if not isinstance(entry, dict):
        if model_id not in _UNPRICED_MODELS_SEEN:
            _UNPRICED_MODELS_SEEN.add(model_id)
            logger.warning("pricing_model_unknown", model_id=model_id, estimate_usd=0.0)
        return 0.0
    in_rate = float(entry.get("input_per_1k", 0.0) or 0.0)
    out_rate = float(entry.get("output_per_1k", 0.0) or 0.0)
    threshold = entry.get("long_prompt_threshold_tokens")
    prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens
    if isinstance(threshold, int) and prompt_tokens > threshold:
        # Conservatively count cached prompt tokens; vendor threshold semantics are unspecified.
        in_rate = float(entry.get("long_prompt_input_per_1k", in_rate) or in_rate)
        out_rate = float(entry.get("long_prompt_output_per_1k", out_rate) or out_rate)
    cache_rate = float(entry.get("cache_read_per_1k", 0.0) or 0.0)
    if isinstance(threshold, int) and prompt_tokens > threshold:
        base_rate = float(entry.get("input_per_1k", 0.0) or 0.0)
        cache_rate = cache_rate * in_rate / base_rate if base_rate else cache_rate
    write_rate = float(entry.get("cache_write_per_1k", 0.0) or 0.0)
    if isinstance(threshold, int) and prompt_tokens > threshold:
        base_rate = float(entry.get("input_per_1k", 0.0) or 0.0)
        write_rate = write_rate * in_rate / base_rate if base_rate else write_rate
    return round(
        ((input_tokens / 1000.0) * in_rate)
        + ((output_tokens / 1000.0) * out_rate)
        + ((cache_read_tokens / 1000.0) * cache_rate)
        + ((cache_write_tokens / 1000.0) * write_rate),
        8,
    )


def clear_pricing_cache() -> None:
    """Drop the process-wide pricing cache. Test-only helper."""
    global _PRICING_CACHE, _PRICING_PATH_CACHE, _PRICING_CONFIG_CACHE, _PRICING_CONFIG_SEEN, _PRICING_CACHE_KEY
    _PRICING_CACHE = None
    _PRICING_PATH_CACHE = None
    _PRICING_CONFIG_CACHE = None
    _PRICING_CONFIG_SEEN = False
    _PRICING_CACHE_KEY = None
    _UNPRICED_MODELS_SEEN.clear()
