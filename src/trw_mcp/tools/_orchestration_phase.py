"""Phase validation helpers for orchestration tools (PRD-CORE-089-FR03)."""

from __future__ import annotations

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _check_framework_version_staleness(run_framework: str) -> str | None:
    """Compare run framework version against the current deployed version."""
    if not run_framework:
        return None

    try:
        config = get_config()
        reader = FileStateReader()
        trw_dir = resolve_project_root() / config.trw_dir
        version_path = trw_dir / config.frameworks_dir / "VERSION.yaml"
        if not reader.exists(version_path):
            return None

        version_data = reader.read_yaml(version_path)
        current_version = str(version_data.get("framework_version", ""))
        if not current_version or run_framework == current_version:
            return None

        return (
            f"Run uses framework {run_framework} but current is "
            f"{current_version}. Consider re-bootstrapping or "
            f"acknowledging the version delta."
        )
    except (StateError, ValueError, TypeError, OSError):
        return None
