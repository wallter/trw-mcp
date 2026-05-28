# Parent facade: bootstrap/_ide_targets.py
"""Distill channel update wiring for each IDE client.

Extracted from ``_ide_targets.py`` to keep the facade under the 350
effective-LOC ceiling (PRD-DIST-243 / trw-mcp-python.md gate).

One function per client, each calling the corresponding
``install_<client>_distill_channels()`` bootstrap function.
All functions are fail-open: distill channel updates are additive
and must never block the core update flow.

Called from ``_ide_targets._update_*_artifacts()`` at the end of
each client's update sequence.
"""

from __future__ import annotations

from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


def _update_opencode_distill_channels(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Update opencode distill channels (FR41-FR43 — additive, fail-open)."""
    try:
        from ._opencode_distill_channels import install_opencode_distill_channels

        dc = install_opencode_distill_channels(target_dir)
        errors = dc.get("errors")
        if isinstance(errors, list):
            result["errors"].extend(errors)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(
            f"opencode distill channels update skipped: {exc}"
        )


def _update_codex_distill_channels(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Update codex distill channels (FR41-FR43 — additive, fail-open)."""
    try:
        from ._codex_distill_channels import install_codex_distill_channels

        dc = install_codex_distill_channels(target_dir)
        for key in ("created", "updated", "preserved", "errors"):
            items = dc.get(key)
            if isinstance(items, list):
                result.setdefault(key, []).extend(items)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(
            f"codex distill channels update skipped: {exc}"
        )


def _update_copilot_distill_channels(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Update copilot distill channels (FR41-FR43 — additive, fail-open)."""
    try:
        from ._copilot_distill_channels import install_copilot_distill_channels

        dc = install_copilot_distill_channels(target_dir)
        for key in ("created", "updated", "preserved", "errors"):
            items = dc.get(key)
            if isinstance(items, list):
                result.setdefault(key, []).extend(items)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(
            f"copilot distill channels update skipped: {exc}"
        )


def _update_antigravity_distill_channels(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Update antigravity distill channels (FR41-FR43 — additive, fail-open)."""
    try:
        from ._antigravity_distill_channels import install_antigravity_distill_channels

        dc = install_antigravity_distill_channels(target_dir)
        for key in ("created", "updated", "preserved", "errors"):
            items = dc.get(key)
            if isinstance(items, list):
                result.setdefault(key, []).extend(items)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(
            f"antigravity distill channels update skipped: {exc}"
        )


def _update_cursor_distill_channels(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Update cursor distill channels (FR41-FR43 — additive, fail-open)."""
    try:
        from ._cursor_distill_channels import install_cursor_distill_channels

        dc = install_cursor_distill_channels(target_dir)
        for key in ("created", "updated", "preserved", "errors"):
            items = dc.get(key)
            if isinstance(items, list):
                result.setdefault(key, []).extend(items)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(
            f"cursor distill channels update skipped: {exc}"
        )
