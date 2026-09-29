"""Phase validation helpers for orchestration tools (PRD-CORE-089-FR03)."""

from __future__ import annotations

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import (
    StatusReversionLatestDict,
    StatusReversionMetricsDict,
)
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _compute_reversion_metrics(
    events: list[dict[str, object]],
) -> StatusReversionMetricsDict:
    """Compute reversion frequency metrics from events."""
    revert_events = [event for event in events if event.get("event") == "phase_revert"]
    phase_enter_events = [event for event in events if event.get("event") == "phase_enter"]

    revert_count = len(revert_events)
    total_transitions = revert_count + len(phase_enter_events)
    rate = revert_count / total_transitions if total_transitions > 0 else 0.0

    by_trigger: dict[str, int] = {}
    for event in revert_events:
        trigger = str(event.get("trigger_classified", event.get("trigger", "other")))
        by_trigger[trigger] = by_trigger.get(trigger, 0) + 1

    config = get_config()
    if rate >= config.reversion_rate_concerning:
        classification = "concerning"
    elif rate >= config.reversion_rate_elevated:
        classification = "elevated"
    else:
        classification = "healthy"

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
