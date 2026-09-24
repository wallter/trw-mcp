"""The resolved hook switches, published for the shell.

``TRWConfig`` is the only owner of ``hooks_enabled`` and ``learning_recall_enabled``.
A hook cannot walk the machine -> project -> env cascade, and a ``grep`` over
``config.yaml`` misreads flow mappings, quoted keys, the machine layer and every
``TRW_*`` override. So the resolved values are written to
``.trw/runtime/hook-flags`` as ``key=true|false`` lines, and ``lib-trw.sh`` reads
only that file. A missing file means the defaults: both on.

Published at server boot, at ``trw_session_start``, at install, and by
``trw-mcp hook-flags``, which trw-eval runs after it writes an overlay.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._loader import resolve_config_overrides
from trw_mcp.state.persistence import FileStateWriter

logger = structlog.get_logger(__name__)

HOOK_FLAG_FIELDS = ("hooks_enabled", "learning_recall_enabled")


def hook_flags_path(trw_dir: Path) -> Path:
    return trw_dir / "runtime" / "hook-flags"


def write_hook_flags(trw_dir: Path, config: TRWConfig | None = None) -> Path:
    """Write the switches as *config* resolves them, or as the cascade resolves them for *trw_dir*."""
    resolved = config or TRWConfig(**resolve_config_overrides(trw_dir / "config.yaml"))  # type: ignore[arg-type]
    path = hook_flags_path(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{field}={str(bool(getattr(resolved, field))).lower()}\n" for field in HOOK_FLAG_FIELDS)
    FileStateWriter().write_text(path, body)
    return path


def publish_hook_flags(trw_dir: Path, config: TRWConfig | None = None) -> None:
    """:func:`write_hook_flags` for callers that must not fail on it (boot, session start)."""
    try:
        write_hook_flags(trw_dir, config)
    except Exception:  # justified: fail-open, a stale file is logged and the caller proceeds
        logger.warning("hook_flags_publish_failed", trw_dir=str(trw_dir), exc_info=True)


def run_hook_flags_cli(_args: object) -> None:
    """``trw-mcp hook-flags``: publish for the current project; trw-eval runs it after an overlay."""
    from trw_mcp.state._paths import resolve_trw_dir

    print(write_hook_flags(resolve_trw_dir()))
