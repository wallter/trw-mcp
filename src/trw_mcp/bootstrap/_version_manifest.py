"""Manifest read + content-hash helpers — extracted from _version_migration.py.

Belongs to the ``_version_migration.py`` facade. Re-exported there for
backward compatibility with callers that import via the parent (``_update_project.py``,
test modules, ``bootstrap/__init__.py``).

Self-contained:
- ``_MANIFEST_FILE`` — name of the managed-artifacts manifest in .trw/
- ``_coerce_manifest_list`` — coerce a manifest field to list[str]
- ``_read_manifest`` — load + normalize the manifest YAML
- ``_compute_content_hashes`` — SHA256 of installed artifact files
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_MANIFEST_FILE = "managed-artifacts.yaml"


def _coerce_manifest_list(value: object) -> list[str]:
    """Coerce a manifest field to ``list[str]``, returning ``[]`` for non-lists."""
    return [str(item) for item in value] if isinstance(value, list) else []


def _read_manifest(target_dir: Path) -> dict[str, object] | None:
    """Read the managed-artifacts manifest from a target project.

    Returns ``None`` if the manifest does not exist (first update after
    manifest support was added).
    """
    manifest_path = target_dir / ".trw" / _MANIFEST_FILE
    if not manifest_path.exists():
        return None
    try:
        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        data = reader.read_yaml(manifest_path)
        if not isinstance(data, dict):
            return None
        result: dict[str, object] = {
            key: _coerce_manifest_list(data.get(key, []))
            for key in (
                "skills",
                "agents",
                "hooks",
                "opencode_commands",
                "opencode_agents",
                "opencode_skills",
                "custom_skills",
                "custom_agents",
                "custom_hooks",
                "custom_opencode_commands",
                "custom_opencode_agents",
                "custom_opencode_skills",
            )
        }
        raw_version = data.get("version", 1)
        result["version"] = int(str(raw_version))
        raw_hashes = data.get("content_hashes")
        if isinstance(raw_hashes, dict):
            result["content_hashes"] = {str(k): str(v) for k, v in raw_hashes.items()}
        else:
            result["content_hashes"] = {}
        return result
    except OSError:
        return None


def _compute_content_hashes(
    target_dir: Path,
    bundled: dict[str, list[str]],
) -> dict[str, str]:
    """Compute SHA256 hashes of installed artifact files.

    PRD-FIX-068-FR04: Hashes enable drift detection between installed
    copies and the current bundle.
    """
    hashes: dict[str, str] = {}

    def _record_hash(path: Path, key: str) -> None:
        try:
            if path.is_file():
                hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            logger.warning("content_hash_failed", path=str(path))

    for name in bundled["agents"]:
        _record_hash(target_dir / ".claude" / "agents" / name, name)
    for name in bundled["hooks"]:
        _record_hash(target_dir / ".claude" / "hooks" / name, name)
    for name in bundled["skills"]:
        _record_hash(target_dir / ".claude" / "skills" / name / "SKILL.md", f"{name}/SKILL.md")
    for name in bundled.get("opencode_commands", []):
        _record_hash(target_dir / ".opencode" / "commands" / name, f".opencode/commands/{name}")
    for name in bundled.get("opencode_agents", []):
        _record_hash(target_dir / ".opencode" / "agents" / name, f".opencode/agents/{name}")
    for name in bundled.get("opencode_skills", []):
        _record_hash(target_dir / ".opencode" / "skills" / name / "SKILL.md", f".opencode/skills/{name}/SKILL.md")
    _record_hash(target_dir / ".opencode" / "INSTRUCTIONS.md", ".opencode/INSTRUCTIONS.md")
    _record_hash(target_dir / ".codex" / "INSTRUCTIONS.md", ".codex/INSTRUCTIONS.md")
    _record_hash(target_dir / "AGENTS.md", "AGENTS.md")
    return hashes
