"""Claude Code distill channel bootstrap — install entry-point.

Installs all five Claude Code distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .claude/agents/trw-distill-explorer.md    (CC-05)
  - .claude/hooks/pre-tool-distill-hint.sh    (CC-03 — opt-in gate applies)
  - .claude/hooks/lib-distill-hint.sh         (CC-03 shared library)
  - .trw/channels/manifest.yaml               (five CC channel entries merged)

NOTE: .claude/settings.json is NOT modified — operator opt-in per PRD-2405 OQ-01.

PRD-DIST-2405 FR41-FR43.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.bootstrap._file_ops import _new_result
from trw_mcp.channels._manifest_loader import ManifestValidationError
from trw_mcp.channels.claude_code._explorer_subagent import install_cc05_subagent

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_cc_channel_manifest",
    "install_claude_code_distill_channels",
]

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "claude_code" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-claude-code.yaml"

# Hook scripts are stored in the dev repo's .claude/hooks/ and are
# bundled here as static strings for distribution to target projects.
_HOOKS_DATA_DIR = Path(__file__).parent.parent / "data" / "claude_code" / "hooks"


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_cc_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Add Claude Code channel entries while preserving other clients."""
    added, total = merge_distill_channel_manifest(repo_root, _MANIFEST_DATA, "claude-code")
    log.debug(
        "cc_manifest_bootstrapped",
        added=added,
        total=total,
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Hook script content (shipped with the package, installed to target project)
# ---------------------------------------------------------------------------


def _get_hook_content(hook_name: str) -> str | None:
    """Return bundled hook script content from data directory, or None if absent."""
    hook_path = _HOOKS_DATA_DIR / hook_name
    if hook_path.exists():
        return hook_path.read_text(encoding="utf-8")
    return None


def _install_hook(
    repo_root: Path,
    hook_name: str,
    result: dict[str, list[str]],
) -> None:
    """Install a bundled hook script to .claude/hooks/ if the source exists."""
    content = _get_hook_content(hook_name)
    if content is None:
        # Source not present in this distribution — skip silently.
        log.debug("cc_hook_source_absent", hook=hook_name, outcome="skipped")
        return

    hooks_dir = repo_root / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / hook_name

    try:
        existed = dest.exists()
        if existed and dest.read_text(encoding="utf-8") == content:
            result["preserved"].append(str(dest.relative_to(repo_root)))
            return
        dest.write_text(content, encoding="utf-8")
        # Make shell scripts executable
        if hook_name.endswith(".sh"):
            dest.chmod(dest.stat().st_mode | 0o111)
        key = "updated" if existed else "created"
        result[key].append(str(dest.relative_to(repo_root)))
    except OSError as exc:
        result["errors"].append(f"Failed to install {hook_name}: {exc}")


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_claude_code_distill_channels(
    target_dir: Path,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install all Claude Code distill channel artifacts.

    Called from ``_init_project_ide._install_opencode_artifacts`` and
    ``_ide_targets._update_opencode_artifacts`` equivalents.

    Args:
        target_dir: Repository root directory.
        force: When True, overwrite existing artifacts unconditionally.

    Returns:
        Dict with ``created``, ``updated``, ``preserved``, ``errors`` lists.
    """
    result = _new_result()

    # 1. Install CC-05 subagent (.claude/agents/trw-distill-explorer.md)
    try:
        written = install_cc05_subagent(target_dir)
        rel = ".claude/agents/trw-distill-explorer.md"
        result["created" if written else "preserved"].append(rel)
    except Exception as exc:  # justified: fail-open, subagent is best-effort
        log.warning("cc05_subagent_install_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"CC-05 subagent install failed: {exc}")

    # 2. Install CC-03 hook scripts (.claude/hooks/)
    # NOTE: does NOT modify .claude/settings.json — operator opt-in (OQ-01)
    for hook_name in ("pre-tool-distill-hint.sh", "lib-distill-hint.sh"):
        try:
            _install_hook(target_dir, hook_name, result)
        except Exception as exc:  # justified: fail-open, hook install is best-effort
            log.warning("cc_hook_install_failed", hook=hook_name, error=str(exc), outcome="warning")
            result["errors"].append(f"CC-03 hook {hook_name} install failed: {exc}")

    # 3. Bootstrap channel manifest (five CC channel entries)
    try:
        bootstrap_cc_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning(
            "cc_manifest_validation_error",
            error=str(exc),
            outcome="warning",
        )
        result["errors"].append(f"CC manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("cc_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"CC manifest bootstrap failed: {exc}")

    log.debug(
        "claude_code_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
