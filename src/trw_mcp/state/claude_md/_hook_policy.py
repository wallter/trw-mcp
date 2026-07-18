"""Hook-policy refresh used by the instruction-sync facade."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._file_ops import _write_hook_env_file
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._profiles import resolve_client_profile

logger = structlog.get_logger(__name__)


def refresh_hook_policy(trw_dir: Path, project_root: Path, config: TRWConfig, client: str) -> None:
    """Persist the active profile's hook flags without blocking instruction sync."""
    if client == "auto":
        from trw_mcp.state.claude_md._agents_md import _determine_write_target_decision

        decision = _determine_write_target_decision(client, config, project_root, "root")
        if decision.instruction_targets:
            profile = resolve_client_profile(decision.instruction_targets[0].client_id)
        elif decision.write_claude:
            profile = resolve_client_profile("claude-code")
        else:
            profile = config.client_profile
    else:
        profile = config.client_profile if client == "all" else resolve_client_profile(client)
    try:
        _write_hook_env_file(trw_dir, profile)
    except Exception:  # justified: hook policy refresh is fail-open for instruction sync
        logger.warning("hook_env_sync_failed", client=profile.client_id, exc_info=True)
