"""TRW findings pipeline tools — register, query, convert to PRD.

These 3 tools implement the structured findings pipeline (PRD-CORE-010)
for capturing, querying, and converting research findings.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.finding import (
    FindingEntry,
    FindingRef,
    FindingSeverity,
    FindingStatus,
    FindingsIndex,
    FindingsRegistry,
)
from trw_mcp.state._paths import resolve_project_root, resolve_run_path
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()

# Severity sort order for query results (lower = higher priority)
_SEVERITY_ORDER: dict[str, int] = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
}


def register_findings_tools(server: FastMCP) -> None:
    """Register all 3 findings pipeline tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_finding_register(
        summary: str,
        detail: str,
        severity: str = "medium",
        component: str = "",
        tags: list[str] | None = None,
        wave: int = 1,
        shard: int = 1,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Register a structured research finding with auto-ID and dedup detection.

        Args:
            summary: One-line summary of the finding.
            detail: Detailed description with context and evidence.
            severity: Finding severity (critical, high, medium, low, info).
            component: Component or module the finding relates to.
            tags: Optional categorization tags.
            wave: Wave number where the finding was discovered.
            shard: Shard number where the finding was discovered.
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        # Validate severity
        try:
            sev = FindingSeverity(severity.lower())
        except ValueError:
            valid_sevs = [s.value for s in FindingSeverity]
            raise ValidationError(
                f"Invalid severity: {severity!r}. Valid: {valid_sevs}",
                severity=severity,
            )

        # Resolve run path
        resolved_run_path = resolve_run_path(run_path)

        # Ensure findings directories exist
        findings_dir = resolved_run_path / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir
        _writer.ensure_dir(entries_dir)

        # Auto-generate finding ID
        finding_id = _generate_finding_id(wave, shard, entries_dir)

        # Run dedup detection
        dedup_match = _check_dedup(summary, detail, findings_dir)

        # Determine prd_candidate
        prd_candidate = sev in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)

        # Read run_id from run.yaml if available
        run_id = _read_run_id(resolved_run_path)

        # Create FindingEntry
        entry = FindingEntry(
            id=finding_id,
            summary=summary,
            detail=detail,
            severity=sev,
            status=FindingStatus.OPEN,
            component=component,
            tags=tags or [],
            source_shard=f"S{shard}",
            source_wave=wave,
            run_id=run_id,
            prd_candidate=prd_candidate,
            dedup_of=dedup_match,
        )

        # Write per-run entry file
        entry_path = entries_dir / f"{finding_id}.yaml"
        _writer.write_yaml(entry_path, model_to_dict(entry))

        # Update per-run FindingsIndex
        _update_run_index(findings_dir, entry)

        # Upsert into global FindingsRegistry
        _upsert_global_registry(entry, run_id)

        logger.info(
            "trw_finding_registered",
            finding_id=finding_id,
            severity=sev.value,
            dedup_match=dedup_match,
            prd_candidate=prd_candidate,
        )

        return {
            "finding_id": finding_id,
            "path": str(entry_path),
            "dedup_match": dedup_match,
            "prd_candidate": prd_candidate,
            "severity": sev.value,
            "status": "open",
        }

    @server.tool()
    def trw_finding_to_prd(
        finding_id: str,
        run_path: str | None = None,
        category: str = "CORE",
        priority: str = "P1",
    ) -> dict[str, object]:
        """Convert a registered finding into a PRD with bidirectional traceability.

        Args:
            finding_id: Finding ID to convert (e.g., "F-W1-S1-001").
            run_path: Path to the run directory. Auto-detects if not provided.
            category: PRD category for the generated PRD.
            priority: PRD priority level.
        """
        resolved_run_path = resolve_run_path(run_path)
        findings_dir = resolved_run_path / _config.findings_dir
        entries_dir = findings_dir / _config.findings_entries_dir

        # Load finding
        entry_path = entries_dir / f"{finding_id}.yaml"
        if not entry_path.exists():
            raise StateError(
                f"Finding not found: {finding_id}", path=str(entry_path),
            )

        entry_data = _reader.read_yaml(entry_path)
        entry = FindingEntry.model_validate(entry_data)

        # Use the finding summary as PRD input text
        input_text = f"{entry.summary}\n\n{entry.detail}"
        prd_result = _create_prd_from_finding(input_text, category, priority)

        prd_id = str(prd_result.get("prd_id", ""))
        prd_path = str(prd_result.get("output_path", ""))

        # Patch PRD frontmatter with traceability.implements
        if prd_path and Path(prd_path).exists():
            from trw_mcp.state.prd_utils import update_frontmatter

            update_frontmatter(Path(prd_path), {
                "traceability": {"implements": [finding_id]},
            })

        # Update finding with target_prd and status
        entry_data["target_prd"] = prd_id
        if entry_data.get("status") == FindingStatus.OPEN.value:
            entry_data["status"] = FindingStatus.ACKNOWLEDGED.value
        entry_data["updated"] = str(date.today())
        _writer.write_yaml(entry_path, entry_data)

        # Update global registry
        _update_registry_ref(finding_id, prd_id, FindingStatus.ACKNOWLEDGED.value)

        logger.info(
            "trw_finding_to_prd",
            finding_id=finding_id,
            prd_id=prd_id,
        )

        return {
            "prd_id": prd_id,
            "prd_path": prd_path,
            "finding_id": finding_id,
            "status": "acknowledged",
        }

    @server.tool()
    def trw_finding_query(
        severity: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        component: str | None = None,
        run_path: str | None = None,
        include_global: bool = True,
    ) -> dict[str, object]:
        """Query findings with filtering by severity, status, tags, or component.

        Args:
            severity: Filter by severity level (critical, high, medium, low, info).
            status: Filter by status (open, acknowledged, in-progress, resolved, wont-fix).
            tags: Filter by tags (any-match).
            component: Filter by component (substring match).
            run_path: Path to the run directory. Auto-detects if not provided.
            include_global: Whether to include the global registry in results.
        """
        results: list[dict[str, object]] = []
        sources: list[str] = []

        # Query per-run findings
        try:
            resolved_run_path = resolve_run_path(run_path)
            findings_dir = resolved_run_path / _config.findings_dir
            entries_dir = findings_dir / _config.findings_entries_dir
            if entries_dir.exists():
                sources.append("per-run")
                for entry_file in sorted(entries_dir.glob("*.yaml")):
                    try:
                        data = _reader.read_yaml(entry_file)
                        if _matches_filters(data, severity, status, tags, component):
                            data["source"] = "per-run"
                            results.append(data)
                    except (StateError, ValueError, TypeError) as exc:
                        logger.debug(
                            "finding_entry_read_failed",
                            path=str(entry_file),
                            error=str(exc),
                        )
                        continue
        except StateError as exc:
            logger.debug("per_run_findings_unavailable", error=str(exc))

        # Query global registry
        if include_global:
            registry_path = _get_registry_path()
            if registry_path.exists():
                sources.append("global")
                try:
                    reg_data = _reader.read_yaml(registry_path)
                    reg_entries = reg_data.get("entries", [])
                    if isinstance(reg_entries, list):
                        for ref in reg_entries:
                            if isinstance(ref, dict) and _matches_filters(
                                ref, severity, status, tags, component,
                            ):
                                ref_id = str(ref.get("id", ""))
                                if not any(r.get("id") == ref_id for r in results):
                                    ref["source"] = "global"
                                    results.append(ref)
                except (StateError, ValueError, TypeError) as exc:
                    logger.warning(
                        "global_registry_read_failed",
                        error=str(exc),
                    )

        # Sort: critical first, then by severity order, then by id
        results.sort(
            key=lambda r: (
                _SEVERITY_ORDER.get(str(r.get("severity", "medium")), 2),
                str(r.get("id", "")),
            ),
        )

        logger.info(
            "trw_finding_queried",
            total=len(results),
            sources=sources,
        )

        return {
            "findings": results,
            "total": len(results),
            "sources": sources,
        }


# --- Private helpers ---


def _generate_finding_id(wave: int, shard: int, entries_dir: Path) -> str:
    """Generate a finding ID with auto-incrementing sequence.

    Format: ``F-W{wave}-S{shard}-{seq:03d}``

    Args:
        wave: Wave number.
        shard: Shard number.
        entries_dir: Directory containing existing finding YAML files.

    Returns:
        New finding ID string.
    """
    prefix = f"F-W{wave}-S{shard}-"
    max_seq = 0

    if entries_dir.exists():
        for entry_file in entries_dir.glob(f"{prefix}*.yaml"):
            stem = entry_file.stem
            suffix = stem[len(prefix):]
            try:
                seq = int(suffix)
                if seq > max_seq:
                    max_seq = seq
            except ValueError:
                continue

    return f"{prefix}{max_seq + 1:03d}"


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """Compute word-set Jaccard similarity between two texts.

    Args:
        text_a: First text.
        text_b: Second text.

    Returns:
        Jaccard similarity coefficient (0.0 to 1.0).
    """
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())

    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _check_dedup(
    summary: str,
    detail: str,
    findings_dir: Path,
) -> str | None:
    """Check for duplicate findings using Jaccard similarity.

    Args:
        summary: New finding summary.
        detail: New finding detail.
        findings_dir: Per-run findings directory.

    Returns:
        ID of best matching duplicate, or None if no match.
    """
    new_text = f"{summary} {detail}"
    entries_dir = findings_dir / _config.findings_entries_dir

    if not entries_dir.exists():
        return None

    best_match: str | None = None
    best_score = 0.0

    for entry_file in entries_dir.glob("*.yaml"):
        try:
            data = _reader.read_yaml(entry_file)
            existing_text = f"{data.get('summary', '')} {data.get('detail', '')}"
            score = _jaccard_similarity(new_text, existing_text)
            if score >= _config.finding_dedup_threshold and score > best_score:
                best_score = score
                best_match = str(data.get("id", entry_file.stem))
        except (StateError, ValueError, TypeError):
            continue

    return best_match


def _read_run_id(run_path: Path) -> str:
    """Read run_id from run.yaml.

    Args:
        run_path: Path to the run directory.

    Returns:
        Run ID string, or empty string if not found.
    """
    run_yaml = run_path / "meta" / "run.yaml"
    if run_yaml.exists():
        try:
            data = _reader.read_yaml(run_yaml)
            return str(data.get("run_id", ""))
        except (StateError, ValueError, TypeError) as exc:
            logger.debug("run_id_read_failed", path=str(run_yaml), error=str(exc))
    return ""


def _update_run_index(findings_dir: Path, entry: FindingEntry) -> None:
    """Update the per-run FindingsIndex with a new entry.

    Args:
        findings_dir: Per-run findings directory.
        entry: Finding entry to add.
    """
    index_path = findings_dir / "index.yaml"
    index_data: dict[str, object]

    entries: list[dict[str, object]] = []
    if index_path.exists():
        try:
            raw = _reader.read_yaml(index_path)
            raw_entries = raw.get("entries", [])
            if isinstance(raw_entries, list):
                entries = list(raw_entries)
        except (StateError, ValueError, TypeError) as exc:
            logger.warning("findings_index_read_failed", error=str(exc))

    # Append new entry (as dict for serialization)
    entries.append(model_to_dict(entry))

    index_data = {
        "entries": entries,
        "total_count": len(entries),
        "last_updated": str(date.today()),
    }
    _writer.write_yaml(index_path, index_data)


def _get_registry_path() -> Path:
    """Get the path to the global findings registry.

    Returns:
        Path to .trw/findings/registry.yaml.
    """
    project_root = resolve_project_root()
    return (
        project_root
        / _config.trw_dir
        / _config.findings_dir
        / _config.findings_registry_file
    )


def _upsert_global_registry(entry: FindingEntry, run_id: str) -> None:
    """Upsert a FindingRef into the global FindingsRegistry.

    Args:
        entry: Finding entry to add as a reference.
        run_id: Run ID for the finding.
    """
    registry_path = _get_registry_path()
    _writer.ensure_dir(registry_path.parent)

    # Load existing registry
    entries: list[dict[str, object]] = []
    runs_indexed: list[str] = []

    if registry_path.exists():
        try:
            raw = _reader.read_yaml(registry_path)
            raw_entries = raw.get("entries", [])
            entries = list(raw_entries) if isinstance(raw_entries, list) else []
            raw_runs = raw.get("runs_indexed", [])
            runs_indexed = [str(r) for r in raw_runs] if isinstance(raw_runs, list) else []
        except (StateError, ValueError, TypeError) as exc:
            logger.warning("registry_load_failed", error=str(exc))
            entries = []
            runs_indexed = []

    # Create reference
    ref = FindingRef(
        id=entry.id,
        summary=entry.summary,
        severity=entry.severity,
        status=entry.status,
        run_id=run_id,
        target_prd=entry.target_prd,
    )

    # Upsert: replace if exists, append if new
    ref_dict = model_to_dict(ref)
    found = False
    for i, existing in enumerate(entries):
        if isinstance(existing, dict) and existing.get("id") == entry.id:
            entries[i] = ref_dict
            found = True
            break
    if not found:
        entries.append(ref_dict)

    # Track run
    if run_id and run_id not in runs_indexed:
        runs_indexed.append(run_id)

    registry_data: dict[str, object] = {
        "entries": entries,
        "total_count": len(entries),
        "runs_indexed": runs_indexed,
    }
    _writer.write_yaml(registry_path, registry_data)


def _update_registry_ref(
    finding_id: str,
    target_prd: str,
    status: str,
) -> None:
    """Update a finding reference in the global registry.

    Args:
        finding_id: Finding ID to update.
        target_prd: PRD ID to set.
        status: New finding status.
    """
    registry_path = _get_registry_path()
    if not registry_path.exists():
        return

    try:
        raw = _reader.read_yaml(registry_path)
        raw_entries = raw.get("entries", [])
        if isinstance(raw_entries, list):
            for ref_entry in raw_entries:
                if isinstance(ref_entry, dict) and ref_entry.get("id") == finding_id:
                    ref_entry["target_prd"] = target_prd
                    ref_entry["status"] = status
                    break
        _writer.write_yaml(registry_path, raw)
    except (StateError, ValueError, TypeError) as exc:
        logger.warning(
            "registry_ref_update_failed",
            finding_id=finding_id,
            error=str(exc),
        )


def _matches_filters(
    data: dict[str, object],
    severity: str | None,
    status: str | None,
    tags: list[str] | None,
    component: str | None,
) -> bool:
    """Check if a finding matches the given filters.

    Args:
        data: Finding data dictionary.
        severity: Severity filter (exact match).
        status: Status filter (exact match).
        tags: Tags filter (any-match).
        component: Component filter (substring match).

    Returns:
        True if the finding matches all provided filters.
    """
    if severity and str(data.get("severity", "")).lower() != severity.lower():
        return False
    if status and str(data.get("status", "")).lower() != status.lower():
        return False
    if tags:
        raw_tags = data.get("tags", [])
        entry_tags = list(raw_tags) if isinstance(raw_tags, list) else []
        if not any(t in entry_tags for t in tags):
            return False
    if component:
        entry_component = str(data.get("component", ""))
        if component.lower() not in entry_component.lower():
            return False
    return True


def get_unlinked_findings(
    severity_filter: tuple[str, ...] = ("critical", "high"),
) -> list[str]:
    """Query the global findings registry for unlinked high-severity findings.

    Returns finding IDs where ``severity`` is in ``severity_filter`` and
    ``target_prd`` is empty/None — i.e., findings that are PRD candidates
    but have not yet been converted to a PRD.

    Args:
        severity_filter: Tuple of severity levels to check for unlinked status.

    Returns:
        List of finding IDs that are unlinked.
    """
    registry_path = _get_registry_path()
    if not registry_path.exists():
        return []

    unlinked: list[str] = []
    try:
        reg_data = _reader.read_yaml(registry_path)
        reg_entries = reg_data.get("entries", [])
        if isinstance(reg_entries, list):
            for ref in reg_entries:
                if not isinstance(ref, dict):
                    continue
                sev = str(ref.get("severity", "")).lower()
                has_prd = bool(ref.get("target_prd"))
                if sev in severity_filter and not has_prd:
                    unlinked.append(str(ref.get("id", "")))
    except (StateError, ValueError, TypeError) as exc:
        logger.debug("get_unlinked_findings_failed", error=str(exc))

    return unlinked


def _create_prd_from_finding(
    input_text: str,
    category: str,
    priority: str,
) -> dict[str, object]:
    """Create a PRD from finding text using the requirements tool pipeline.

    Args:
        input_text: Finding summary + detail as PRD input.
        category: PRD category.
        priority: PRD priority.

    Returns:
        Result dict from trw_prd_create.
    """
    from trw_mcp.models.requirements import Priority

    try:
        prd_priority = Priority(priority)
    except ValueError:
        prd_priority = Priority.P1

    from trw_mcp.state.prd_utils import next_prd_sequence
    from trw_mcp.tools.requirements import _generate_prd_body, _render_prd
    from trw_mcp.models.requirements import (
        PRDConfidence,
        PRDDates,
        PRDEvidence,
        PRDFrontmatter,
        PRDQualityGates,
        PRDTraceability,
        EvidenceLevel,
    )

    prds_dir = resolve_project_root() / Path(_config.prds_relative_path)
    sequence = next_prd_sequence(prds_dir, category.upper())
    prd_id = f"PRD-{category.upper()}-{sequence:03d}"

    title = input_text.strip().split("\n")[0][:60].rstrip(".")

    body = _generate_prd_body(prd_id, title, input_text, category, priority, 0.7)

    frontmatter = PRDFrontmatter(
        id=prd_id,
        title=title,
        version="1.0",
        priority=prd_priority,
        category=category.upper(),
        confidence=PRDConfidence(
            implementation_feasibility=0.7,
            requirement_clarity=0.7,
            estimate_confidence=0.6,
            test_coverage_target=0.85,
        ),
        evidence=PRDEvidence(
            level=EvidenceLevel.MODERATE,
            sources=["Finding conversion"],
        ),
        traceability=PRDTraceability(),
        quality_gates=PRDQualityGates(
            ambiguity_rate_max=_config.ambiguity_rate_max,
            completeness_min=_config.completeness_min,
            traceability_coverage_min=_config.traceability_coverage_min,
        ),
        dates=PRDDates(created=date.today(), updated=date.today()),
    )

    from trw_mcp.state.persistence import model_to_dict as _m2d

    frontmatter_dict = _m2d(frontmatter)
    prd_content = _render_prd(frontmatter_dict, body)

    output_path = ""
    if prds_dir.exists() or (resolve_project_root() / _config.trw_dir).exists():
        _writer.ensure_dir(prds_dir)
        prd_file = prds_dir / f"{prd_id}.md"
        _writer.write_text(prd_file, prd_content)
        output_path = str(prd_file)

    return {
        "prd_id": prd_id,
        "title": title,
        "output_path": output_path,
    }
