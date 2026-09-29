"""``_resolve_target_config``: the config a `doctor` TARGET project would run with.

Belongs to the ``_subcommands_doctor.py`` facade. Re-exported there for
back-compat -- ``_run_doctor`` and several tests import it from that module
path. Split out purely for the 350-effective-LOC module-size gate
(PRD-CORE-311-FR07 made room for the new ``sync_health`` row); behavior is
unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import TRWConfig

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

__all__ = ["resolve_target_config"]


def resolve_target_config(target: Path) -> TRWConfig:
    """Build the config the TARGET project records, not this process's defaults.

    ``TRWConfig()`` is the bare constructor: field defaults only, never the
    target's ``.trw/config.yaml``. Every row reading ``target_platforms`` or
    ``client_profile`` off it therefore printed ``claude-code`` for a codex
    project — the identical answer it would print for a project that recorded
    nothing at all, which makes the row unable to be wrong and unable to be
    right (PRD-CORE-262-FR05).

    Detection never enters this: ``target_platforms`` is the durable record the
    init path itself wrote, and directory presence cannot distinguish TRW's own
    scaffold from the user's.

    The cascade is not re-implemented here. It is
    :func:`~trw_mcp.models.config._loader.resolve_config_overrides`, the same
    function production builds from, because a hand-rolled copy had already
    drifted: this one dropped ``platform_api_key`` (correctly) but never
    re-resolved it from ``credentials.yaml``, and it omitted both the
    ``~/.trw/config.yaml`` layer and the ``TRW_*`` exclusion that preserves
    ``env > file`` precedence. So the doctor resolved a ``config.yaml`` value
    wherever an env var shadowed it and the live server resolved the env value
    — a row measuring against a setting the operator did not choose, which is
    the PRD-CORE-262-FR05 defect this docstring cites as its own justification.

    CORE262-10: ``_read_yaml_overrides`` documents "never raises" but does not
    honor that contract -- ``FileStateReader.read_yaml`` raises ``StateError``
    on malformed YAML (or a non-mapping top level), and nothing here caught
    it. ``_run_doctor`` calls this function BEFORE ``_doctor_core``'s per-check
    exception isolation even starts, so a malformed target ``config.yaml``
    used to abort the whole ``doctor`` invocation before a single row printed
    -- not even the ``config: FAIL`` row this exact input is supposed to
    produce. Falling back to ``TRWConfig()`` here is safe: ``_check_config``
    re-parses the same file independently and reports the parse failure as
    its own FAIL row.
    """
    from trw_mcp.exceptions import StateError
    from trw_mcp.models.config._loader import apply_platform_meta_tune_gate, resolve_config_overrides

    try:
        overrides = resolve_config_overrides(target / ".trw" / "config.yaml")
    except StateError:
        logger.warning("doctor_target_config_unreadable", path=str(target / ".trw" / "config.yaml"), exc_info=True)
        return TRWConfig()
    if not overrides:
        return TRWConfig()
    try:
        # PRD-FIX-137-FR03: the doctor reports the config the server would RUN
        # with, so the non-Linux meta-tune override applies here as well.
        return apply_platform_meta_tune_gate(TRWConfig(**overrides))  # type: ignore[arg-type]
    except Exception:  # justified: an invalid config.yaml is _check_config's verdict, not this row's
        logger.warning("doctor_target_config_invalid", path=str(target / ".trw" / "config.yaml"))
        return TRWConfig()
