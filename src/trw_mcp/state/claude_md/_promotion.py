"""Learning promotion logic — selecting which learnings go into CLAUDE.md."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._helpers import iter_yaml_entry_files
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()


def collect_promotable_learnings(
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
) -> list[dict[str, object]]:
    """Collect active learnings eligible for CLAUDE.md promotion.

    Reads from SQLite via the memory adapter. For mature entries
    (q_observations >= threshold), q_value is used instead of static
    impact for the promotion decision (PRD-CORE-004 1c).

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration instance.
        reader: File state reader instance (kept for API compat).

    Returns:
        List of high-impact learning entry dicts.
    """
    high_impact: list[dict[str, object]] = []

    try:
        from trw_mcp.state.memory_adapter import list_active_learnings

        all_active = list_active_learnings(trw_dir)
    except (ImportError, OSError, ValueError):
        return high_impact

    for data in all_active:
        try:
            impact = data.get("impact", 0.0)
            q_obs = int(str(data.get("q_observations", 0)))

            # Use q_value for mature entries, impact for cold-start
            if q_obs >= config.q_cold_start_threshold:
                score = float(str(data.get("q_value", impact)))
            else:
                score = float(str(impact)) if isinstance(impact, (int, float)) else 0.0

            # Apply time decay for accurate promotion decisions
            created_at_raw = str(data.get("created", ""))
            if created_at_raw:
                try:
                    from datetime import datetime as _dt

                    from trw_mcp.scoring import apply_time_decay

                    created_dt = _dt.fromisoformat(created_at_raw)
                    score = apply_time_decay(score, created_dt)
                except (ValueError, ImportError):
                    pass  # Malformed date — use raw score

            if score >= config.learning_promotion_impact:
                high_impact.append(data)
        except (ValueError, TypeError):  # per-item error handling: skip entries with malformed fields  # noqa: PERF203
            continue

    return high_impact


def collect_patterns(
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
) -> list[dict[str, object]]:
    """Collect pattern entries for CLAUDE.md sync.

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration instance.
        reader: File state reader instance.

    Returns:
        List of pattern entry dicts.
    """
    patterns: list[dict[str, object]] = []
    patterns_dir = trw_dir / config.patterns_dir
    if not patterns_dir.exists():
        return patterns

    for pattern_file in iter_yaml_entry_files(patterns_dir):
        try:
            patterns.append(reader.read_yaml(pattern_file))
        except (StateError, ValueError, TypeError):  # per-item error handling: skip corrupt pattern files  # noqa: PERF203
            continue

    return patterns


def collect_context_data(
    trw_dir: Path,
    config: TRWConfig,
    reader: FileStateReader,
) -> tuple[dict[str, object], dict[str, object]]:
    """Collect architecture and conventions context data.

    Args:
        trw_dir: Path to .trw directory.
        config: TRW configuration instance.
        reader: File state reader instance.

    Returns:
        Tuple of (architecture_data, conventions_data).
    """
    arch_data: dict[str, object] = {}
    conv_data: dict[str, object] = {}
    context_dir = trw_dir / config.context_dir
    try:
        if reader.exists(context_dir / "architecture.yaml"):
            arch_data = reader.read_yaml(context_dir / "architecture.yaml")
        if reader.exists(context_dir / "conventions.yaml"):
            conv_data = reader.read_yaml(context_dir / "conventions.yaml")
    except (StateError, ValueError, TypeError):
        logger.debug("context_yaml_load_failed", exc_info=True)
    return arch_data, conv_data
