"""Private helpers for orchestration tools — deployment and bundled-file access.

Extracted from orchestration.py to stay under the 600-line module size gate.
Parent facade: ``trw_mcp.tools.orchestration``

Imports ``get_config`` directly from ``trw_mcp.models.config`` (not via
orchestration.py) to avoid a circular import -- orchestration.py re-exports
symbols from this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import DeployFrameworksVersionDataDict
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
)
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness as _check_framework_version_staleness,
    _compute_reversion_metrics as _compute_reversion_metrics,
    _compute_wave_progress as _compute_wave_progress,
)

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def _get_bundled_file(filename: str, subdir: str = "") -> str | None:
    """Load a bundled file from the package data directory.

    Args:
        filename: File to load (e.g., "framework.md", "claude_md.md").
        subdir: Optional subdirectory under data/ (e.g., "templates").

    Returns:
        File text content, or None if not found.
    """
    data_dir = (Path(__file__).parent.parent / "data").resolve()
    if subdir:
        data_dir = data_dir / subdir
    file_path = (data_dir / filename).resolve()

    # QUAL-042-FR04: Path containment — prevent traversal outside data dir
    if not file_path.is_relative_to(data_dir):
        logger.warning("bundled_file_path_traversal", filename=filename, subdir=subdir)
        return None

    if file_path.exists():
        return file_path.read_text(encoding="utf-8")
    return None


def _get_package_version() -> str:
    """Get the trw-mcp package version from importlib.metadata.

    Returns:
        Package version string, or "unknown" if not installed.
    """
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version("trw-mcp")
    except Exception:  # justified: import-guard, package may not be installed in editable mode
        return "unknown"


def _deploy_frameworks(trw_dir: Path) -> dict[str, str]:
    """Deploy bundled frameworks to .trw/frameworks/ as read-only references.

    Writes FRAMEWORK.md, AARE-F-FRAMEWORK.md, and VERSION.yaml.
    Skips if VERSION.yaml matches current bundled versions (idempotent).

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        Dictionary with deployment status and version info.
    """
    config = get_config()
    reader = FileStateReader()
    writer = FileStateWriter()
    frameworks_dir = trw_dir / config.frameworks_dir
    writer.ensure_dir(frameworks_dir)

    version_path = frameworks_dir / "VERSION.yaml"
    current_fw_version = config.framework_version
    current_aaref_version = config.aaref_version
    current_pkg_version = _get_package_version()

    # Check existing VERSION.yaml for skip logic
    if reader.exists(version_path):
        existing = reader.read_yaml(version_path)
        existing_versions = (
            str(existing.get("framework_version", "")),
            str(existing.get("aaref_version", "")),
            str(existing.get("trw_mcp_version", "")),
        )
        if existing_versions == (current_fw_version, current_aaref_version, current_pkg_version):
            return {"status": "up_to_date", "framework_version": current_fw_version}

        # Version mismatch — log upgrade event
        _events.log_event(
            trw_dir / "upgrade_events.jsonl",
            "framework_upgrade",
            {
                "old_framework": existing_versions[0],
                "new_framework": current_fw_version,
                "old_aaref": existing_versions[1],
                "new_aaref": current_aaref_version,
                "old_pkg": existing_versions[2],
                "new_pkg": current_pkg_version,
            },
        )

    framework_files = [
        ("framework.md", "FRAMEWORK.md"),
        ("aaref.md", "AARE-F-FRAMEWORK.md"),
    ]
    for source_name, target_name in framework_files:
        content = _get_bundled_file(source_name)
        if content:
            (frameworks_dir / target_name).write_text(content, encoding="utf-8")

    version_data: DeployFrameworksVersionDataDict = {
        "framework_version": current_fw_version,
        "aaref_version": current_aaref_version,
        "trw_mcp_version": current_pkg_version,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }
    writer.write_yaml(version_path, cast("dict[str, object]", version_data))

    logger.info(
        "frameworks_deployed",
        framework_version=current_fw_version,
        aaref_version=current_aaref_version,
    )

    return {
        "status": "deployed",
        "framework_version": current_fw_version,
        "aaref_version": current_aaref_version,
    }


def _deploy_templates(trw_dir: Path) -> None:
    """Copy bundled CLAUDE.md template to .trw/templates/ if not present.

    Does NOT overwrite existing template (preserves project customizations).

    Args:
        trw_dir: Path to the .trw directory.
    """
    config = get_config()
    writer = FileStateWriter()
    templates_dir = trw_dir / config.templates_dir
    writer.ensure_dir(templates_dir)

    template_path = templates_dir / "claude_md.md"
    if template_path.exists():
        return  # Preserve project customization

    template_data = _get_bundled_file("claude_md.md", subdir="templates")
    if template_data:
        template_path.write_text(template_data, encoding="utf-8")

