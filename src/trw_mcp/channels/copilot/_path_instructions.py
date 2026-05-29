"""C2: .github/instructions/trw-distill-hotspots.instructions.md renderer.

Full-rewrite of the path-scoped instructions file with valid YAML frontmatter.
The applyTo field uses directory-level minimatch glob patterns (e.g.
'backend/routers/**/*.py'), NEVER literal file paths (FR08, P0-12).

VS Code + cloud agent only — path-scoped instructions are NOT active in bare
CLI sessions. The absence of this file is safe (no instruction is better than
a stale one).

stale_action: FULL_PRUNE (file deleted, no T0 fallback for C2).
File is git_tracked: false, added to .gitignore at render time.

PRD-DIST-2406.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from trw_mcp.channels._conflict import RenderLog, write_atomic
from trw_mcp.channels._gitignore import add_gitignore_entry
from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._manifest_models import (
    ChannelEntry,
    ChannelStatus,
    ChannelSurface,
    CleanupAction,
    CleanupConfig,
    CleanupTrigger,
    HumanEditDetection,
    MarkersConfig,
    WriteStrategy,
)
from trw_mcp.channels._provenance import now_utc_iso8601
from trw_mcp.channels._state import ChannelState, read_state, state_path_for, write_state
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels._ttl import check_staleness
from trw_mcp.channels.copilot._templates import render_c2_path_instructions
from trw_mcp.channels.instruction_segment import InstructionSegmentResult

log = structlog.get_logger(__name__)

__all__ = [
    "CopilotPathInstructionsRenderer",
    "build_copilot_path_instructions_entry",
    "compute_apply_to_glob",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_PATH = ".github/instructions/trw-distill-hotspots.instructions.md"
MAX_HOTSPOT_FILES = 5
BUDGET_TOKENS = 500


# ---------------------------------------------------------------------------
# ChannelEntry factory
# ---------------------------------------------------------------------------


def build_copilot_path_instructions_entry(
    *,
    ttl_commits: int = 20,
    ttl_days: int = 14,
    quota_total_bytes: int = 2048,
) -> ChannelEntry:
    """Build the canonical ChannelEntry for copilot-path-instructions-distill.

    Args:
        ttl_commits: Staleness threshold in commits.
        ttl_days: Staleness threshold in days.
        quota_total_bytes: Maximum file size in UTF-8 bytes.

    Returns:
        Configured ChannelEntry.
    """
    return ChannelEntry(
        id="copilot-path-instructions-distill",
        client="copilot",
        surface=ChannelSurface.INSTRUCTION_FILE_SEGMENT,
        telemetry_tag="copilot_path_instructions_distill",
        file=OUTPUT_PATH,
        lock_file=".trw/channels/copilot-path-instructions-distill.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.FULL_REWRITE,
        tier_default="T1",
        tier_min="T0",
        markers=MarkersConfig(start="", end=""),
        ttl_commits=ttl_commits,
        ttl_days=ttl_days,
        quota_total_bytes=quota_total_bytes,
        human_edit_detection=HumanEditDetection.NONE,
        cleanup=CleanupConfig(
            trigger=CleanupTrigger.TTL_EXCEEDED,
            action=CleanupAction.FULL_PRUNE,  # C2 has no T0 fallback
        ),
        regenerate_cmd="trw-distill self-improve risk-report --repo . --persist-sidecar",
        description=(
            "C2: path-scoped instructions for Copilot. "
            "VS Code + cloud agent only. applyTo uses directory minimatch globs (P0-12). "
            "Full-prune on stale (no T0 fallback)."
        ),
        sidecar_schema="risk-report-sidecar/v0",
        activation_gate=None,
    )


# ---------------------------------------------------------------------------
# Public helper: compute_apply_to_glob
# ---------------------------------------------------------------------------


def compute_apply_to_glob(hotspot_files: list[str]) -> str:
    """Derive directory-level minimatch globs from hotspot file paths.

    For each path 'dir/subdir/file.py' produces 'dir/subdir/**/*.py'.
    NEVER emits literal file paths — only directory patterns (FR08, P0-12).

    Example:
        ['backend/routers/admin.py', 'trw-mcp/src/trw_mcp/state/ceremony.py']
        -> 'backend/routers/**/*.py,trw-mcp/src/trw_mcp/state/**/*.py'

    Args:
        hotspot_files: List of file paths relative to repo root.

    Returns:
        Comma-joined minimatch directory glob string.
    """
    seen: set[str] = set()
    globs: list[str] = []

    for file_path in hotspot_files:
        p = Path(file_path)
        parent = str(p.parent)
        suffix = p.suffix or ".py"
        ext_glob = f"*{suffix}"

        if parent in (".", ""):
            # Top-level file — use bare glob
            glob_str = f"**/{ext_glob}"
        else:
            glob_str = f"{parent}/**/{ext_glob}"

        if glob_str not in seen:
            seen.add(glob_str)
            globs.append(glob_str)

    return ",".join(globs) if globs else "**/*.py"


# ---------------------------------------------------------------------------
# Renderer class
# ---------------------------------------------------------------------------


class CopilotPathInstructionsRenderer:
    """Full-rewrite .github/instructions/trw-distill-hotspots.instructions.md.

    Implements FR08-FR10 and NFR05, NFR06.

    VS Code + cloud agent only. Not active in bare CLI sessions.
    """

    OUTPUT_PATH = OUTPUT_PATH
    MAX_HOTSPOT_FILES = MAX_HOTSPOT_FILES

    @staticmethod
    def compute_apply_to_glob(hotspot_files: list[str]) -> str:
        """Derive directory-level minimatch globs (FR08, P0-12).

        See module-level compute_apply_to_glob for docs.
        """
        return compute_apply_to_glob(hotspot_files)

    def render(
        self,
        repo_root: Path,
        sidecar_data: dict[str, object] | None,
        sidecar_sha: str | None,
        *,
        target_file: Path | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> InstructionSegmentResult:
        """Render the C2 path-scoped instructions file.

        Args:
            repo_root: Repository root directory.
            sidecar_data: Parsed sidecar payload or None (skips render).
            sidecar_sha: Git SHA of the sidecar file.
            target_file: Override target path.
            force: Skip TTL and conflict checks.
            dry_run: Return would-be content without writing.

        Returns:
            InstructionSegmentResult describing the outcome.
        """
        entry = build_copilot_path_instructions_entry()
        channel_id = entry.id

        resolved_target = target_file or (repo_root / self.OUTPUT_PATH)

        # Acquire lock (NFR05)
        lock_path = repo_root / ".trw" / "channels" / "copilot-path-instructions-distill.lock"
        try:
            lock = ChannelLock(lock_path)
            lock.__enter__()
        except ChannelLockSkip:
            log.debug(
                "copilot_path_instructions_lock_skip",
                channel_id=channel_id,
                outcome="skipped_lock",
            )
            _emit_event(channel_id, "channel_lock_skip", None, "skipped_lock")
            return InstructionSegmentResult(channel_id=channel_id, status="skipped_lock")

        try:
            return self._render_under_lock(
                entry=entry,
                repo_root=repo_root,
                sidecar_data=sidecar_data,
                sidecar_sha=sidecar_sha,
                resolved_target=resolved_target,
                force=force,
                dry_run=dry_run,
            )
        except Exception as exc:
            log.debug(
                "copilot_path_instructions_error",
                channel_id=channel_id,
                error=str(exc),
                outcome="error",
            )
            _emit_event(channel_id, "channel_error", None, "error")
            return InstructionSegmentResult(channel_id=channel_id, status="error", error=str(exc))
        finally:
            try:
                lock.__exit__(None, None, None)
            except Exception:
                pass

    def _render_under_lock(
        self,
        *,
        entry: ChannelEntry,
        repo_root: Path,
        sidecar_data: dict[str, object] | None,
        sidecar_sha: str | None,
        resolved_target: Path,
        force: bool,
        dry_run: bool,
    ) -> InstructionSegmentResult:
        """Execute render steps while the channel lock is held."""
        channel_id = entry.id
        channels_dir = repo_root / ".trw" / "channels"
        state_file = state_path_for(channel_id, channels_dir)
        state = read_state(state_file)

        # TTL check — stale: FULL_PRUNE (delete file, no T0 fallback)
        if not force:
            ttl_result = check_staleness(
                entry=entry,
                last_sidecar_sha=sidecar_sha,
                last_render_ts=state.last_render_ts if state else None,
                repo_root=repo_root,
            )
            if not ttl_result.ttl_unknown and ttl_result.is_stale:
                if resolved_target.exists():
                    resolved_target.unlink()
                _emit_event(channel_id, "push_stale", None, "full_prune")
                return InstructionSegmentResult(
                    channel_id=channel_id,
                    status="skipped_ttl",
                    ttl_commits_remaining=0,
                )

        if sidecar_data is None:
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="skipped_quota_exempt",
                error="no sidecar data",
            )

        # Build content
        _spots = sidecar_data.get("hotspots")
        hotspots_raw: list[object] = list(_spots) if isinstance(_spots, list) else []
        top_hotspots = hotspots_raw[: self.MAX_HOTSPOT_FILES]

        # Extract hotspot file paths for glob derivation
        hotspot_files: list[str] = []
        for h in top_hotspots:
            if isinstance(h, dict):
                path = str(h.get("file", h.get("path", "")))
            else:
                path = str(h)
            if path:
                hotspot_files.append(path)

        apply_to = compute_apply_to_glob(hotspot_files)
        ts = now_utc_iso8601()

        # Check if hotspot set changed from previous render (delete-then-rewrite)
        old_state_sha = state.last_sidecar_sha if state else None
        if old_state_sha and old_state_sha != sidecar_sha and resolved_target.exists():
            resolved_target.unlink()

        file_content = render_c2_path_instructions(
            apply_to=apply_to,
            hotspot_entries=[(h if isinstance(h, dict) else {"file": str(h)}) for h in top_hotspots],
            ts=ts,
        )

        bytes_written = len(file_content.encode("utf-8"))
        tokens_estimated = len(file_content.split())

        if dry_run:
            _emit_event(channel_id, "push_ephemeral", "T1", "dry_run")
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="dry_run",
                tier_used="T1",
                bytes_written=bytes_written,
                tokens_estimated=tokens_estimated,
                would_write=file_content,
            )

        # Write file
        resolved_target.parent.mkdir(parents=True, exist_ok=True)
        render_log = RenderLog(channels_dir / "render-log.jsonl")
        write_atomic(
            resolved_target,
            file_content,
            channel_id=channel_id,
            render_log=render_log,
            sidecar_sha=sidecar_sha,
        )

        # Add to .gitignore (file is not git-tracked)
        add_gitignore_entry(repo_root, self.OUTPUT_PATH)

        # Persist state
        seg_sha = hashlib.sha256(file_content.encode("utf-8")).hexdigest()
        new_state = ChannelState(
            channel_id=channel_id,
            last_render_tier="T1",
            last_render_bytes=bytes_written,
            last_render_tokens_est=tokens_estimated,
            last_sidecar_sha=sidecar_sha,
            segment_interior_sha256=seg_sha,
            last_render_ts=ts,
        )
        write_state(new_state, state_file)

        _emit_event(channel_id, "push_write", "T1", "written")

        return InstructionSegmentResult(
            channel_id=channel_id,
            status="written",
            tier_used="T1",
            bytes_written=bytes_written,
            tokens_estimated=tokens_estimated,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit_event(
    channel_id: str,
    event_type: str,
    tier: str | None,
    outcome: str,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    """Fail-open telemetry wrapper (NFR06)."""
    try:
        append_channel_event(
            channel_id=channel_id,
            client="copilot",
            event_type=event_type,
            tier=tier,
            extra={"outcome": outcome, **(extra or {})},
        )
    except Exception:
        pass
