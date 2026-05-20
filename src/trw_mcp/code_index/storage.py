"""Atomic JSON storage for the local code-index manifest."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import ValidationError

from trw_mcp.code_index.models import CodeIndexManifest

MANIFEST_RELATIVE_PATH: str = ".trw/code-index/manifest.json"


def default_manifest_path(repo_root: Path | str) -> Path:
    """Return the canonical manifest path for ``repo_root``."""

    return Path(repo_root) / MANIFEST_RELATIVE_PATH


def load_manifest(path: Path) -> CodeIndexManifest | None:
    """Load a manifest, returning ``None`` for missing or corrupt state."""

    if not path.exists():
        return None
    try:
        return CodeIndexManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def save_manifest(path: Path, manifest: CodeIndexManifest) -> None:
    """Persist ``manifest`` via temp file plus atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(f"{manifest.model_dump_json(indent=2)}\n", encoding="utf-8")
    try:
        os.replace(temp_path, path)
    except OSError:
        if temp_path.exists():
            temp_path.unlink()
        raise


__all__ = [
    "MANIFEST_RELATIVE_PATH",
    "default_manifest_path",
    "load_manifest",
    "save_manifest",
]
