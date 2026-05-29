"""trw_channel_render MCP tool (PRD-DIST-2400 FR17).

Renders (or re-renders) a specific channel from
``.trw/channels/manifest.yaml``.

Dispatches by ``surface`` type:
- ``instruction_file_segment`` / ``agents_md_segment``: delegated to
  ``render_instruction_segment()`` from Phase D1.
- All other surfaces return ``status="unsupported_surface_in_substrate"``
  — downstream PRDs (2401/2405/2406) implement those surfaces.

NEVER raises — all error paths return a ``status="error"`` dict.

IP boundary: zero ``trw_distill`` imports permitted here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import structlog
from fastmcp import FastMCP

log = structlog.get_logger(__name__)

_DEFAULT_CHANNELS_MANIFEST = ".trw/channels/manifest.yaml"
# Surfaces that Phase D1 renderer handles.
_INSTRUCTION_SURFACES = frozenset(
    {
        "instruction_file_segment",
        "agents_md_segment",
    }
)

__all__ = [
    "compute_channel_render",
    "register_channel_render_tools",
]


def _resolve_repo_root(repo_root: str | None) -> Path | None:
    """Resolve git repo root via subprocess or explicit override."""
    if repo_root is not None:
        return Path(repo_root)
    git = shutil.which("git")
    if git is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [git, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            stripped = proc.stdout.strip()
            if stripped:
                return Path(stripped)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _resolve_sidecar_sha(repo_root: Path) -> str | None:
    """Return current git HEAD SHA, or None on failure."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [git, "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            if sha and len(sha) == 40:
                return sha
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def compute_channel_render(
    *,
    channel_id: str,
    force: bool = False,
    repo_root: str | None = None,
    dry_run: bool = False,
    tier_override: str | None = None,
) -> dict[str, Any]:
    """Pure-Python entry point — also used by tests.

    Returns a dict with keys matching FR17 specification.  NEVER raises.
    """
    from trw_mcp.channels._manifest_loader import (
        ManifestMissingError,
        ManifestValidationError,
        auto_recreate_empty,
        load,
    )
    from trw_mcp.channels._manifest_models import ChannelEntry

    resolved_root = _resolve_repo_root(repo_root)

    # Determine manifest path.
    if resolved_root is not None:
        manifest_path = resolved_root / _DEFAULT_CHANNELS_MANIFEST
    else:
        manifest_path = Path(_DEFAULT_CHANNELS_MANIFEST)

    # Load manifest; auto-recover on missing (FR15 parity).
    try:
        manifest = load(manifest_path)
    except ManifestMissingError:
        auto_recreate_empty(manifest_path)
        log.warning(
            "channel_manifest_missing_auto_recovered",
            channel_id=channel_id,
            manifest_path=str(manifest_path),
        )
        return {
            "channel_id": channel_id,
            "status": "not_found",
            "tier_used": None,
            "tokens_emitted": None,
            "bytes_written": None,
            "conflict_detected": False,
            "ttl_commits_remaining": None,
            "would_write": None,
            "error": "Manifest was missing; auto-recreated empty manifest. Re-register channel and retry.",
        }
    except ManifestValidationError as exc:
        return {
            "channel_id": channel_id,
            "status": "error",
            "tier_used": None,
            "tokens_emitted": None,
            "bytes_written": None,
            "conflict_detected": False,
            "ttl_commits_remaining": None,
            "would_write": None,
            "error": f"Manifest validation error: {exc}",
        }

    # Find the channel entry.
    entry: ChannelEntry | None = None
    for ch in manifest.channels:
        if ch.id == channel_id:
            entry = ch
            break

    if entry is None:
        return {
            "channel_id": channel_id,
            "status": "not_found",
            "tier_used": None,
            "tokens_emitted": None,
            "bytes_written": None,
            "conflict_detected": False,
            "ttl_commits_remaining": None,
            "would_write": None,
            "error": f"Channel '{channel_id}' not found in manifest",
        }

    # Use enum value or string for surface comparison.
    surface_val: str = entry.surface.value if hasattr(entry.surface, "value") else str(entry.surface)

    if surface_val not in _INSTRUCTION_SURFACES:
        return {
            "channel_id": channel_id,
            "status": "unsupported_surface_in_substrate",
            "tier_used": None,
            "tokens_emitted": None,
            "bytes_written": None,
            "conflict_detected": False,
            "ttl_commits_remaining": None,
            "would_write": None,
            "error": (
                f"Surface '{surface_val}' is not handled by the substrate renderer. "
                "Downstream PRDs (2401/2405/2406) implement this surface."
            ),
        }

    # Dispatch to instruction_segment renderer.
    if resolved_root is None:
        return {
            "channel_id": channel_id,
            "status": "error",
            "tier_used": None,
            "tokens_emitted": None,
            "bytes_written": None,
            "conflict_detected": False,
            "ttl_commits_remaining": None,
            "would_write": None,
            "error": "Could not resolve git repo root; pass repo_root explicitly",
        }

    from trw_mcp.channels.instruction_segment import render_instruction_segment

    # Apply tier_override by injecting it as the tier passed to the callback.
    # The renderer uses entry.tier_default; we override by wrapping the content
    # callback so it always returns content keyed on the effective tier.
    effective_tier = tier_override if tier_override is not None else str(entry.tier_default)

    def _placeholder_content(tier_str: str) -> str:
        # Use effective_tier so tier_override is reflected in content.
        t = tier_override if tier_override is not None else tier_str
        return f"# {channel_id} — placeholder content for tier {t}"

    sidecar_sha = _resolve_sidecar_sha(resolved_root)

    # If tier_override supplied, create a patched entry with tier_default set.
    render_entry = entry
    if tier_override is not None:
        render_entry = entry.model_copy(update={"tier_default": effective_tier})

    result = render_instruction_segment(
        entry=render_entry,
        repo_root=resolved_root,
        sidecar_sha=sidecar_sha,
        content_for_tier=_placeholder_content,
        dry_run=dry_run,
        force=force,
    )

    return {
        "channel_id": channel_id,
        "status": result.status,
        "tier_used": result.tier_used,
        "tokens_emitted": result.tokens_estimated,
        "bytes_written": result.bytes_written,
        "conflict_detected": result.conflict_detected,
        "ttl_commits_remaining": result.ttl_commits_remaining,
        "would_write": result.would_write,
        "error": result.error,
    }


def register_channel_render_tools(server: FastMCP) -> None:
    """Register trw_channel_render on the MCP server."""

    @server.tool()
    def trw_channel_render(
        channel_id: str,
        force: bool = False,
        repo_root: str | None = None,
        dry_run: bool = False,
        tier_override: str | None = None,
    ) -> dict[str, Any]:
        """Render (or re-render) a specific channel from .trw/channels/manifest.yaml.

        Dispatches by surface type. Instruction-segment surfaces are handled
        directly; other surfaces return status="unsupported_surface_in_substrate"
        (downstream PRDs 2401/2405/2406 implement those).

        Use when: you need to render or re-render a specific channel from the manifest to keep segments in sync.

        Returns: {channel_id, status, tier_used, tokens_emitted, bytes_written,
                  conflict_detected, ttl_commits_remaining, would_write, error}.
        NEVER raises.
        """
        try:
            return compute_channel_render(
                channel_id=channel_id,
                force=force,
                repo_root=repo_root,
                dry_run=dry_run,
                tier_override=tier_override,
            )
        except Exception as exc:  # justified: absolute fail-open — never propagate to MCP client
            return {
                "channel_id": channel_id,
                "status": "error",
                "tier_used": None,
                "tokens_emitted": None,
                "bytes_written": None,
                "conflict_detected": False,
                "ttl_commits_remaining": None,
                "would_write": None,
                "error": str(exc),
            }
