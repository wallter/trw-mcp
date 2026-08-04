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


def _resolve_pricing_path() -> Path | None:
    """Resolve the active pricing table path from config or package defaults."""
    try:
        from trw_mcp.models.config import get_config

        configured = str(get_config().pricing_table_path).strip()
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
    global _PRICING_CACHE, _PRICING_PATH_CACHE
    resolved_path = _resolve_pricing_path()
    if _PRICING_CACHE is not None and resolved_path == _PRICING_PATH_CACHE:
        return _PRICING_CACHE
    try:
        if resolved_path is None or not resolved_path.is_file():
            logger.warning("pricing_yaml_missing", path=str(resolved_path or ""))
            _PRICING_CACHE = {"version": "unresolved", "models": {}}
            _PRICING_PATH_CACHE = resolved_path
            return _PRICING_CACHE
        _PRICING_PATH_CACHE = resolved_path
        with resolved_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            logger.warning("pricing_yaml_malformed", path=str(_PRICING_PATH_CACHE))
            _PRICING_CACHE = {"version": "malformed", "models": {}}
            return _PRICING_CACHE
        _PRICING_CACHE = data
        return _PRICING_CACHE
    except Exception:  # justified: boundary, pricing lookup must never break tool calls
        logger.warning("pricing_yaml_load_failed", exc_info=True)
        _PRICING_CACHE = {"version": "error", "models": {}}
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
) -> float:
    """Look up per-1K rates and return an estimated USD cost for the call.

    The incoming id is matched by *family* rather than by exact key. One model
    reaches this function under several spellings — a dated snapshot
    (``claude-haiku-4-5-20251001``, which is exactly what ``clients/llm.py``
    stamps for its own default model), a ``[1m]`` long-context rendering, a
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
    return round(((input_tokens / 1000.0) * in_rate) + ((output_tokens / 1000.0) * out_rate), 8)


def clear_pricing_cache() -> None:
    """Drop the process-wide pricing cache. Test-only helper."""
    global _PRICING_CACHE, _PRICING_PATH_CACHE
    _PRICING_CACHE = None
    _PRICING_PATH_CACHE = None
    _UNPRICED_MODELS_SEEN.clear()
