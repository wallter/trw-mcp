"""Shared additive merge for client distill-channel manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from trw_mcp.channels._manifest_loader import ManifestValidationError, auto_recreate_empty, load, write
from trw_mcp.channels._manifest_models import ChannelEntry
from trw_mcp.channels._provenance import now_utc_iso8601


def merge_distill_channel_manifest(repo_root: Path, manifest_data: Path, client_label: str) -> tuple[int, int]:
    """Validate and add one client's bundled entries to the target manifest."""
    raw: Any = YAML(typ="safe").load(manifest_data.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("channels", []), list):
        raise ManifestValidationError(f"{client_label} manifest entry validation failed: channels must be a list")

    validated: list[ChannelEntry] = []
    for entry_dict in raw.get("channels", []):
        try:
            validated.append(ChannelEntry.model_validate(entry_dict))
        except Exception as exc:
            raise ManifestValidationError(f"{client_label} manifest entry validation failed: {exc}") from exc

    manifest_path = repo_root / ".trw" / "channels" / "manifest.yaml"
    try:
        manifest = load(manifest_path)
    except Exception:
        auto_recreate_empty(manifest_path)
        manifest = load(manifest_path)

    existing_ids = {entry.id for entry in manifest.channels}
    added = 0
    for entry in validated:
        if entry.id in existing_ids:
            continue
        manifest.channels.append(entry)
        existing_ids.add(entry.id)
        added += 1

    manifest.generated_at = now_utc_iso8601()
    write(manifest, manifest_path)
    return added, len(manifest.channels)


__all__ = ["merge_distill_channel_manifest"]
