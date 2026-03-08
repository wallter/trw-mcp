"""Release builder — packages bundled data into distributable .tar.gz archives.

Used by the `trw-mcp build-release` CLI subcommand to create release
artifacts that can be uploaded to the backend for auto-upgrade distribution.
"""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"


def build_release_bundle(
    *,
    version: str | None = None,
    output_dir: Path | None = None,
) -> dict[str, object]:
    """Build a release .tar.gz containing the bundled data/ directory.

    Args:
        version: Release version string. Read from pyproject.toml if not provided.
        output_dir: Directory to write the bundle. Defaults to current directory.

    Returns:
        {path: str, version: str, checksum: str, size_bytes: int}
    """
    if version is None:
        version = _read_version()

    out = (output_dir or Path.cwd()).resolve()
    out.mkdir(parents=True, exist_ok=True)

    bundle_name = f"trw-release-{version}.tar.gz"
    bundle_path = out / bundle_name

    # Build tar.gz of data/ directory
    with tarfile.open(bundle_path, "w:gz") as tar:
        tar.add(str(_DATA_DIR), arcname="data")

    # Compute SHA-256
    checksum = _sha256(bundle_path)
    size_bytes = bundle_path.stat().st_size

    return {
        "path": str(bundle_path),
        "version": version,
        "checksum": checksum,
        "size_bytes": size_bytes,
        "framework_version": _read_framework_version(),
    }


def _read_version() -> str:
    """Read version from pyproject.toml."""
    pyproject = Path(__file__).parent.parent.parent.parent / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                # version = "0.3.0"
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    # Fallback
    try:
        from trw_mcp import __version__
        return __version__
    except (ImportError, AttributeError):
        return "0.0.0"


def _read_framework_version() -> str:
    """Read framework version from bundled FRAMEWORK.md first line."""
    fw_path = _DATA_DIR / "framework.md"
    if fw_path.exists():
        first_line = fw_path.read_text(encoding="utf-8").split("\n", 1)[0]
        # "v24.2_TRW — CLAUDE CODE..."
        if "\u2014" in first_line:
            return first_line.split("\u2014")[0].strip().split()[0]
        return first_line.split()[0]
    return "unknown"


def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
