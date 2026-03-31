"""Core build check caching utilities.

Contains ``cache_build_status`` and ``_cache_to_context`` for persisting
build results to ``.trw/context/``.

The subprocess-based ``run_build_check`` has been removed (PRD-CORE-098).
Agents now run tests via Bash and report results through the
``trw_build_check`` tool's parameter-based reporter API.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.build import BuildStatus
from trw_mcp.state.persistence import FileStateWriter, model_to_dict

logger = structlog.get_logger(__name__)


def _cache_to_context(
    trw_dir: Path,
    filename: str,
    data: dict[str, object],
) -> Path:
    """Write a result dict to .trw/context/<filename>.

    Args:
        trw_dir: Path to .trw directory.
        filename: YAML filename within context/.
        data: Dict to serialize.

    Returns:
        Path to the written file.
    """
    writer = FileStateWriter()
    context_dir = trw_dir / "context"
    writer.ensure_dir(context_dir)
    cache_path = context_dir / filename
    writer.write_yaml(cache_path, data)
    return cache_path


def cache_build_status(trw_dir: Path, status: BuildStatus) -> Path:
    """Write BuildStatus to .trw/context/build-status.yaml.

    Args:
        trw_dir: Path to .trw directory.
        status: BuildStatus to cache.

    Returns:
        Path to the cached file.
    """
    writer = FileStateWriter()
    context_dir = trw_dir / "context"
    writer.ensure_dir(context_dir)
    cache_path = context_dir / "build-status.yaml"
    writer.write_yaml(cache_path, model_to_dict(status))
    return cache_path
