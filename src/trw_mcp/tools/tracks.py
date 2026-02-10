"""TRW concurrent track management tools — create, list, status, merge-check.

Single ``trw_tracks`` MCP tool with action parameter for sprint track lifecycle.

PRD-CORE-003: Concurrent Sprint Track Support
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.track import (
    ConflictSeverity,
    FileConflict,
    MergeRecommendation,
    Track,
    TrackRegistry,
)
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()

# Valid actions for trw_tracks
_VALID_ACTIONS = frozenset({"create", "list", "status", "merge-check"})


def _registry_path(sprint_id: str) -> Path:
    """Resolve registry file path for a sprint.

    Args:
        sprint_id: Sprint identifier (e.g. ``"sprint-6"``).

    Returns:
        Absolute path to ``.trw/tracks/{sprint_id}.yaml``.
    """
    project_root = resolve_project_root()
    return project_root / _config.trw_dir / "tracks" / f"{sprint_id}.yaml"


def _load_registry(sprint_id: str) -> TrackRegistry:
    """Load track registry for a sprint, creating empty if not found.

    Args:
        sprint_id: Sprint identifier.

    Returns:
        TrackRegistry for the given sprint.
    """
    path = _registry_path(sprint_id)
    if not path.exists():
        return TrackRegistry(sprint_id=sprint_id)

    data = _reader.read_yaml(path)
    tracks_raw = data.get("tracks", [])
    if not isinstance(tracks_raw, list):
        return TrackRegistry(sprint_id=sprint_id)

    tracks: list[Track] = []
    for entry in tracks_raw:
        if isinstance(entry, dict):
            tracks.append(Track.model_validate(entry))

    return TrackRegistry(sprint_id=str(data.get("sprint_id", sprint_id)), tracks=tracks)


def _save_registry(registry: TrackRegistry) -> Path:
    """Atomically persist a track registry to disk.

    Args:
        registry: TrackRegistry to save.

    Returns:
        Path where the registry was written.
    """
    path = _registry_path(registry.sprint_id)
    _writer.ensure_dir(path.parent)
    _writer.write_yaml(path, model_to_dict(registry))
    return path


def _classify_conflict_severity(file_path: str) -> ConflictSeverity:
    """Classify conflict severity based on file path heuristics.

    - ``models/*.py`` → HIGH (schema changes)
    - ``server.py``, ``__init__.py`` → LOW (additive registrations)
    - Everything else → MEDIUM (function additions)

    Args:
        file_path: Relative file path.

    Returns:
        Conflict severity classification.
    """
    name = file_path.rsplit("/", 1)[-1]

    if "models/" in file_path or file_path.startswith("models/"):
        return ConflictSeverity.HIGH

    if name in ("server.py", "__init__.py"):
        return ConflictSeverity.LOW

    return ConflictSeverity.MEDIUM


def _detect_conflicts(registry: TrackRegistry) -> list[FileConflict]:
    """Detect file-level conflicts between tracks.

    Args:
        registry: TrackRegistry with tracks to check.

    Returns:
        List of FileConflict entries for files claimed by 2+ tracks.
    """
    file_to_tracks: dict[str, list[str]] = defaultdict(list)
    for track in registry.tracks:
        for f in track.files:
            file_to_tracks[f].append(track.name)

    conflicts: list[FileConflict] = []
    for file_path, track_names in sorted(file_to_tracks.items()):
        if len(track_names) >= 2:
            sorted_names = sorted(track_names)
            conflicts.append(
                FileConflict(
                    file_path=file_path,
                    tracks=sorted_names,
                    severity=_classify_conflict_severity(file_path),
                    reason=f"Modified by {len(sorted_names)} tracks: {', '.join(sorted_names)}",
                )
            )

    return conflicts


def _recommend_merge_order(
    registry: TrackRegistry,
    conflicts: list[FileConflict],
) -> list[MergeRecommendation]:
    """Recommend merge ordering based on conflict analysis.

    Ordering rules:
    1. Tracks with no conflicts merge first
    2. Among conflicting tracks, fewer conflict files → merge earlier
    3. Ties broken alphabetically by track name (deterministic)

    Args:
        registry: TrackRegistry with all tracks.
        conflicts: Pre-computed file conflicts.

    Returns:
        Ordered list of MergeRecommendation entries.
    """
    track_conflict_count: dict[str, int] = defaultdict(int)
    for conflict in conflicts:
        for track_name in conflict.tracks:
            track_conflict_count[track_name] += 1

    sorted_tracks = sorted(
        registry.tracks,
        key=lambda t: (track_conflict_count.get(t.name, 0), t.name),
    )

    recommendations: list[MergeRecommendation] = []
    for i, track in enumerate(sorted_tracks, start=1):
        count = track_conflict_count.get(track.name, 0)
        if count == 0:
            rationale = "No file conflicts — safe to merge first"
        else:
            rationale = f"{count} conflicting file(s) — merge after less-conflicted tracks"
        recommendations.append(
            MergeRecommendation(
                track_name=track.name,
                order=i,
                conflict_count=count,
                rationale=rationale,
            )
        )

    return recommendations


def _action_create(
    track: str | None,
    sprint: str | None,
    prd_scope: list[str] | None,
    files: list[str] | None,
    run_path: str | None,
) -> dict[str, object]:
    """Handle create action — register or update a track.

    Args:
        track: Track name (required).
        sprint: Sprint identifier (required).
        prd_scope: List of PRD IDs this track covers.
        files: List of file paths this track modifies.
        run_path: Optional run directory path for this track.

    Returns:
        Summary of the created/updated track.
    """
    if not track:
        raise ValidationError("track parameter is required for create action")
    if not sprint:
        raise ValidationError("sprint parameter is required for create action")

    registry = _load_registry(sprint)

    existing = next((t for t in registry.tracks if t.name == track), None)
    if existing is not None:
        if prd_scope is not None:
            existing.prd_scope = prd_scope
        if files is not None:
            existing.files = files
        if run_path is not None:
            existing.run_path = run_path
        action_taken = "updated"
    else:
        new_track = Track(
            name=track,
            sprint=sprint,
            prd_scope=prd_scope or [],
            files=files or [],
            run_path=run_path,
        )
        registry.tracks.append(new_track)
        action_taken = "created"

    saved_path = _save_registry(registry)

    logger.info(
        "track_registered",
        track_name=track,
        sprint=sprint,
        action_taken=action_taken,
        registry_path=str(saved_path),
    )

    return {
        "action": "create",
        "track": track,
        "sprint": sprint,
        "action_taken": action_taken,
        "prd_scope": prd_scope or [],
        "file_count": len(files) if files else 0,
        "registry_path": str(saved_path),
    }


def _action_list(sprint: str | None) -> dict[str, object]:
    """Handle list action — return all tracks.

    Args:
        sprint: Sprint ID to list. If None, lists across all sprints.

    Returns:
        Summary of all registered tracks.
    """
    if sprint:
        registry = _load_registry(sprint)
        all_tracks = registry.tracks
    else:
        project_root = resolve_project_root()
        tracks_dir = project_root / _config.trw_dir / "tracks"
        all_tracks = []
        if tracks_dir.exists():
            for yaml_file in sorted(tracks_dir.glob("*.yaml")):
                sprint_id = yaml_file.stem
                reg = _load_registry(sprint_id)
                all_tracks.extend(reg.tracks)

    track_summaries: list[dict[str, object]] = [
        {
            "name": t.name,
            "sprint": t.sprint,
            "status": t.status,
            "prd_scope": t.prd_scope,
            "file_count": len(t.files),
            "run_path": t.run_path,
        }
        for t in all_tracks
    ]

    return {
        "action": "list",
        "sprint_filter": sprint,
        "track_count": len(track_summaries),
        "tracks": track_summaries,
    }


def _action_status(
    track: str | None,
    sprint: str | None,
) -> dict[str, object]:
    """Handle status action — return detailed status for a specific track.

    Args:
        track: Track name (required).
        sprint: Sprint identifier (required).

    Returns:
        Full track details including files list.
    """
    if not track:
        raise ValidationError("track parameter is required for status action")
    if not sprint:
        raise ValidationError("sprint parameter is required for status action")

    registry = _load_registry(sprint)
    found = next((t for t in registry.tracks if t.name == track), None)
    if found is None:
        raise ValidationError(
            f"Track '{track}' not found in sprint '{sprint}'"
        )

    return {
        "action": "status",
        "track": model_to_dict(found),
    }


def _action_merge_check(sprint: str | None) -> dict[str, object]:
    """Handle merge-check action — detect conflicts and recommend ordering.

    Args:
        sprint: Sprint identifier (required).

    Returns:
        Conflict report with merge ordering recommendations.
    """
    if not sprint:
        raise ValidationError("sprint parameter is required for merge-check action")

    registry = _load_registry(sprint)
    if not registry.tracks:
        return {
            "action": "merge-check",
            "sprint": sprint,
            "conflicts": [],
            "merge_order": [],
            "summary": "No tracks registered for this sprint",
        }

    conflicts = _detect_conflicts(registry)
    merge_order = _recommend_merge_order(registry, conflicts)

    return {
        "action": "merge-check",
        "sprint": sprint,
        "conflict_count": len(conflicts),
        "conflicts": [model_to_dict(c) for c in conflicts],
        "merge_order": [model_to_dict(m) for m in merge_order],
        "summary": (
            f"{len(conflicts)} file conflict(s) across {len(registry.tracks)} tracks"
            if conflicts
            else f"No conflicts across {len(registry.tracks)} tracks — all clear to merge"
        ),
    }


def register_track_tools(server: FastMCP) -> None:
    """Register the trw_tracks tool on the MCP server.

    Args:
        server: FastMCP server instance.
    """

    @server.tool()
    async def trw_tracks(
        action: str,
        track: str | None = None,
        sprint: str | None = None,
        prd_scope: list[str] | None = None,
        files: list[str] | None = None,
        run_path: str | None = None,
    ) -> dict[str, object]:
        """Manage concurrent sprint tracks — create, list, status, merge-check.

        Args:
            action: Action to perform (create, list, status, merge-check).
            track: Track name (required for create, status).
            sprint: Sprint identifier (required for create, status, merge-check; optional for list).
            prd_scope: List of PRD IDs this track covers (create only).
            files: List of file paths this track modifies (create only).
            run_path: Optional run directory path for this track (create only).
        """
        if action not in _VALID_ACTIONS:
            raise ValidationError(
                f"Invalid action '{action}'. Must be one of: {', '.join(sorted(_VALID_ACTIONS))}"
            )

        if action == "create":
            return _action_create(track, sprint, prd_scope, files, run_path)
        if action == "list":
            return _action_list(sprint)
        if action == "status":
            return _action_status(track, sprint)
        # action == "merge-check" (only remaining valid action)
        return _action_merge_check(sprint)
