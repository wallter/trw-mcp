"""Copilot distill channel bootstrap — install entry-point.

Installs all four Copilot distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .github/copilot-instructions.md segment   (C1 T0 beacon, marker_replace)
  - .github/instructions/trw-distill-hotspots.instructions.md  (C2 stub)
  - .vscode/mcp.json                           (C3 json_key_merge)
  - .trw/channels/manifest.yaml               (four copilot channel entries merged)

C4 (copilot-mcp-tool-return) is a pull channel — no file written.

PRD-DIST-2406 FR41-FR43.
"""

from __future__ import annotations

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
    "bootstrap_copilot_channel_manifest",
    "install_copilot_distill_channels",
]

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "copilot" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-copilot.yaml"

# C2 stub content for path-scoped instructions (T0 presence beacon)
_C2_STUB_CONTENT = """\
---
applyTo: '**'
---
<!-- TRW distill path-instructions — run `trw-distill self-improve risk-report` to populate -->
"""


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_copilot_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Load manifest-copilot.yaml and merge four ChannelEntry records.

    Merge is additive — existing entries for other clients are preserved.
    All-or-nothing: if any entry fails validation, raises ManifestValidationError.

    Args:
        repo_root: Repository root directory.

    Returns:
        Dict with ``status`` and ``count`` of entries added.
    """
    yaml = YAML(typ="safe")
    raw: Any = yaml.load(_MANIFEST_DATA.read_text(encoding="utf-8")) or {}
    raw_channels: list[dict[str, Any]] = raw.get("channels", [])

    # Validate all entries first (all-or-nothing)
    validated: list[ChannelEntry] = []
    for entry_dict in raw_channels:
        try:
            validated.append(ChannelEntry.model_validate(entry_dict))
        except Exception as exc:
            raise ManifestValidationError(f"copilot manifest entry validation failed: {exc}") from exc

    # Load or recreate target manifest
    manifest_path = repo_root / ".trw" / "channels" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        manifest = load(manifest_path)
    except Exception:
        auto_recreate_empty(manifest_path)
        manifest = load(manifest_path)

    # Merge: add new entries, preserve existing
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
        "copilot_manifest_bootstrapped",
        added=added,
        total=len(manifest.channels),
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_copilot_distill_channels(
    target_dir: Path,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install all Copilot distill channel artifacts.

    Installs C1 T0 beacon, C2 path-instructions stub, C3 vscode mcp.json,
    and merges channel manifest entries.

    Args:
        target_dir: Repository root directory.
        force: When True, overwrite existing artifacts unconditionally.

    Returns:
        Dict with ``created``, ``updated``, ``preserved``, ``errors`` lists.
    """
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }

    # 1. C3: .vscode/mcp.json (json_key_merge)
    try:
        from trw_mcp.channels.copilot import generate_vscode_mcp_config

        vscode_result = generate_vscode_mcp_config(target_dir, force=force)
        result["created"].extend(vscode_result.get("created", []))
        result["updated"].extend(vscode_result.get("updated", []))
        result["preserved"].extend(vscode_result.get("preserved", []))
        result["errors"].extend(vscode_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, vscode config is best-effort
        log.warning("copilot_vscode_mcp_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Copilot .vscode/mcp.json install failed: {exc}")

    # 2. C2: .github/instructions/trw-distill-hotspots.instructions.md (T0 stub)
    try:
        instructions_dir = target_dir / ".github" / "instructions"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        c2_path = instructions_dir / "trw-distill-hotspots.instructions.md"
        rel = ".github/instructions/trw-distill-hotspots.instructions.md"
        if c2_path.exists() and not force:
            result["preserved"].append(rel)
        else:
            existed = c2_path.exists()
            c2_path.write_text(_C2_STUB_CONTENT, encoding="utf-8")
            result["updated" if existed else "created"].append(rel)
    except Exception as exc:  # justified: fail-open, path instructions are best-effort
        log.warning("copilot_c2_stub_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Copilot C2 path instructions install failed: {exc}")

    # 3. Bootstrap channel manifest (four copilot channel entries)
    try:
        bootstrap_copilot_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning(
            "copilot_manifest_validation_error",
            error=str(exc),
            outcome="warning",
        )
        result["errors"].append(f"Copilot manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("copilot_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Copilot manifest bootstrap failed: {exc}")

    log.debug(
        "copilot_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
