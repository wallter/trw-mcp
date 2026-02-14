"""TRW sprint orchestration tools — trw_sprint_start, trw_sprint_finish.

Automates sprint kickoff (parse doc, init run, register track, generate prompt)
and sprint finish (verify tracks, build check, DoD status, simplifier waves).

PRD scope: Sprint 10.5 MVP
"""

from __future__ import annotations

import re
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import Phase, RunState, RunStatus
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.persistence import (
    FileEventLogger,
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.models.sprint import SprintDoc, SprintTrack
from trw_mcp.state.sprint_parser import (
    extract_prd_refs,
    get_track_by_name,
    parse_sprint_doc,
)
from trw_mcp.tools.tracks import _action_create

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()
_events = FileEventLogger(_writer)

# --- Kickoff prompt template ---

_KICKOFF_TEMPLATE = """\
You are executing a single track of a parallel sprint.

## Sprint Context
- **Sprint**: {sprint_number} — {sprint_title}
- **Track**: {track_letter} — {track_title}
- **Goal**: {sprint_goal}

## PRD Scope
{prd_scope_list}

## Files (ONLY modify these)
{files_list}

## Overlap Warnings
{overlap_warnings}

## Instructions
1. Run directory already initialized at `{run_path}`.
2. Follow TRW phases: RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER.
3. **File boundary enforcement**: Only modify files listed above. If you need to change a file \
not on the list, STOP and coordinate with the user.
4. Write tests for all non-trivial changes. Coverage target: >=85%.
5. Commit format: `feat(sprint{sprint_number}): Track {track_letter} — <description>`
6. Do NOT run code-simplifier — that happens post-merge.
7. Use `trw_event` and `trw_checkpoint` during implementation to persist state.

## Validation Criteria
{validation_criteria}

## Definition of Done
{dod_items}
"""


def _create_lightweight_run(
    task_name: str,
    sprint_number: int,
    track_letter: str,
    prd_scope: list[str],
) -> Path:
    """Create a minimal run directory for a sprint track.

    Lighter than trw_init — creates just the run scaffolding without
    deploying frameworks/templates (those already exist in .trw/).

    Args:
        task_name: Task name for directory path.
        sprint_number: Sprint number.
        track_letter: Track letter (A, B, C...).
        prd_scope: List of PRD IDs for this track.

    Returns:
        Path to the created run directory.
    """
    project_root = resolve_project_root()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    random_hex = secrets.token_hex(4)
    run_id = f"{timestamp}-{random_hex}"

    task_dir = project_root / _config.task_root / task_name
    run_root = task_dir / "runs" / run_id

    for subdir in [
        "meta", "reports", "artifacts", "scratch",
        "scratch/_orchestrator", "scratch/_blackboard",
        "shards", "validation",
    ]:
        _writer.ensure_dir(run_root / subdir)

    run_state = RunState(
        run_id=run_id,
        task=task_name,
        framework=_config.framework_version,
        status=RunStatus.ACTIVE,
        phase=Phase.RESEARCH,
        objective=f"Sprint {sprint_number} Track {track_letter}",
        prd_scope=prd_scope,
    )
    _writer.write_yaml(run_root / "meta" / "run.yaml", model_to_dict(run_state))

    _events.log_event(
        run_root / "meta" / "events.jsonl",
        "run_init",
        {
            "task": task_name,
            "run_id": run_id,
            "sprint": str(sprint_number),
            "track": track_letter,
        },
    )

    return run_root


def _format_bullet_list(items: list[str], prefix: str = "- ") -> str:
    """Format a list as markdown bullet points.

    Args:
        items: List of strings to format.
        prefix: Bullet prefix.

    Returns:
        Formatted bullet list, or "None" if empty.
    """
    if not items:
        return "None"
    return "\n".join(f"{prefix}{item}" for item in items)


def _get_overlap_warnings(
    sprint_doc: SprintDoc,
    track_letter: str,
) -> list[str]:
    """Extract overlap warnings for a specific track from the file overlap matrix.

    Args:
        sprint_doc: Parsed sprint document.
        track_letter: Track letter to check.

    Returns:
        List of warning strings for files with overlap.
    """
    warnings: list[str] = []
    for entry in sprint_doc.file_overlap_matrix:
        if not entry.has_conflict:
            continue
        owner = entry.track_owners.get(track_letter, "--")
        if owner.strip() not in ("--", "", "NONE"):
            other_tracks = [
                f"Track {k}" for k, v in entry.track_owners.items()
                if k != track_letter and v.strip() not in ("--", "", "NONE")
            ]
            if other_tracks:
                warnings.append(
                    f"`{entry.file_path}` — shared with {', '.join(other_tracks)}"
                )
    return warnings


def _generate_kickoff_prompt(
    sprint_doc: SprintDoc,
    track: SprintTrack,
    run_path: Path,
) -> str:
    """Generate a ready-to-paste kickoff prompt for a sprint track.

    Args:
        sprint_doc: Parsed sprint document.
        track: The specific track to generate for.
        run_path: Path to the initialized run directory.

    Returns:
        Formatted kickoff prompt string.
    """
    overlap_warnings = _get_overlap_warnings(sprint_doc, track.name)

    return _KICKOFF_TEMPLATE.format(
        sprint_number=sprint_doc.sprint_number,
        sprint_title=sprint_doc.title,
        track_letter=track.name,
        track_title=track.title,
        sprint_goal=sprint_doc.goal or "(no goal specified)",
        prd_scope_list=_format_bullet_list(track.prd_scope),
        files_list=_format_bullet_list(track.files),
        overlap_warnings=_format_bullet_list(overlap_warnings) if overlap_warnings else "No file overlaps with other tracks.",
        run_path=str(run_path),
        validation_criteria=_format_bullet_list(track.validation_criteria),
        dod_items=_format_bullet_list(track.dod_items),
    )


def _check_track_commits(
    sprint_number: int,
    tracks: list[SprintTrack],
    commit_pattern: str,
) -> tuple[list[str], list[str]]:
    """Check which tracks have been committed via git log.

    Args:
        sprint_number: Sprint number to search for.
        tracks: List of track definitions.
        commit_pattern: Pattern template with {num} and {track} placeholders.

    Returns:
        Tuple of (committed_tracks, missing_tracks) letter lists.
    """
    committed: list[str] = []
    missing: list[str] = []

    for track in tracks:
        grep_pattern = commit_pattern.format(num=sprint_number, track=track.name)
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"--grep={grep_pattern}"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(resolve_project_root()),
            )
            if result.returncode == 0 and result.stdout.strip():
                committed.append(track.name)
            else:
                missing.append(track.name)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            missing.append(track.name)

    return committed, missing


def _get_changed_py_files(base_branch: str = "main") -> list[str]:
    """Get list of changed .py files compared to base branch.

    Args:
        base_branch: Base branch to diff against.

    Returns:
        List of changed .py file paths.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_branch, "--", "*.py"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(resolve_project_root()),
        )
        if result.returncode == 0:
            return [
                f.strip() for f in result.stdout.strip().splitlines()
                if f.strip().endswith(".py")
            ]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return []


def _chunk_list(items: list[str], size: int) -> list[list[str]]:
    """Split a list into chunks of given size.

    Args:
        items: List to chunk.
        size: Maximum chunk size.

    Returns:
        List of chunks.
    """
    return [items[i:i + size] for i in range(0, len(items), size)]


def register_sprint_tools(server: FastMCP) -> None:
    """Register sprint orchestration tools on the MCP server.

    Args:
        server: FastMCP server instance.
    """

    @server.tool()
    async def trw_sprint_start(
        sprint_doc_path: str,
        track: str,
    ) -> dict[str, object]:
        """Start a sprint track — parse doc, init run, register track, generate prompt.

        Parses the sprint document, initializes a run directory, registers the track,
        and returns a ready-to-paste kickoff prompt with all context extracted.

        Args:
            sprint_doc_path: Path to the sprint markdown document.
            track: Track letter to start (e.g., "A", "B", "C").
        """
        # Resolve and read sprint doc
        doc_path = Path(sprint_doc_path)
        if not doc_path.is_absolute():
            doc_path = resolve_project_root() / doc_path

        if not doc_path.exists():
            raise ValidationError(
                f"Sprint document not found: {doc_path}",
                path=str(doc_path),
            )

        content = doc_path.read_text(encoding="utf-8")
        sprint_doc = parse_sprint_doc(content, source_path=str(doc_path))

        # Look up track
        track_info = get_track_by_name(sprint_doc, track)

        # Create run directory
        task_name = f"sprint-{sprint_doc.sprint_number}-track-{track_info.name.lower()}"
        run_path = _create_lightweight_run(
            task_name=task_name,
            sprint_number=sprint_doc.sprint_number,
            track_letter=track_info.name,
            prd_scope=track_info.prd_scope,
        )

        # Register track via tracks tool
        sprint_id = f"sprint-{sprint_doc.sprint_number}"
        _action_create(
            track=track_info.name,
            sprint=sprint_id,
            prd_scope=track_info.prd_scope,
            files=track_info.files,
            run_path=str(run_path),
        )

        # Generate kickoff prompt
        kickoff_prompt = _generate_kickoff_prompt(sprint_doc, track_info, run_path)

        logger.info(
            "sprint_track_started",
            sprint=sprint_doc.sprint_number,
            track=track_info.name,
            run_path=str(run_path),
            files=len(track_info.files),
            prd_scope=len(track_info.prd_scope),
        )

        return {
            "sprint_number": sprint_doc.sprint_number,
            "track": track_info.name,
            "track_title": track_info.title,
            "run_path": str(run_path),
            "sprint_id": sprint_id,
            "prd_scope": track_info.prd_scope,
            "files": track_info.files,
            "file_count": len(track_info.files),
            "validation_criteria_count": len(track_info.validation_criteria),
            "dod_items_count": len(track_info.dod_items),
            "kickoff_prompt": kickoff_prompt,
        }

    @server.tool()
    async def trw_sprint_finish(
        sprint_doc_path: str,
    ) -> dict[str, object]:
        """Finish a sprint — verify tracks, build check, DoD status, simplifier waves.

        Checks that all tracks have been committed, runs build verification,
        parses DoD status, and generates code-simplifier wave instructions.

        Args:
            sprint_doc_path: Path to the sprint markdown document.
        """
        doc_path = Path(sprint_doc_path)
        if not doc_path.is_absolute():
            doc_path = resolve_project_root() / doc_path

        if not doc_path.exists():
            raise ValidationError(
                f"Sprint document not found: {doc_path}",
                path=str(doc_path),
            )

        content = doc_path.read_text(encoding="utf-8")
        sprint_doc = parse_sprint_doc(content, source_path=str(doc_path))
        errors: list[str] = []

        # Step 1: Check track commits
        committed, missing = _check_track_commits(
            sprint_doc.sprint_number,
            sprint_doc.tracks,
            _config.sprint_commit_pattern,
        )

        all_committed = len(missing) == 0
        if not all_committed:
            errors.append(
                f"Missing track commits: {', '.join(missing)}. "
                f"Expected pattern: {_config.sprint_commit_pattern.format(num=sprint_doc.sprint_number, track='X')}"
            )

        # Step 2: Build check (only if all tracks committed)
        build_result: dict[str, object] = {"status": "skipped"}
        if all_committed:
            try:
                from trw_mcp.tools.build import run_build_check, cache_build_status

                project_root = resolve_project_root()
                trw_dir = resolve_trw_dir()
                status = run_build_check(
                    project_root,
                    scope="full",
                    timeout_secs=_config.build_check_timeout_secs,
                    pytest_args=_config.build_check_pytest_args,
                    mypy_args=_config.build_check_mypy_args,
                )
                cache_build_status(trw_dir, status)
                build_result = {
                    "status": "complete",
                    "tests_passed": status.tests_passed,
                    "mypy_clean": status.mypy_clean,
                    "coverage_pct": status.coverage_pct,
                    "test_count": status.test_count,
                    "failure_count": status.failure_count,
                }
                if not status.tests_passed or not status.mypy_clean:
                    errors.append(
                        f"Build check failed: tests_passed={status.tests_passed}, "
                        f"mypy_clean={status.mypy_clean}"
                    )
            except Exception as exc:
                build_result = {"status": "error", "error": str(exc)}
                errors.append(f"Build check error: {exc}")
        else:
            build_result = {
                "status": "blocked",
                "reason": f"Tracks not committed: {', '.join(missing)}",
            }

        # Step 3: DoD status
        dod_checked = [item for item in sprint_doc.dod_items if item.startswith("[x]")]
        dod_unchecked = [item for item in sprint_doc.dod_items if item.startswith("[ ]")]

        # Step 4: Code simplifier waves
        changed_files = _get_changed_py_files()
        wave_size = _config.sprint_code_simplifier_wave_size
        simplifier_waves = _chunk_list(changed_files, wave_size)

        # Build next steps
        next_steps: list[str] = []
        if missing:
            next_steps.append(
                f"Commit missing tracks ({', '.join(missing)}) before finishing"
            )
        if dod_unchecked:
            next_steps.append(
                f"Complete {len(dod_unchecked)} unchecked DoD item(s)"
            )
        if errors:
            next_steps.append("Fix build/test failures listed above")
        if simplifier_waves and not errors:
            next_steps.append(
                f"Run code-simplifier on {len(simplifier_waves)} wave(s) "
                f"({len(changed_files)} files total)"
            )
        if not errors and not dod_unchecked:
            next_steps.append("Sprint is ready for delivery")

        logger.info(
            "sprint_finish_check",
            sprint=sprint_doc.sprint_number,
            tracks_committed=len(committed),
            tracks_missing=len(missing),
            dod_done=len(dod_checked),
            dod_remaining=len(dod_unchecked),
            errors=len(errors),
        )

        return {
            "sprint_number": sprint_doc.sprint_number,
            "all_tracks_committed": all_committed,
            "committed_tracks": committed,
            "missing_tracks": missing,
            "build": build_result,
            "dod_status": {
                "total": len(sprint_doc.dod_items),
                "checked": len(dod_checked),
                "unchecked": len(dod_unchecked),
                "items": sprint_doc.dod_items,
            },
            "simplifier_waves": {
                "wave_count": len(simplifier_waves),
                "total_files": len(changed_files),
                "wave_size": wave_size,
                "waves": [
                    {"wave": i + 1, "files": wave}
                    for i, wave in enumerate(simplifier_waves)
                ],
            },
            "errors": errors,
            "next_steps": next_steps,
            "ready": len(errors) == 0 and len(dod_unchecked) == 0,
        }
