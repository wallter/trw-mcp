"""Shared additive merge for client distill-channel manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from trw_mcp.channels._manifest_loader import (
    ManifestMissingError,
    ManifestValidationError,
    auto_recreate_empty,
    load,
    migrate_retired_entry_keys,
    write,
)
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
    except ManifestMissingError:
        # Normal on first init-project: nothing was recovered, so no WARNING
        # and no manifest_recovered telemetry event (see auto_recreate_empty).
        auto_recreate_empty(manifest_path, reason="missing")
        manifest = load(manifest_path)
    except ManifestValidationError as exc:
        # A pre-6.0.0 manifest fails only on the retired tier keys: drop them in place (every other byte kept)
        # and carry on. Anything else is never replaced: it may hold entries no bundle can restore, so the
        # file stays untouched and every caller reports the error (RC-014 review P0).
        try:
            dropped = migrate_retired_entry_keys(manifest_path)
        except ManifestValidationError:
            dropped = 0
        if not dropped:
            raise ManifestValidationError(
                f"{manifest_path} is invalid and was left unchanged ({exc}). Fix the entries it names, or remove"
                " the file and run `trw-mcp update-project` to regenerate it."
            ) from exc
        manifest = load(manifest_path)

    existing_ids = {entry.id for entry in manifest.channels}
    added = 0
    for entry in validated:
        if entry.id in existing_ids:
            continue
        manifest.channels.append(entry)
        existing_ids.add(entry.id)
        added += 1

    # Rewriting an unchanged manifest would only move ``generated_at``: a no-op
    # update must change nothing (PRD-INFRA-190 FR03).
    if added:
        manifest.generated_at = now_utc_iso8601()
        write(manifest, manifest_path)
    return added, len(manifest.channels)


__all__ = ["merge_distill_channel_manifest"]
