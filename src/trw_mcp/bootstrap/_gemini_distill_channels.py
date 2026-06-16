"""Gemini distill channel bootstrap — install entry-point.

PRD-DIST-2459 FR-3. Mirrors ``_claude_code_distill_channels.py`` but adapts to
Gemini CLI's hook surface (``.gemini/settings.json`` ``hooks.BeforeTool``).

Artifacts written:
  - .gemini/hooks/trw-before-tool-hint.sh   (GM-01 hook script)
  - .gemini/hooks/lib-distill-hint.sh       (GM-01 shared library)
  - .gemini/settings.json                   (hooks.BeforeTool entry merged in)
  - .trw/channels/manifest.yaml             (gm-01 channel entry merged)

The hook is gated OFF by default via the shared ``cc03_hook_enabled`` activation
gate (FR-6) — registering the hook is a clean no-op until the operator enables
it in ``.trw/config.yaml``. The hook itself NEVER denies / NEVER exits 2.

IP boundary: nothing here imports ``trw_distill``. The hook crosses the boundary
only through the distill-unaware ``compute_before_edit_hint`` sidecar reader.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

from trw_mcp.channels._manifest_loader import (
    ManifestValidationError,
    auto_recreate_empty,
    load,
    write,
)
from trw_mcp.channels._manifest_models import ChannelEntry
from trw_mcp.channels._provenance import now_utc_iso8601

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_gemini_channel_manifest",
    "install_gemini_distill_channels",
    "merge_before_tool_hook",
]

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "gemini" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-gemini.yaml"

_HOOKS_DATA_DIR = Path(__file__).parent.parent / "data" / "gemini" / "hooks"

# The two hook scripts that ship to .gemini/hooks/.
_HOOK_NAMES: tuple[str, ...] = ("trw-before-tool-hint.sh", "lib-distill-hint.sh")

# Relative (project-root) path the BeforeTool settings entry points at.
_HOOK_REL_PATH = ".gemini/hooks/trw-before-tool-hint.sh"

# Stable name used to find/replace our managed BeforeTool hook entry idempotently.
_HOOK_ENTRY_NAME = "trw-distill-before-edit-hint"

# Tool names the BeforeTool hook matches (Gemini file-editing tools).
_HOOK_MATCHER = "write_file|replace|edit_file|create_file"

# Hook latency budget in milliseconds (Gemini hook timeout, ms).
_HOOK_TIMEOUT_MS = 3000


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_gemini_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Load manifest-gemini.yaml and merge the GM-01 ChannelEntry record.

    Merge is additive — existing entries for other clients are preserved.
    All-or-nothing: if any entry fails validation, raises ManifestValidationError.
    """
    yaml = YAML(typ="safe")
    raw: Any = yaml.load(_MANIFEST_DATA.read_text(encoding="utf-8")) or {}
    raw_channels: list[dict[str, Any]] = raw.get("channels", [])

    validated: list[ChannelEntry] = []
    for entry_dict in raw_channels:
        try:
            validated.append(ChannelEntry.model_validate(entry_dict))
        except Exception as exc:
            raise ManifestValidationError(f"gemini manifest entry validation failed: {exc}") from exc

    manifest_path = repo_root / ".trw" / "channels" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        manifest = load(manifest_path)
    except Exception:
        auto_recreate_empty(manifest_path)
        manifest = load(manifest_path)

    existing_ids = {e.id for e in manifest.channels}
    added = 0
    for entry in validated:
        if entry.id not in existing_ids:
            manifest.channels.append(entry)
            existing_ids.add(entry.id)
            added += 1

    manifest.generated_at = now_utc_iso8601()
    write(manifest, manifest_path)

    log.debug(
        "gemini_manifest_bootstrapped",
        added=added,
        total=len(manifest.channels),
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Hook script install
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
    """Install a bundled hook script to .gemini/hooks/ if the source exists."""
    content = _get_hook_content(hook_name)
    if content is None:
        log.debug("gemini_hook_source_absent", hook=hook_name, outcome="skipped")
        return

    hooks_dir = repo_root / ".gemini" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / hook_name

    try:
        existed = dest.exists()
        if existed and dest.read_text(encoding="utf-8") == content:
            result["preserved"].append(str(dest.relative_to(repo_root)))
            return
        dest.write_text(content, encoding="utf-8")
        if hook_name.endswith(".sh"):
            dest.chmod(dest.stat().st_mode | 0o111)
        key = "updated" if existed else "created"
        result[key].append(str(dest.relative_to(repo_root)))
    except OSError as exc:
        result["errors"].append(f"Failed to install {hook_name}: {exc}")


# ---------------------------------------------------------------------------
# .gemini/settings.json hooks.BeforeTool merge
# ---------------------------------------------------------------------------


def _managed_before_tool_block() -> dict[str, Any]:
    """Return the TRW-managed BeforeTool matcher block for settings.json."""
    return {
        "matcher": _HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "name": _HOOK_ENTRY_NAME,
                "command": f"sh {_HOOK_REL_PATH}",
                "timeout": _HOOK_TIMEOUT_MS,
            }
        ],
    }


def _is_managed_block(block: object) -> bool:
    """True if *block* is the TRW-managed BeforeTool entry (by hook name)."""
    if not isinstance(block, dict):
        return False
    hooks = block.get("hooks")
    if not isinstance(hooks, list):
        return False
    return any(isinstance(hook, dict) and hook.get("name") == _HOOK_ENTRY_NAME for hook in hooks)


def merge_before_tool_hook(settings: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *settings* with the GM-01 BeforeTool hook merged in.

    Idempotent: a pre-existing TRW-managed block (matched by hook name) is
    replaced rather than duplicated. All other settings, MCP servers, and
    non-TRW hook entries are preserved.
    """
    merged = dict(settings)

    hooks_cfg = merged.get("hooks")
    if not isinstance(hooks_cfg, dict):
        hooks_cfg = {}
    else:
        hooks_cfg = dict(hooks_cfg)

    before_tool = hooks_cfg.get("BeforeTool")
    if not isinstance(before_tool, list):
        before_tool = []
    # Drop any prior TRW-managed block, keep everything else.
    before_tool = [b for b in before_tool if not _is_managed_block(b)]
    before_tool.append(_managed_before_tool_block())

    hooks_cfg["BeforeTool"] = before_tool
    merged["hooks"] = hooks_cfg
    return merged


def _install_settings_hook(
    repo_root: Path,
    result: dict[str, list[str]],
) -> None:
    """Merge the BeforeTool hook entry into .gemini/settings.json (idempotent)."""
    settings_path = repo_root / ".gemini" / "settings.json"
    rel = ".gemini/settings.json"

    existing: dict[str, Any] = {}
    existed = settings_path.exists()
    if existed:
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            # Corrupt/unreadable file: do not clobber — record and skip.
            log.warning("gemini_settings_unreadable", error=str(exc), outcome="warning")
            result["errors"].append(f"Failed to read {rel}: {exc}")
            return

    merged = merge_before_tool_hook(existing)
    new_text = json.dumps(merged, indent=2) + "\n"

    if existed:
        try:
            if settings_path.read_text(encoding="utf-8") == new_text:
                result["preserved"].append(rel)
                return
        except (OSError, UnicodeDecodeError):
            pass

    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(new_text, encoding="utf-8")
        result["updated" if existed else "created"].append(rel)
    except OSError as exc:
        result["errors"].append(f"Failed to write {rel}: {exc}")


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_gemini_distill_channels(
    target_dir: Path,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install all Gemini distill channel artifacts.

    Writes the GM-01 hook scripts to ``.gemini/hooks/``, merges the
    ``hooks.BeforeTool`` entry into ``.gemini/settings.json``, and merges the
    GM-01 channel entry into ``.trw/channels/manifest.yaml``.

    Idempotent on re-run. Gated OFF by default via ``cc03_hook_enabled`` — the
    hook is registered but a clean no-op until the operator opts in.

    Args:
        target_dir: Repository root directory.
        force: Reserved for API parity with sibling installers (unused — the
            merges are already idempotent and non-destructive).

    Returns:
        Dict with ``created``, ``updated``, ``preserved``, ``errors`` lists.
    """
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }

    # 1. Install GM-01 hook scripts (.gemini/hooks/)
    for hook_name in _HOOK_NAMES:
        try:
            _install_hook(target_dir, hook_name, result)
        except Exception as exc:  # justified: fail-open, hook install is best-effort
            log.warning("gemini_hook_install_failed", hook=hook_name, error=str(exc), outcome="warning")
            result["errors"].append(f"GM-01 hook {hook_name} install failed: {exc}")

    # 2. Merge hooks.BeforeTool into .gemini/settings.json
    try:
        _install_settings_hook(target_dir, result)
    except Exception as exc:  # justified: fail-open, settings merge is best-effort
        log.warning("gemini_settings_hook_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"GM-01 settings merge failed: {exc}")

    # 3. Bootstrap channel manifest (gm-01 channel entry)
    try:
        bootstrap_gemini_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning("gemini_manifest_validation_error", error=str(exc), outcome="warning")
        result["errors"].append(f"Gemini manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("gemini_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Gemini manifest bootstrap failed: {exc}")

    log.debug(
        "gemini_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
