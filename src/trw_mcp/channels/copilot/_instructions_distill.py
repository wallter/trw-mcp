"""C1: .github/copilot-instructions.md segment renderer.

Marker-replaces the distill segment into .github/copilot-instructions.md between
    <!-- trw-distill:begin -->
and
    <!-- trw-distill:end -->

The distill marker pair is DISTINCT from and SEQUENTIAL AFTER the ceremony marker pair
(<!-- trw:copilot:start --> / <!-- trw:copilot:end -->).

Hard 250-token cap enforced (NFR03, FR03). Uses char/4 + 20% overhead estimate
(no tiktoken dep added per NFR07). stale_action: TIER_DOWN only — T0 beacon
is permanent and non-prunable (FR04, P1-14).

Total file size check before write: emits quota_proximity warning if resulting
file > 3,500 bytes (FR06, P2-13).

PRD-DIST-2406.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from trw_mcp.channels._conflict import RenderLog, detect_human_edit, write_atomic
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
from trw_mcp.channels._marker_replace import replace_distill_segment
from trw_mcp.channels._provenance import now_utc_iso8601, render_provenance_comment
from trw_mcp.channels._state import ChannelState, read_state, state_path_for, write_state
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels._ttl import check_staleness
from trw_mcp.channels.copilot._templates import render_c1_t0_beacon
from trw_mcp.channels.copilot._tier_down import count_tokens_estimate as _count_tokens_estimate
from trw_mcp.channels.copilot._tier_down import render_with_tier_down as _render_with_tier_down_impl
from trw_mcp.channels.instruction_segment import InstructionSegmentResult

log = structlog.get_logger(__name__)

__all__ = [
    "CopilotInstructionsDistillRenderer",
    "build_copilot_instructions_distill_entry",
]

# ---------------------------------------------------------------------------
# Marker constants
# ---------------------------------------------------------------------------

DISTILL_BEGIN = "<!-- trw-distill:begin -->"
DISTILL_END = "<!-- trw-distill:end -->"

# Token budget (hard cap, NFR03 / FR03)
BUDGET_TOKENS = 250

# Total file size threshold for quota_proximity warning (FR06 / P2-13)
QUOTA_PROXIMITY_BYTES = 3500

# Char-based approximate cap: 250 tokens * 4 chars/token * 1.2 overhead = 1200 chars
# We use a slightly conservative 1000 chars as segment content cap.
CHAR_CAP = 1000


# _count_tokens_estimate is re-exported from _tier_down for test access.

# ---------------------------------------------------------------------------
# ChannelEntry factory
# ---------------------------------------------------------------------------


def build_copilot_instructions_distill_entry(
    *,
    tier_default: str = "T1",
    ttl_commits: int = 10,
    ttl_days: int = 7,
    quota_total_bytes: int = CHAR_CAP,
) -> ChannelEntry:
    """Build the canonical ChannelEntry for copilot-instructions-distill.

    Args:
        tier_default: Default render tier (T1 per PRD).
        ttl_commits: Staleness threshold in commits.
        ttl_days: Staleness threshold in days.
        quota_total_bytes: Maximum segment size in UTF-8 bytes.

    Returns:
        Configured ChannelEntry ready for rendering.
    """
    return ChannelEntry(
        id="copilot-instructions-distill",
        client="copilot",
        surface=ChannelSurface.COPILOT_INSTRUCTIONS_SEGMENT,
        telemetry_tag="copilot_instructions_distill",
        file=".github/copilot-instructions.md",
        lock_file=".trw/channels/copilot-instructions-distill.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.MARKER_REPLACE,
        tier_default=tier_default,
        tier_min="T0",
        markers=MarkersConfig(start=DISTILL_BEGIN, end=DISTILL_END),
        ttl_commits=ttl_commits,
        ttl_days=ttl_days,
        quota_total_bytes=quota_total_bytes,
        human_edit_detection=HumanEditDetection.SHA256_SEGMENT,
        cleanup=CleanupConfig(
            trigger=CleanupTrigger.TTL_EXCEEDED,
            action=CleanupAction.TIER_DOWN,  # NOT TIER_DOWN_THEN_PRUNE (P1-14)
        ),
        regenerate_cmd="trw-distill self-improve risk-report --repo . --persist-sidecar",
        description=(
            "C1: copilot-instructions.md distill segment. "
            "Hard 250-token cap. T0 beacon is permanent. "
            "stale_action: TIER_DOWN only (P1-14)."
        ),
        sidecar_schema="risk-report-sidecar/v0",
    )


# ---------------------------------------------------------------------------
# Renderer class
# ---------------------------------------------------------------------------


class CopilotInstructionsDistillRenderer:
    """Marker-replace distill segment into .github/copilot-instructions.md.

    Implements FR02-FR07 and NFR01, NFR03, NFR05, NFR06, NFR10.

    Usage::

        renderer = CopilotInstructionsDistillRenderer()
        result = renderer.render(
            repo_root=Path("."),
            sidecar_data={"conventions": [...], "hotspots": [...]},
            sidecar_sha="abc123",
        )
    """

    START_MARKER = DISTILL_BEGIN
    END_MARKER = DISTILL_END
    BUDGET_TOKENS = BUDGET_TOKENS
    QUOTA_PROXIMITY_BYTES = QUOTA_PROXIMITY_BYTES

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
        """Render the C1 distill segment into copilot-instructions.md.

        Implements the 11-step render sequence with 250-token hard cap.

        Args:
            repo_root: Repository root directory.
            sidecar_data: Parsed sidecar payload or None (renders T0 beacon).
            sidecar_sha: Git SHA of the sidecar file.
            target_file: Override target path (defaults to .github/copilot-instructions.md).
            force: Skip TTL and conflict checks.
            dry_run: Return would-be content without writing to disk.

        Returns:
            InstructionSegmentResult describing the outcome.
        """
        entry = build_copilot_instructions_distill_entry()
        channel_id = entry.id

        # Step 1: Resolve target file
        resolved_target = target_file or (repo_root / ".github" / "copilot-instructions.md")

        # Step 2: Acquire lock (NFR05)
        lock_path = repo_root / ".trw" / "channels" / "copilot-instructions-distill.lock"
        try:
            lock = ChannelLock(lock_path)
            lock.__enter__()
        except ChannelLockSkip:
            log.debug(
                "copilot_instructions_distill_lock_skip",
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
            log.warning(
                "copilot_instructions_distill_error",
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
        """Execute steps 3-11 while the channel lock is held."""
        channel_id = entry.id
        channels_dir = repo_root / ".trw" / "channels"
        state_file = state_path_for(channel_id, channels_dir)

        # Step 3: Read state
        state = read_state(state_file)

        # Step 4: TTL check
        if not force:
            ttl_result = check_staleness(
                entry=entry,
                last_sidecar_sha=sidecar_sha,
                last_render_ts=state.last_render_ts if state else None,
                repo_root=repo_root,
            )
            if not ttl_result.ttl_unknown and ttl_result.is_stale:
                # stale_action: TIER_DOWN (not FULL_PRUNE) — write T0 beacon
                t0_content = self._build_t0_beacon()
                return self._write_segment(
                    entry=entry,
                    repo_root=repo_root,
                    sidecar_sha=sidecar_sha,
                    resolved_target=resolved_target,
                    channels_dir=channels_dir,
                    state_file=state_file,
                    segment_content=t0_content,
                    tier_used="T0",
                    dry_run=dry_run,
                    extra_event_fields={"tier_down_reason": "ttl_exceeded"},
                )

        # Step 5: Conflict detection
        if not force:
            expected_sha = state.segment_interior_sha256 if state else None
            conflict = detect_human_edit(
                mode=HumanEditDetection.SHA256_SEGMENT,
                target_path=resolved_target,
                expected_sha=expected_sha,
                markers=entry.markers,
            )
            if conflict:
                log.debug(
                    "copilot_instructions_distill_conflict",
                    channel_id=channel_id,
                    outcome="skipped_conflict",
                )
                _emit_event(channel_id, "channel_conflict", None, "skipped_conflict")
                return InstructionSegmentResult(
                    channel_id=channel_id,
                    status="skipped_conflict",
                    conflict_detected=True,
                )

        # Steps 6-8: Choose tier, render, enforce token cap
        ts = now_utc_iso8601()
        if sidecar_data is None:
            segment_content = self._build_t0_beacon(ts=ts)
            tier_used = "T0"
        else:
            segment_content, tier_used = self._render_with_tier_down(sidecar_data, ts=ts)

        # Step 6b: Check total file size (FR06 / P2-13)
        existing_content = resolved_target.read_text(encoding="utf-8") if resolved_target.exists() else ""
        total_size = len((existing_content + segment_content).encode("utf-8"))
        if total_size > self.QUOTA_PROXIMITY_BYTES:
            log.warning(
                "copilot_instructions_distill_quota_proximity",
                channel_id=channel_id,
                total_bytes=total_size,
                threshold=self.QUOTA_PROXIMITY_BYTES,
                outcome="quota_proximity_warning",
            )
            _emit_event(channel_id, "push_ephemeral", tier_used, "quota_proximity", extra={"total_bytes": total_size})

        return self._write_segment(
            entry=entry,
            repo_root=repo_root,
            sidecar_sha=sidecar_sha,
            resolved_target=resolved_target,
            channels_dir=channels_dir,
            state_file=state_file,
            segment_content=segment_content,
            tier_used=tier_used,
            dry_run=dry_run,
        )

    def _render_with_tier_down(
        self,
        sidecar_data: dict[str, object],
        *,
        ts: str,
    ) -> tuple[str, str]:
        """Render T1 content with four-step tier-down ladder if over 250 tokens.

        Delegates to _tier_down.render_with_tier_down for the actual logic.

        Returns:
            (rendered_content, tier_used) tuple.
        """
        return _render_with_tier_down_impl(
            sidecar_data,
            ts=ts,
            budget_tokens=self.BUDGET_TOKENS,
        )

    def _build_t0_beacon(self, *, ts: str | None = None) -> str:
        """Build the T0 presence beacon content."""
        if ts is None:
            ts = now_utc_iso8601()
        return render_c1_t0_beacon(ts=ts)

    def _write_segment(
        self,
        *,
        entry: ChannelEntry,
        repo_root: Path,
        sidecar_sha: str | None,
        resolved_target: Path,
        channels_dir: Path,
        state_file: Path,
        segment_content: str,
        tier_used: str,
        dry_run: bool,
        extra_event_fields: dict[str, object] | None = None,
    ) -> InstructionSegmentResult:
        """Build wrapped segment and write to disk (or return dry-run result)."""
        channel_id = entry.id
        ts = now_utc_iso8601()

        # Step 9: Prepend provenance comment, wrap in markers
        provenance = render_provenance_comment(
            channel_id=channel_id,
            sha=sidecar_sha or "none",
            ts=ts,
            tier=tier_used,
            regenerate=entry.regenerate_cmd or "",
        )

        # Build wrapped segment (markers + provenance + content)
        wrapped_segment = f"{provenance}\n{segment_content}"

        # Read existing file to compute the full replacement content
        if resolved_target.exists():
            existing = resolved_target.read_text(encoding="utf-8")
        else:
            resolved_target.parent.mkdir(parents=True, exist_ok=True)
            existing = ""

        markers = MarkersConfig(start=self.START_MARKER, end=self.END_MARKER)

        # Inject segment using marker_replace
        if self.START_MARKER in existing:
            full_content = replace_distill_segment(existing, wrapped_segment, markers=markers)
        else:
            # Append after ceremony end marker if present, else at EOF
            ceremony_end = "<!-- trw:copilot:end -->"
            if ceremony_end in existing:
                idx = existing.find(ceremony_end)
                insert_after = idx + len(ceremony_end)
                before = existing[:insert_after]
                after = existing[insert_after:]
                new_section = f"\n\n{self.START_MARKER}\n{wrapped_segment}\n{self.END_MARKER}\n"
                full_content = before + new_section + after.lstrip("\n")
            else:
                new_section = f"{self.START_MARKER}\n{wrapped_segment}\n{self.END_MARKER}\n"
                full_content = existing + ("\n\n" + new_section if existing.strip() else new_section)

        bytes_written = len(full_content.encode("utf-8"))
        tokens_estimated = _count_tokens_estimate(wrapped_segment)

        if dry_run:
            _emit_event(channel_id, "push_ephemeral", tier_used, "dry_run")
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="dry_run",
                tier_used=tier_used,
                bytes_written=bytes_written,
                tokens_estimated=tokens_estimated,
                would_write=full_content,
            )

        # Step 10: Write atomically
        render_log = RenderLog(channels_dir / "render-log.jsonl")
        write_atomic(
            resolved_target,
            full_content,
            channel_id=channel_id,
            render_log=render_log,
            sidecar_sha=sidecar_sha,
        )

        # Persist state
        seg_sha = hashlib.sha256(wrapped_segment.encode("utf-8")).hexdigest()
        new_state = ChannelState(
            channel_id=channel_id,
            last_render_tier=tier_used,
            last_render_bytes=bytes_written,
            last_render_tokens_est=tokens_estimated,
            last_sidecar_sha=sidecar_sha,
            segment_interior_sha256=seg_sha,
            last_render_ts=ts,
        )
        write_state(new_state, state_file)

        # Step 11: Emit telemetry (fail-open, NFR06)
        extra = extra_event_fields or {}
        _emit_event(channel_id, "push_write", tier_used, "written", extra=extra)

        log.debug(
            "copilot_instructions_distill_written",
            channel_id=channel_id,
            tier_used=tier_used,
            bytes_written=bytes_written,
            outcome="written",
        )

        return InstructionSegmentResult(
            channel_id=channel_id,
            status="written",
            tier_used=tier_used,
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
