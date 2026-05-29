"""opencode distill channel bootstrap — single entry-point.

Installs all six opencode distill channel artifacts at ``init-project`` and
``update-project`` time.  Called as a facade from ``bootstrap/_opencode.py``.

Install ordering (FR26):
  1. ``generate_opencode_config()``          (existing — called before this)
  2. ``generate_agents_md()``               (existing — modified to acquire agents-md.lock)
  3. ``install_opencode_distill_channels()`` (this module — all distill artifacts)

Artifacts written:
  - AGENTS.md distill segment (between trw:distill:start / trw:distill:end markers)
  - .opencode/commands/trw-before-edit.md
  - .opencode/commands/trw-distill-hotspots.md
  - .opencode/commands/trw-distill-conventions.md
  - .opencode/agents/trw-distill-explorer.md
  - .trw/managed-artifacts.yaml hash entries for the four new files
  - .trw/client-profile.env (TRW_CLIENT_PROFILE=opencode)
  - .trw/channels/manifest.yaml merged with six opencode ChannelEntry records
  - .gitignore entries for channel-events.jsonl and client-profile.env

PRD-DIST-2403 FR25-FR30.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

from trw_mcp.channels._gitignore import add_gitignore_entry
from trw_mcp.channels._manifest_loader import (
    ManifestValidationError,
    auto_recreate_empty,
    load,
    write,
)
from trw_mcp.channels._manifest_models import ChannelEntry
from trw_mcp.channels._provenance import now_utc_iso8601
from trw_mcp.channels.opencode._agents_md_segment import (
    install_opencode_agents_md_distill_segment,
)
from trw_mcp.channels.opencode._custom_commands import install_custom_commands
from trw_mcp.channels.opencode._explorer_agent import install_explorer_agent

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_channel_manifest",
    "install_opencode_distill_channels",
]

# Gitignore entries (FR28)
_GITIGNORE_ENTRIES = [
    ".trw/telemetry/channel-events.jsonl",
    ".trw/client-profile.env",
]

# Managed artifacts registry file
_MANAGED_ARTIFACTS_PATH = ".trw/managed-artifacts.yaml"

# Client profile env file (FR19)
_CLIENT_PROFILE_ENV_PATH = ".trw/client-profile.env"
_CLIENT_PROFILE_ENV_CONTENT = "TRW_CLIENT_PROFILE=opencode\n"


# ---------------------------------------------------------------------------
# Managed-artifacts helper
# ---------------------------------------------------------------------------


def _load_managed_artifacts(repo_root: Path) -> dict[str, Any]:
    """Load .trw/managed-artifacts.yaml, returning empty dict if absent."""
    path = repo_root / _MANAGED_ARTIFACTS_PATH
    if not path.exists():
        return {}
    try:
        yaml = YAML(typ="safe")
        result: dict[str, Any] = yaml.load(path.read_text(encoding="utf-8")) or {}
        return result
    except Exception:
        return {}


def _save_managed_artifacts(repo_root: Path, data: dict[str, Any]) -> None:
    """Write .trw/managed-artifacts.yaml."""
    path = repo_root / _MANAGED_ARTIFACTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    with open(path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Load manifest-opencode.yaml and merge six ChannelEntry records.

    Merge is additive — existing entries for other clients are preserved (FR30).
    All-or-nothing: if any entry fails validation, raises ManifestValidationError
    and no partial state is written (FR29).

    Args:
        repo_root: Repository root directory.

    Returns:
        Dict with ``status`` and ``count`` of entries added.

    Raises:
        ManifestValidationError: If any entry fails ChannelEntry.model_validate().
    """
    manifest_data_path = Path(__file__).parent.parent / "data" / "opencode" / "channels" / "manifest-opencode.yaml"
    yaml = YAML(typ="safe")
    raw = yaml.load(manifest_data_path.read_text(encoding="utf-8")) or {}
    raw_channels: list[dict[str, Any]] = raw.get("channels", [])

    # Validate all entries first (FR29 — all-or-nothing)
    validated: list[ChannelEntry] = []
    for entry_dict in raw_channels:
        try:
            validated.append(ChannelEntry.model_validate(entry_dict))
        except Exception as exc:
            raise ManifestValidationError(f"opencode manifest entry validation failed: {exc}") from exc

    # Load existing manifest
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
        "opencode_manifest_bootstrapped",
        added=added,
        total=len(manifest.channels),
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_opencode_distill_channels(
    repo_root: Path,
    sidecar_data: dict[str, Any] | None = None,
    sidecar_sha: str | None = None,
) -> dict[str, object]:
    """Install all six opencode distill channel artifacts.

    This is the single entry-point called from ``bootstrap/_opencode.py``
    as a thin facade.

    Args:
        repo_root: Repository root directory.
        sidecar_data: Optional parsed sidecar payload for T1 render.
        sidecar_sha: Optional sidecar git SHA for TTL tracking.

    Returns:
        Dict with install status for each artifact group.
    """
    results: dict[str, object] = {}

    # 1. Load managed artifacts (for user-edit detection)
    managed = _load_managed_artifacts(repo_root)
    raw_cmds = managed.get("commands")
    cmd_hashes: dict[str, str] = raw_cmds if isinstance(raw_cmds, dict) else {}
    raw_explorer = managed.get("explorer_agent")
    explorer_sha: str | None = str(raw_explorer) if raw_explorer is not None else None

    # 2. AGENTS.md distill segment (acquires shared agents-md.lock internally)
    segment_result = install_opencode_agents_md_distill_segment(
        repo_root,
        sidecar_data,
        sidecar_sha,
    )
    results["agents_md_segment"] = segment_result.status

    # 3. Custom command files
    cmd_results = install_custom_commands(repo_root, existing_hashes=cmd_hashes)
    results["custom_commands"] = {k: v["status"] for k, v in cmd_results.items()}

    # 4. Explorer agent
    explorer_result = install_explorer_agent(repo_root, existing_sha256=explorer_sha)
    results["explorer_agent"] = explorer_result["status"]

    # 5. Update managed-artifacts.yaml with new hashes
    new_cmd_hashes: dict[str, str] = {}
    for filename, res in cmd_results.items():
        new_cmd_hashes[filename] = str(res.get("sha256", ""))
    managed["commands"] = new_cmd_hashes
    if explorer_result.get("sha256"):
        managed["explorer_agent"] = explorer_result["sha256"]
    _save_managed_artifacts(repo_root, managed)

    # 6. Write client-profile.env (FR19)
    env_path = repo_root / _CLIENT_PROFILE_ENV_PATH
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(_CLIENT_PROFILE_ENV_CONTENT, encoding="utf-8")
    results["client_profile_env"] = "written"

    # 7. Bootstrap channel manifest (FR27 / FR30)
    try:
        manifest_result = bootstrap_channel_manifest(repo_root)
        results["manifest"] = manifest_result
    except ManifestValidationError as exc:
        log.debug(
            "opencode_manifest_validation_error",
            error=str(exc),
            outcome="error",
        )
        results["manifest"] = {"status": "error", "error": str(exc)}
        raise

    # 8. Gitignore entries (FR28)
    for entry_str in _GITIGNORE_ENTRIES:
        try:
            add_gitignore_entry(repo_root, entry_str)
        except Exception as exc:
            log.debug(
                "opencode_gitignore_entry_error",
                entry=entry_str,
                error=str(exc),
                outcome="error",
            )
    results["gitignore"] = "updated"

    log.debug(
        "opencode_distill_channels_installed",
        repo_root=str(repo_root),
        outcome="ok",
    )
    return results
