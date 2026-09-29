"""Hook-policy refresh used by the instruction-sync facade."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._file_ops import write_hook_env_for_clients
from trw_mcp.bootstrap._utils import resolve_client_write_targets
from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


def refresh_hook_policy(trw_dir: Path, project_root: Path, config: TRWConfig, client: str) -> list[str]:
    """Persist every synced client's hook flags without blocking instruction sync.

    Writes ``hook-env.d/<key>.sh`` for each client the sync is for, the way ``init`` and ``update`` do:
    ``auto`` means the project's recorded clients (detection only where nothing is recorded), ``all``
    every supported client, and a named client that one. It used to write a single profile (for
    ``all`` the config's, for ``auto`` the first detected target), which left every other installed
    client's hooks reading stale settings (queued row (b) of PRD-CORE-305 FR07; trw_assess chose the
    recorded list over raw detection, 0.68). Returns operator-facing warnings (a client whose TRW
    hooks the project's resolved ``hooks_enabled`` would silence) for the caller's result payload.
    """
    del config  # the clients come from the project's record, not the serving config's profile
    warnings: list[str] = []
    try:
        client_ids = resolve_client_write_targets(project_root, None if client == "auto" else client)
    except Exception:  # justified: hook policy refresh is fail-open for instruction sync
        logger.warning("hook_env_sync_failed", client=client, exc_info=True)
        return warnings
    write_hook_env_for_clients(trw_dir, client_ids, warnings=warnings)
    return warnings
