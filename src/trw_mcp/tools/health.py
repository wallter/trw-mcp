"""TRW flywheel health diagnostic tool — PRD-CORE-027.

Aggregates Q-learning activations, event stream health, recall receipts,
access distribution, source attribution, and ceremony compliance to
produce a go/no-go recommendation.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.health import HealthReport
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()


def _scan_learnings(entries_dir: Path) -> dict[str, object]:
    """Scan all learning entries and compute aggregate metrics.

    Returns dict with keys used to populate HealthReport fields.
    """
    total = 0
    active = 0
    high_impact = 0
    q_active = 0
    q_obs_sum = 0
    access_sum = 0
    never_accessed = 0
    source_human = 0
    source_agent = 0
    source_unset = 0

    if not entries_dir.is_dir():
        return {
            "total": 0, "active": 0, "high_impact": 0,
            "q_activations": 0, "q_avg_observations": 0.0,
            "access_total": 0, "access_mean": 0.0,
            "entries_never_accessed": 0,
            "source_human": 0, "source_agent": 0, "source_unset": 0,
        }

    for entry_file in entries_dir.glob("*.yaml"):
        if entry_file.name == "index.yaml":
            continue
        try:
            data = _reader.read_yaml(entry_file)
        except Exception:  # noqa: BLE001
            continue

        total += 1
        status = str(data.get("status", "active"))
        if status == "active":
            active += 1
        impact = float(data.get("impact", 0.5))
        if impact >= 0.7:
            high_impact += 1

        q_obs = int(data.get("q_observations", 0))
        if q_obs > 0:
            q_active += 1
            q_obs_sum += q_obs

        ac = int(data.get("access_count", 0))
        access_sum += ac
        if ac == 0:
            never_accessed += 1

        src = str(data.get("source_type", ""))
        if src == "human":
            source_human += 1
        elif src == "agent":
            source_agent += 1
        else:
            source_unset += 1

    return {
        "total": total,
        "active": active,
        "high_impact": high_impact,
        "q_activations": q_active,
        "q_avg_observations": (q_obs_sum / q_active) if q_active > 0 else 0.0,
        "access_total": access_sum,
        "access_mean": (access_sum / total) if total > 0 else 0.0,
        "entries_never_accessed": never_accessed,
        "source_human": source_human,
        "source_agent": source_agent,
        "source_unset": source_unset,
    }


def _scan_events(run_dir: Path | None) -> dict[str, object]:
    """Scan events.jsonl for event stream health metrics."""
    events_total = 0
    type_dist: dict[str, int] = {}
    reflections = 0
    syncs = 0

    if run_dir is None:
        return {
            "events_total": 0, "event_type_distribution": {},
            "reflections_found": 0, "claude_md_syncs_found": 0,
        }

    events_path = run_dir / "meta" / "events.jsonl"
    if not events_path.exists():
        return {
            "events_total": 0, "event_type_distribution": {},
            "reflections_found": 0, "claude_md_syncs_found": 0,
        }

    try:
        events = _reader.read_jsonl(events_path)
    except Exception:  # noqa: BLE001
        events = []

    for evt in events:
        events_total += 1
        etype = str(evt.get("event", "unknown"))
        type_dist[etype] = type_dist.get(etype, 0) + 1
        if etype in ("reflection_complete", "trw_reflect_complete"):
            reflections += 1
        if etype in ("claude_md_sync", "claude_md_synced"):
            syncs += 1

    return {
        "events_total": events_total,
        "event_type_distribution": type_dist,
        "reflections_found": reflections,
        "claude_md_syncs_found": syncs,
    }


def _count_recall_receipts(trw_dir: Path) -> int:
    """Count total recall receipts in .trw/learnings/receipts/."""
    receipts_dir = trw_dir / _config.learnings_dir / "receipts"
    if not receipts_dir.is_dir():
        return 0
    return sum(1 for _ in receipts_dir.glob("*.yaml"))


def _find_active_run(trw_dir: Path) -> Path | None:
    """Find most recent active run directory."""
    project_root = trw_dir.parent
    task_root = project_root / _config.task_root
    if not task_root.exists():
        return None

    latest_name = ""
    latest_dir: Path | None = None
    for task_dir in task_root.iterdir():
        runs_dir = task_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for run_dir in runs_dir.iterdir():
            run_yaml = run_dir / "meta" / "run.yaml"
            if run_yaml.exists() and run_dir.name > latest_name:
                latest_name = run_dir.name
                latest_dir = run_dir
    return latest_dir


def _assess(report: HealthReport) -> tuple[str, list[str]]:
    """Produce recommendation and issues list from metrics."""
    issues: list[str] = []

    if report.q_activations == 0:
        issues.append("Q-learning has 0 activations — reward signals not reaching scoring")
    if report.events_total == 0:
        issues.append("Event stream empty — no events logged in active run")
    if report.total_learnings > 0 and report.entries_never_accessed == report.total_learnings:
        issues.append("All learnings have 0 access count — trw_recall not being used")
    if report.reflections_found == 0 and report.events_total > 0:
        issues.append("No reflection events found despite logged events")
    if report.total_learnings > 0 and report.source_unset > report.total_learnings * 0.5:
        issues.append(f"{report.source_unset}/{report.total_learnings} learnings lack source attribution")
    if report.high_impact_learnings == 0 and report.total_learnings > 10:
        issues.append("No high-impact learnings (>=0.7) despite 10+ entries")

    if len(issues) >= 3:
        return "blocked", issues
    if len(issues) >= 1:
        return "caution", issues
    return "go", issues


def compute_health(trw_dir: Path, run_dir: Path | None = None) -> HealthReport:
    """Compute the full flywheel health report.

    Args:
        trw_dir: Path to the .trw/ directory.
        run_dir: Optional active run directory for event stream analysis.

    Returns:
        Populated HealthReport with recommendation.
    """
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    learning_metrics = _scan_learnings(entries_dir)
    event_metrics = _scan_events(run_dir)
    receipts_count = _count_recall_receipts(trw_dir)

    report = HealthReport(
        q_activations=int(learning_metrics["q_activations"]),
        q_avg_observations=float(learning_metrics["q_avg_observations"]),
        events_total=int(event_metrics["events_total"]),
        event_type_distribution=dict(event_metrics["event_type_distribution"]),
        recall_receipts_count=receipts_count,
        access_total=int(learning_metrics["access_total"]),
        access_mean=float(learning_metrics["access_mean"]),
        entries_never_accessed=int(learning_metrics["entries_never_accessed"]),
        source_human=int(learning_metrics["source_human"]),
        source_agent=int(learning_metrics["source_agent"]),
        source_unset=int(learning_metrics["source_unset"]),
        total_learnings=int(learning_metrics["total"]),
        active_learnings=int(learning_metrics["active"]),
        high_impact_learnings=int(learning_metrics["high_impact"]),
        reflections_found=int(event_metrics["reflections_found"]),
        claude_md_syncs_found=int(event_metrics["claude_md_syncs_found"]),
    )

    recommendation, issues = _assess(report)
    report.recommendation = recommendation
    report.issues = issues

    logger.info(
        "health_computed",
        recommendation=recommendation,
        issues=len(issues),
        total_learnings=report.total_learnings,
        q_activations=report.q_activations,
    )
    return report


def register_health_tools(server: FastMCP) -> None:
    """Register flywheel health diagnostic tool on the MCP server."""

    @server.tool()
    def trw_health(run_path: str | None = None) -> dict[str, object]:
        """Flywheel health diagnostic — aggregates Q-learning, events, recall, ceremony metrics.

        Returns a structured health report with go/caution/blocked recommendation
        and a list of specific issues requiring attention.

        Args:
            run_path: Optional path to active run directory for event analysis.
        """
        trw_dir = resolve_trw_dir()

        if run_path:
            active_run = Path(run_path).resolve()
        else:
            active_run = _find_active_run(trw_dir)

        report = compute_health(trw_dir, active_run)

        return {
            "recommendation": report.recommendation,
            "issues": report.issues,
            "q_learning": {
                "activations": report.q_activations,
                "avg_observations": round(report.q_avg_observations, 2),
            },
            "event_stream": {
                "total": report.events_total,
                "type_distribution": report.event_type_distribution,
            },
            "recall": {
                "receipts": report.recall_receipts_count,
            },
            "learnings": {
                "total": report.total_learnings,
                "active": report.active_learnings,
                "high_impact": report.high_impact_learnings,
                "never_accessed": report.entries_never_accessed,
                "access_mean": round(report.access_mean, 2),
            },
            "source_attribution": {
                "human": report.source_human,
                "agent": report.source_agent,
                "unset": report.source_unset,
            },
            "ceremony": {
                "reflections": report.reflections_found,
                "claude_md_syncs": report.claude_md_syncs_found,
            },
        }
