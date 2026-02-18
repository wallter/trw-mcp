#!/usr/bin/env python3
"""Build self-contained install-trw.sh from template + wheel.

Usage:
    python scripts/build_installer.py [--wheel WHEEL_PATH]

Reads ``scripts/install-trw.template.sh``, finds the latest wheel in
``dist/``, base64-encodes it, substitutes placeholders, and writes
``dist/install-trw.sh``.

Build a wheel first:
    python -m build --wheel
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import stat
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TEMPLATE_PATH = SCRIPT_DIR / "install-trw.template.sh"
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_NAME = "install-trw.sh"


def find_latest_wheel(dist_dir: Path) -> Path:
    """Find the most recent .whl file in *dist_dir*."""
    wheels = sorted(dist_dir.glob("trw_mcp-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        print(f"ERROR: No trw_mcp-*.whl found in {dist_dir}", file=sys.stderr)
        print("Run:  python -m build --wheel", file=sys.stderr)
        sys.exit(1)
    return wheels[-1]


def extract_version(wheel_path: Path) -> str:
    """Extract version from wheel filename (PEP 427 format)."""
    # trw_mcp-0.2.0-py3-none-any.whl -> 0.2.0
    match = re.match(r"trw_mcp-([^-]+)-", wheel_path.name)
    if not match:
        print(f"ERROR: Cannot extract version from {wheel_path.name}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def build_installer(wheel_path: Path | None = None) -> Path:
    """Build the self-contained installer script.

    Returns:
        Path to the generated installer.
    """
    # 1. Read template
    if not TEMPLATE_PATH.exists():
        print(f"ERROR: Template not found: {TEMPLATE_PATH}", file=sys.stderr)
        sys.exit(1)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    # 2. Find wheel
    if wheel_path is None:
        wheel_path = find_latest_wheel(DIST_DIR)
    elif not wheel_path.exists():
        print(f"ERROR: Wheel not found: {wheel_path}", file=sys.stderr)
        sys.exit(1)

    version = extract_version(wheel_path)
    wheel_filename = wheel_path.name

    print(f"Wheel:   {wheel_path}")
    print(f"Version: {version}")

    # 3. Base64-encode the wheel
    wheel_bytes = wheel_path.read_bytes()
    wheel_b64 = base64.b64encode(wheel_bytes).decode("ascii")

    wheel_size_mb = len(wheel_bytes) / (1024 * 1024)
    print(f"Size:    {wheel_size_mb:.1f} MB ({len(wheel_b64)} base64 chars)")

    # 4. Substitute placeholders
    output = template.replace("{{VERSION}}", version)
    output = output.replace("{{WHEEL_FILENAME}}", wheel_filename)
    output = output.replace("{{WHEEL_BASE64}}", wheel_b64)

    # 5. Write output
    DIST_DIR.mkdir(exist_ok=True)
    output_path = DIST_DIR / OUTPUT_NAME
    output_path.write_text(output, encoding="utf-8")

    # Make executable
    current = output_path.stat().st_mode
    output_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    installer_size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Output:  {output_path} ({installer_size_mb:.1f} MB)")
    return output_path


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build self-contained TRW installer from template + wheel"
    )
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="Path to .whl file (default: latest in dist/)",
    )
    args = parser.parse_args()

    output = build_installer(args.wheel)
    print(f"\nDone! Distribute: {output}")


if __name__ == "__main__":
    main()
