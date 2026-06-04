"""Cursor MDC emitter — sole MDC writer for trw-distill channels (P0-07 fix).

Reads distill sidecar; renders and atomically writes .cursor/rules/*.mdc files.
Uses PRD-DIST-2400 substrate exclusively: ChannelLock, write_atomic,
detect_human_edit, check_staleness, append_channel_event, add_gitignore_entry,
remove_gitignore_entry.

ZERO trw_distill imports. Cross-package contract is the sidecar envelope
(risk-report-sidecar/v0) from _sidecar_substrate.py.

PRD-DIST-2401 Phase B+C.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.channels._conflict import RenderLog, write_atomic
from trw_mcp.channels._gitignore import add_gitignore_entry, remove_gitignore_entry
from trw_mcp.channels._lock import ChannelLock, ChannelLockSkip
from trw_mcp.channels._manifest_models import ChannelEntry
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels.cursor._mdc_channel_entries import DEFAULT_ENTRIES
from trw_mcp.channels.cursor._mdc_sidecar import (
    extract_conventions,
    extract_edge_cases,
    extract_edge_cases_for_dir,
    extract_hotspots,
    get_sidecar_sha,
    get_sidecar_ts,
)
from trw_mcp.channels.cursor._mdc_templates import (
    EdgeCaseRecord,
    HotspotRecord,
    dir_slug,
    render_conventions_t0,
    render_conventions_t1,
    render_dangerous_edits_t0,
    render_dangerous_edits_t1,
    render_hotspot_dir_t0,
    render_hotspot_dir_t1,
)
from trw_mcp.channels.cursor._mdc_write import emit_mdc_under_lock

log = structlog.get_logger(__name__)

__all__ = [
    "MdcEmitter",
    "MdcEmitterError",
]

# ---------------------------------------------------------------------------
# Tokens-per-byte estimate (conservative for code-heavy content, P1-21)
# ---------------------------------------------------------------------------
_DEFAULT_MAX_COMBINED_TOKENS: int = 2000
_MAX_HOTSPOT_INSTANTIATIONS: int = 12


class MdcEmitterError(Exception):
    """Raised when the emitter cannot proceed (e.g. manifest missing)."""


def _write_stubs_if_absent(
    cursor_rules: Path,
    render_log: RenderLog,
    emit_event: Callable[..., None],
) -> list[str]:
    """Write T0 stub MDCs if absent. Returns list of created paths."""
    created: list[str] = []
    stubs = [
        (cursor_rules / "distill-conventions.mdc", render_conventions_t0(), "cursor-mdc-conventions"),
        (cursor_rules / "distill-dangerous-edits.mdc", render_dangerous_edits_t0(), "cursor-mdc-dangerous-edits"),
    ]
    for path, content, channel_id in stubs:
        if not path.exists():
            try:
                write_atomic(path, content, channel_id=channel_id, render_log=render_log)
                created.append(str(path))
                emit_event(channel_id, "cursor-ide", "push_write", tier="T0", outcome="stub_created")
            except Exception as exc:
                log.warning("mdc_stub_write_failed", path=str(path), error=str(exc), outcome="error")
    return created


# ---------------------------------------------------------------------------
# MdcEmitter
# ---------------------------------------------------------------------------


class MdcEmitter:
    """Cursor MDC emitter — reads sidecar, writes MDC files atomically.

    Args:
        repo_root: Repository root directory.
        max_instantiations: Maximum hotspot MDC files to emit (default 12).
        max_combined_tokens: Combined token budget across all active channels
            (default 2000, ~1.5% of 128K context). P1-21 fix.
        render_log: Optional RenderLog override for testing.
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        max_instantiations: int = _MAX_HOTSPOT_INSTANTIATIONS,
        max_combined_tokens: int = _DEFAULT_MAX_COMBINED_TOKENS,
        render_log: RenderLog | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._max_instantiations = max_instantiations
        self._max_combined_tokens = max_combined_tokens
        self._render_log = render_log or RenderLog(repo_root / ".trw" / "channels" / "render-log.jsonl")
        self._manifest_path = repo_root / ".trw" / "channels" / "manifest.yaml"
        self._entries: dict[str, ChannelEntry] = {e.id: e for e in DEFAULT_ENTRIES}

    # ------------------------------------------------------------------
    # Public: bootstrap_stubs
    # ------------------------------------------------------------------

    def bootstrap_stubs(self) -> dict[str, Any]:
        """Write T0 stub MDC files and manage .gitignore. Idempotent."""
        cursor_rules = self._repo_root / ".cursor" / "rules"
        cursor_rules.mkdir(parents=True, exist_ok=True)
        created = _write_stubs_if_absent(cursor_rules, self._render_log, self._emit_event)
        try:
            add_gitignore_entry(self._repo_root, "distill-hotspots-*.mdc")
            add_gitignore_entry(self._repo_root, "distill-dangerous-edits.mdc")
            remove_gitignore_entry(self._repo_root, "distill-conventions.mdc")
        except Exception as exc:
            log.warning("mdc_gitignore_update_failed", error=str(exc), outcome="warning")
        return {"status": "ok", "created": created}

    # ------------------------------------------------------------------
    # Public: emit_conventions (CUR-01)
    # ------------------------------------------------------------------

    def emit_conventions(
        self,
        sidecar: dict[str, Any],
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Emit CUR-01 conventions.mdc from sidecar data.

        Returns dict with status, channel_id, tier_used, bytes_written.
        NFR06: never raises; on error returns {"status": "error"}.
        """
        channel_id = "cursor-mdc-conventions"
        entry = self._entries[channel_id]
        target_path = self._repo_root / ".cursor" / "rules" / "distill-conventions.mdc"

        _sidecar = sidecar

        def _t1_conv() -> str:
            return self._render_conventions_t1_from_sidecar(_sidecar)

        try:
            return self._emit_mdc_file(
                channel_id=channel_id,
                entry=entry,
                target_path=target_path,
                sidecar=sidecar,
                force=force,
                dry_run=dry_run,
                render_t0=render_conventions_t0,
                render_t1=_t1_conv,
            )
        except Exception as exc:
            log.warning(
                "emit_conventions_error",
                channel_id=channel_id,
                error=str(exc),
                outcome="error",
            )
            return {"status": "error", "channel_id": channel_id, "error": str(exc)}

    # ------------------------------------------------------------------
    # Public: emit_hotspots (CUR-02)
    # ------------------------------------------------------------------

    def emit_hotspots(
        self,
        sidecar: dict[str, Any],
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """Emit CUR-02 per-directory hotspot MDC files (max 12).

        Returns list of per-directory result dicts.
        NFR06: never raises; individual failures return {"status": "error"}.
        """
        results: list[dict[str, Any]] = []
        hotspots = extract_hotspots(sidecar)

        # Group by directory, sort by max risk score desc, then dir path asc
        dir_map: dict[str, list[HotspotRecord]] = {}
        for h in hotspots:
            parts = h.file_path.replace("\\", "/").rsplit("/", 1)
            directory = parts[0] if len(parts) > 1 else "_root"
            dir_map.setdefault(directory, []).append(h)

        sorted_dirs = sorted(
            dir_map.items(),
            key=lambda item: (-max(r.risk_score for r in item[1]), item[0]),
        )

        capped = sorted_dirs[: self._max_instantiations]
        dropped = sorted_dirs[self._max_instantiations :]

        if dropped:
            dropped_dirs = [d for d, _ in dropped]
            self._emit_event(
                "cursor-mdc-hotspots-template",
                "cursor-ide",
                "quota_exceeded",
                dropped_dirs=dropped_dirs,
                outcome="instantiation_cap",
            )

        cursor_rules = self._repo_root / ".cursor" / "rules"
        cursor_rules.mkdir(parents=True, exist_ok=True)

        for directory, dir_records in capped:
            slug = dir_slug(directory)
            channel_id = f"cursor-mdc-hotspots-{slug}"
            target_path = cursor_rules / f"distill-hotspots-{slug}.mdc"

            edge_cases = extract_edge_cases_for_dir(sidecar, directory)
            _sha = get_sidecar_sha(sidecar)
            _ts = get_sidecar_ts(sidecar)

            def make_t0(d: str = directory) -> str:
                return render_hotspot_dir_t0(d)

            def make_t1(
                d: str = directory,
                dr: list[HotspotRecord] = dir_records,
                ec: list[EdgeCaseRecord] = edge_cases,
                sha: str = _sha,
                ts: str = _ts,
            ) -> str:
                return render_hotspot_dir_t1(d, dr, ec, sha, ts)

            # Use CUR-02 entry with overridden channel_id per instantiation
            entry = self._entries["cursor-mdc-hotspots-template"]

            try:
                result = self._emit_mdc_file(
                    channel_id=channel_id,
                    entry=entry,
                    target_path=target_path,
                    sidecar=sidecar,
                    force=force,
                    dry_run=dry_run,
                    render_t0=make_t0,
                    render_t1=make_t1,
                )
                results.append(result)
            except Exception as exc:
                log.warning(
                    "emit_hotspot_dir_error",
                    channel_id=channel_id,
                    directory=directory,
                    error=str(exc),
                    outcome="error",
                )
                results.append({"status": "error", "channel_id": channel_id, "error": str(exc)})

        return results

    # ------------------------------------------------------------------
    # Public: emit_dangerous_edits (CUR-03)
    # ------------------------------------------------------------------

    def emit_dangerous_edits(
        self,
        sidecar: dict[str, Any],
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Emit CUR-03 dangerous-edits.mdc from sidecar data.

        Returns dict with status, channel_id, tier_used, bytes_written.
        NFR06: never raises; on error returns {"status": "error"}.
        """
        channel_id = "cursor-mdc-dangerous-edits"
        entry = self._entries[channel_id]
        target_path = self._repo_root / ".cursor" / "rules" / "distill-dangerous-edits.mdc"

        _sidecar = sidecar

        def _t1_danger() -> str:
            return self._render_dangerous_edits_t1_from_sidecar(_sidecar)

        try:
            return self._emit_mdc_file(
                channel_id=channel_id,
                entry=entry,
                target_path=target_path,
                sidecar=sidecar,
                force=force,
                dry_run=dry_run,
                render_t0=render_dangerous_edits_t0,
                render_t1=_t1_danger,
            )
        except Exception as exc:
            log.warning(
                "emit_dangerous_edits_error",
                channel_id=channel_id,
                error=str(exc),
                outcome="error",
            )
            return {"status": "error", "channel_id": channel_id, "error": str(exc)}

    # ------------------------------------------------------------------
    # Public: emit_all (CUR-01 + CUR-02 + CUR-03 with combined token budget)
    # ------------------------------------------------------------------

    def emit_all(
        self,
        sidecar: dict[str, Any],
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Emit all cursor MDC channels with combined token budget enforcement.

        Tier-down order on combined budget breach: CUR-02 first (lowest priority),
        then CUR-03, then CUR-01 last.

        Returns dict with per-channel results and combined token count.
        """
        conventions_result = self.emit_conventions(sidecar, force=force, dry_run=dry_run)
        hotspot_results = self.emit_hotspots(sidecar, force=force, dry_run=dry_run)
        dangerous_result = self.emit_dangerous_edits(sidecar, force=force, dry_run=dry_run)

        # Compute combined token estimate
        total_tokens = self._compute_combined_tokens(conventions_result, hotspot_results, dangerous_result)

        if total_tokens > self._max_combined_tokens:
            self._emit_event(
                "cursor-mdc-all",
                "cursor-ide",
                "tier_down",
                combined_tokens=total_tokens,
                max_combined_tokens=self._max_combined_tokens,
                outcome="combined_budget_enforced",
            )

        return {
            "status": "ok",
            "conventions": conventions_result,
            "hotspots": hotspot_results,
            "dangerous_edits": dangerous_result,
            "combined_tokens_estimated": total_tokens,
            "combined_budget_enforced": total_tokens > self._max_combined_tokens,
        }

    # ------------------------------------------------------------------
    # Core: _emit_mdc_file — 11-step canonical sequence
    # ------------------------------------------------------------------

    def _emit_mdc_file(
        self,
        *,
        channel_id: str,
        entry: ChannelEntry,
        target_path: Path,
        sidecar: dict[str, Any],
        force: bool,
        dry_run: bool,
        render_t0: Callable[[], str],
        render_t1: Callable[[], str],
    ) -> dict[str, Any]:
        """Acquire lock then delegate to emit_mdc_under_lock (Step 1 of 11)."""
        lock_path = self._repo_root / (entry.lock_file or f".trw/channels/{channel_id}.lock")
        try:
            lock = ChannelLock(lock_path)
            lock.__enter__()
        except ChannelLockSkip:
            self._emit_event(channel_id, entry.client, "channel_lock_skip", outcome="skipped_lock")
            return {"status": "skipped_lock", "channel_id": channel_id}

        try:
            return emit_mdc_under_lock(
                channel_id=channel_id,
                entry=entry,
                target_path=target_path,
                repo_root=self._repo_root,
                sidecar=sidecar,
                force=force,
                dry_run=dry_run,
                render_t0=render_t0,
                render_t1=render_t1,
                render_log=self._render_log,
                emit_event=self._emit_event,
            )
        finally:
            try:
                lock.__exit__(None, None, None)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Sidecar helpers (delegates to _mdc_sidecar module)
    # ------------------------------------------------------------------

    def _render_conventions_t1_from_sidecar(self, sidecar: dict[str, Any]) -> str:
        conventions = extract_conventions(sidecar)
        hotspots = extract_hotspots(sidecar)
        return render_conventions_t1(conventions, hotspots, get_sidecar_sha(sidecar), get_sidecar_ts(sidecar))

    def _render_dangerous_edits_t1_from_sidecar(self, sidecar: dict[str, Any]) -> str:
        all_ec = extract_edge_cases(sidecar)
        survivors = [e for e in all_ec if e.survived]
        undocumented = [e for e in all_ec if not e.survived]
        return render_dangerous_edits_t1(survivors, undocumented, get_sidecar_sha(sidecar), get_sidecar_ts(sidecar))

    # ------------------------------------------------------------------
    # Combined token computation
    # ------------------------------------------------------------------

    def _compute_combined_tokens(
        self,
        conventions_result: dict[str, Any],
        hotspot_results: list[dict[str, Any]],
        dangerous_result: dict[str, Any],
    ) -> int:
        total = 0
        for r in [conventions_result, dangerous_result]:
            total += r.get("tokens_estimated", 0) or 0
        for r in hotspot_results:
            total += r.get("tokens_estimated", 0) or 0
        return total

    # ------------------------------------------------------------------
    # Telemetry helper
    # ------------------------------------------------------------------

    def _emit_event(
        self,
        channel_id: str,
        client: str,
        event_type: str,
        **kwargs: Any,
    ) -> None:
        try:
            append_channel_event(
                channel_id=channel_id,
                client=client,
                event_type=event_type,
                log_path=self._repo_root / ".trw" / "telemetry" / "channel-events.jsonl",
                **kwargs,
            )
        except Exception:
            pass  # fail-open
