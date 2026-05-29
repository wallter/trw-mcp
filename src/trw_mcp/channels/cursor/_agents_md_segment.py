"""CUR-05: cursor-cli AGENTS.md marker-replace segment writer.

Writes T1 distill summary between TRW:DISTILL:BEGIN and TRW:DISTILL:END
markers in AGENTS.md. Content outside the marker pair is byte-identical
before and after every render (idempotency guarantee).

Delegates to instruction_segment/_renderer.py from PRD-DIST-2400.

PRD-DIST-2401 Phase D.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from trw_mcp.channels._conflict import RenderLog, write_atomic
from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._manifest_models import ChannelEntry
from trw_mcp.channels._marker_replace import replace_distill_segment
from trw_mcp.channels._quota import check_quota, tier_down
from trw_mcp.channels._state import ChannelState, state_path_for, write_state
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels.instruction_segment import (
    InstructionSegmentResult,
)

log = structlog.get_logger(__name__)

__all__ = [
    "TRW_DISTILL_BEGIN",
    "TRW_DISTILL_END",
    "AgentsMdSegmentWriter",
    "render_cursor_cli_t1",
]

TRW_DISTILL_BEGIN = "<!-- TRW:DISTILL:BEGIN -->"
TRW_DISTILL_END = "<!-- TRW:DISTILL:END -->"

# CUR-05 default channel entry (inline — no manifest lookup needed for simple writes)
_CUR05_CHANNEL_ID = "cursor-cli-agents-md-snapshot"


def render_cursor_cli_t1(sidecar: dict[str, Any]) -> str:
    """Render T1 cursor-cli distill summary for AGENTS.md injection.

    Content: top-3 risk files, key conventions, top-3 survivors.
    Quota: ≤1536 bytes.
    """
    payload = sidecar.get("payload") or sidecar
    sha = str(sidecar.get("sha", "unknown"))[:8]
    ts = str(payload.get("generated_at", sidecar.get("generated_at", "unknown")))

    # Top-3 hotspots by risk score
    hotspots = payload.get("hotspots", [])
    sorted_hs = sorted(
        (h for h in hotspots if isinstance(h, dict)),
        key=lambda h: -float(h.get("risk_score", 0.0)),
    )[:3]

    # Key conventions (up to 3)
    conventions = payload.get("conventions", [])
    top_conventions = [c for c in conventions if isinstance(c, dict)][:3]

    # Top-3 survivors
    survivors = payload.get("edge_case_survivors", [])
    top_survivors = [s for s in survivors if isinstance(s, dict)][:3]

    lines: list[str] = [
        f"## TRW Distill Summary (sha={sha}, ts={ts})\n",
        "_cursor-cli distill context — regenerate: trw-distill self-improve mdc-emit_\n",
    ]

    if sorted_hs:
        lines.append("\n### High-Risk Files\n")
        for h in sorted_hs:
            fp = h.get("file_path", "?")
            rs = float(h.get("risk_score", 0))
            reason = h.get("reason", "")
            lines.append(f"- `{fp}` (risk={rs:.2f}): {reason}\n")

    if top_conventions:
        lines.append("\n### Key Conventions\n")
        for c in top_conventions:
            slug = c.get("slug", "?")
            title = c.get("title", "")
            lines.append(f"- **{slug}**: {title}\n")

    if top_survivors:
        lines.append("\n### Survivor Patterns\n")
        for s in top_survivors:
            fp = s.get("file_path", "?")
            desc = s.get("description", "")[:80]
            lines.append(f"- `{fp}`: {desc}\n")

    return "".join(lines)


def _content_for_tier(tier: str, sidecar: dict[str, Any]) -> str:
    """Return distill content for the given tier."""
    if tier == "T0":
        return "_TRW distill data available — use trw_codebase_risk_report() for full analysis_\n"
    return render_cursor_cli_t1(sidecar)


class AgentsMdSegmentWriter:
    """Writer for CUR-05: cursor-cli AGENTS.md marker-replace segment.

    Args:
        repo_root: Repository root directory.
        entry: Channel entry for CUR-05 (default built-in entry).
        render_log_path: Optional path for the render log.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        entry: ChannelEntry | None = None,
        render_log_path: Path | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._entry = entry or _build_default_entry()
        self._render_log_path = render_log_path

    def write(
        self,
        sidecar: dict[str, Any],
        *,
        target_file: Path | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> InstructionSegmentResult:
        """Write the distill segment to AGENTS.md via marker-replace.

        Idempotent: calling twice with the same sidecar produces byte-identical output.
        Content outside the TRW:DISTILL markers is preserved byte-for-byte.

        Args:
            sidecar: Distill sidecar dict.
            target_file: Override AGENTS.md path (default: repo_root/AGENTS.md).
            force: Skip TTL check and conflict detection.
            dry_run: Return would-be content without writing.

        Returns:
            InstructionSegmentResult describing the outcome.
        """
        entry = self._entry
        channel_id = entry.id
        resolved_target = target_file or (self._repo_root / entry.file if entry.file else self._repo_root / "AGENTS.md")

        # Acquire lock
        lock_path: Path
        if entry.lock_file:
            lock_path = self._repo_root / entry.lock_file
        else:
            lock_path = self._repo_root / ".trw" / "channels" / f"{channel_id}.lock"

        try:
            lock = ChannelLock(lock_path)
            lock.__enter__()
        except ChannelLockSkip:
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="skipped_lock",
            )

        try:
            return self._write_under_lock(
                entry=entry,
                channel_id=channel_id,
                target_file=resolved_target,
                sidecar=sidecar,
                force=force,
                dry_run=dry_run,
            )
        except Exception as exc:
            log.warning(
                "agents_md_write_error",
                channel_id=channel_id,
                error=str(exc),
                outcome="error",
            )
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="error",
                error=str(exc),
            )
        finally:
            try:
                lock.__exit__(None, None, None)
            except Exception:
                pass

    def _write_under_lock(
        self,
        *,
        entry: ChannelEntry,
        channel_id: str,
        target_file: Path,
        sidecar: dict[str, Any],
        force: bool,
        dry_run: bool,
    ) -> InstructionSegmentResult:
        """Execute marker-replace write while the channel lock is held."""
        channels_dir = self._repo_root / ".trw" / "channels"
        state_file = state_path_for(channel_id, channels_dir)

        # Render new interior content
        tier = entry.tier_default or "T1"

        def render_at_tier(t: str) -> str:
            return _content_for_tier(t, sidecar)

        body = render_at_tier(tier)

        # Quota enforcement with tier-down
        quota = entry.quota_total_bytes
        while quota is not None and not check_quota(content_bytes=len(body.encode("utf-8")), quota_total_bytes=quota):
            next_tier = tier_down(tier, tier_min=entry.tier_min)
            if next_tier == tier:
                break
            tier = next_tier
            body = render_at_tier(tier)

        # Read existing file for marker-replace
        existing_content = ""
        if target_file.exists():
            try:
                existing_content = target_file.read_text(encoding="utf-8")
            except OSError:
                existing_content = ""

        # Apply marker-replace
        markers = entry.markers
        new_full_content = replace_distill_segment(
            existing_content,
            body,
            markers=markers,
        )

        bytes_written = len(new_full_content.encode("utf-8"))
        tokens_est = max(1, int(bytes_written / 3.5))

        if dry_run:
            return InstructionSegmentResult(
                channel_id=channel_id,
                status="dry_run",
                tier_used=tier,
                bytes_written=bytes_written,
                tokens_estimated=tokens_est,
                would_write=new_full_content,
            )

        # Atomic write
        render_log = RenderLog(channels_dir / "render-log.jsonl")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        log_entry = write_atomic(
            target_file,
            new_full_content,
            channel_id=channel_id,
            render_log=render_log,
            sidecar_sha=str(sidecar.get("sha")) if sidecar.get("sha") else None,
        )

        # Persist state
        import hashlib

        seg_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
        new_state = ChannelState(
            channel_id=channel_id,
            last_render_tier=tier,
            last_render_bytes=log_entry.bytes_written,
            last_render_tokens_est=tokens_est,
            last_sidecar_sha=str(sidecar.get("sha")) if sidecar.get("sha") else None,
            segment_interior_sha256=seg_sha,
            last_render_ts=log_entry.ts,
            last_render_sha=log_entry.sha,
        )
        write_state(new_state, state_file)

        # Telemetry (fail-open)
        try:
            append_channel_event(
                channel_id=channel_id,
                client=entry.client,
                event_type="push_write",
                log_path=self._repo_root / ".trw" / "telemetry" / "channel-events.jsonl",
                tier=tier,
                bytes_written=log_entry.bytes_written,
                outcome="written",
            )
        except Exception:
            pass

        return InstructionSegmentResult(
            channel_id=channel_id,
            status="written",
            tier_used=tier,
            bytes_written=log_entry.bytes_written,
            tokens_estimated=tokens_est,
        )


def _build_default_entry() -> ChannelEntry:
    """Build the default CUR-05 channel entry."""
    from trw_mcp.channels._manifest_models import (
        ChannelStatus,
        ChannelSurface,
        CleanupAction,
        CleanupConfig,
        CleanupTrigger,
        HumanEditDetection,
        MarkersConfig,
        ProvenanceConfig,
        WriteStrategy,
    )

    return ChannelEntry(
        id=_CUR05_CHANNEL_ID,
        client="cursor-cli",
        surface=ChannelSurface.AGENTS_MD_SEGMENT,
        telemetry_tag="cursor.cli.agents_md",
        file="AGENTS.md",
        lock_file=".trw/channels/cursor-cli-agents-md.lock",
        status=ChannelStatus.ACTIVE,
        write_strategy=WriteStrategy.MARKER_REPLACE,
        tier_default="T1",
        tier_min="T0",
        distill_record_types=["convention", "hotspot", "edge_case_survivor"],
        ttl_commits=30,
        ttl_days=14,
        quota_total_bytes=1536,
        mdc_always_apply=False,
        regenerate_cmd="trw-distill self-improve mdc-emit",
        description="CUR-05: cursor-cli AGENTS.md T1 segment",
        markers=MarkersConfig(
            start=TRW_DISTILL_BEGIN,
            end=TRW_DISTILL_END,
        ),
        provenance=ProvenanceConfig(
            enabled=True,
            detection=HumanEditDetection.MARKER_BOUNDARY,
        ),
        cleanup=CleanupConfig(
            trigger=CleanupTrigger.TTL_EXCEEDED,
            action=CleanupAction.CLEAR_SEGMENT,
        ),
    )
