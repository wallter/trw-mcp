"""Private helpers for orchestration tools — wave progress, deployment, version staleness.

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
from trw_mcp.models.typed_dicts import (
    DeployFrameworksVersionDataDict,
    StatusReversionLatestDict,
    StatusReversionMetricsDict,
    WaveDetailDict,
    WaveProgressDict,
    WaveShardCountsDict,
)
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
)

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def _compute_wave_progress(
    wave_data: dict[str, object],
    run_path: Path,
) -> WaveProgressDict | None:
    """Compute wave-level and shard-level progress summary.

    Args:
        wave_data: Parsed wave_manifest.yaml content.
        run_path: Path to the run directory (for reading shard manifest).

    Returns:
        Wave progress dict, or None if no waves found.
    """
    reader = FileStateReader()
    waves_raw = wave_data.get("waves", [])
    if not isinstance(waves_raw, list) or not waves_raw:
        return None

    shard_statuses: dict[str, str] = {}
    shard_manifest_path = run_path / "shards" / "manifest.yaml"
    if shard_manifest_path.exists():
        try:
            shard_data = reader.read_yaml(shard_manifest_path)
            raw_shards = shard_data.get("shards", [])
            if isinstance(raw_shards, list):
                for s in raw_shards:
                    if isinstance(s, dict):
                        sid = str(s.get("id", ""))
                        shard_statuses[sid] = str(s.get("status", "pending"))
        except (StateError, OSError, ValueError, TypeError):
            logger.debug("shard_manifest_load_failed", exc_info=True)

    completed_waves = 0
    active_wave: int | None = None
    wave_details: list[WaveDetailDict] = []

    for w in waves_raw:
        if not isinstance(w, dict):
            continue
        wave_num = int(w.get("wave", 0))
        wave_status = str(w.get("status", "pending"))
        wave_shard_ids = w.get("shards", [])
        if not isinstance(wave_shard_ids, list):
            wave_shard_ids = []

        counts: dict[str, int] = {
            "complete": 0,
            "active": 0,
            "pending": 0,
            "failed": 0,
            "partial": 0,
        }
        for sid in wave_shard_ids:
            st = shard_statuses.get(str(sid), "pending")
            if st in counts:
                counts[st] += 1

        if wave_status in ("complete", "partial"):
            completed_waves += 1
        elif wave_status == "active" or counts["active"] > 0:
            active_wave = wave_num

        wave_details.append(
            WaveDetailDict(
                wave=wave_num,
                status=wave_status,
                shards=WaveShardCountsDict(
                    total=len(wave_shard_ids),
                    complete=counts["complete"],
                    active=counts["active"],
                    pending=counts["pending"],
                    failed=counts["failed"],
                    partial=counts["partial"],
                ),
            )
        )

    return {
        "total_waves": len(waves_raw),
        "completed_waves": completed_waves,
        "active_wave": active_wave,
        "wave_details": wave_details,
    }


def _compute_reversion_metrics(
    events: list[dict[str, object]],
) -> StatusReversionMetricsDict:
    """Compute reversion frequency metrics from events.

    Args:
        events: List of event dicts from events.jsonl.

    Returns:
        Reversion metrics dict with count, rate, by_trigger, classification, latest.
    """
    revert_events = [e for e in events if e.get("event") == "phase_revert"]
    phase_enter_events = [e for e in events if e.get("event") == "phase_enter"]

    revert_count = len(revert_events)
    total_transitions = revert_count + len(phase_enter_events)
    rate = revert_count / total_transitions if total_transitions > 0 else 0.0

    by_trigger: dict[str, int] = {}
    for evt in revert_events:
        trigger = str(evt.get("trigger_classified", evt.get("trigger", "other")))
        by_trigger[trigger] = by_trigger.get(trigger, 0) + 1

    # Classification with configurable thresholds
    config = get_config()
    if rate >= config.reversion_rate_concerning:
        classification = "concerning"
    elif rate >= config.reversion_rate_elevated:
        classification = "elevated"
    else:
        classification = "healthy"

    # Latest reversion
    latest: StatusReversionLatestDict | None = None
    if revert_events:
        last = revert_events[-1]
        latest = StatusReversionLatestDict(
            from_phase=str(last.get("from_phase", "")),
            to_phase=str(last.get("to_phase", "")),
            trigger=str(last.get("trigger_classified", last.get("trigger", ""))),
            reason=str(last.get("reason", "")),
            ts=str(last.get("ts", "")),
        )

    return {
        "count": revert_count,
        "rate": round(rate, 4),
        "by_trigger": by_trigger,
        "classification": classification,
        "latest": latest,
    }


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


def _check_framework_version_staleness(run_framework: str) -> str | None:
    """Compare run's framework version against the current deployed version.

    Args:
        run_framework: Framework version string from run.yaml.

    Returns:
        Warning message string if versions differ, None if current or unreadable.
    """
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
