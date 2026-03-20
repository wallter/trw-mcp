#!/usr/bin/env python3
"""Build self-contained installer from template + wheels.

Usage:
    python scripts/build_installer.py [--wheel WHEEL] [--memory-wheel WHEEL] [--format py|sh]

Reads the template (Python or bash), finds the latest trw-mcp and
trw-memory wheels in ``dist/``, base64-encodes them, substitutes
placeholders, and writes the installer to ``dist/``.

Build wheels first:
    python -m build --wheel              # trw-mcp
    cd ../trw-memory && python -m build --wheel  # trw-memory
"""

from __future__ import annotations

import argparse
import base64
import re
import stat
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DIST_DIR = PROJECT_ROOT / "dist"

# Template paths
TEMPLATES = {
    "py": (SCRIPT_DIR / "install-trw.template.py", "install-trw.py"),
}
DEFAULT_FORMAT = "py"


def find_latest_wheel(dist_dir: Path, pattern: str, label: str) -> Path:
    """Find the most recent .whl file matching *pattern* in *dist_dir*."""
    wheels = sorted(dist_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not wheels:
        print(f"ERROR: No {pattern} found in {dist_dir}", file=sys.stderr)
        print(f"Run:  python -m build --wheel  (for {label})", file=sys.stderr)
        sys.exit(1)
    return wheels[-1]


def extract_version(wheel_path: Path) -> str:
    """Extract version from wheel filename (PEP 427 format)."""
    match = re.match(r"trw_mcp-([^-]+)-", wheel_path.name)
    if not match:
        print(f"ERROR: Cannot extract version from {wheel_path.name}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def _format_b64_for_python(b64: str, line_width: int = 76) -> str:
    """Wrap base64 string into comment-prefixed lines for Python embedding."""
    return "\n".join(f"# {b64[i:i+line_width]}" for i in range(0, len(b64), line_width))


def build_installer(
    wheel_path: Path | None = None,
    memory_wheel_path: Path | None = None,
    shared_wheel_path: Path | None = None,
    fmt: str = DEFAULT_FORMAT,
) -> Path:
    """Build the self-contained installer script.

    Args:
        wheel_path: Path to trw-mcp wheel. Auto-finds latest if None.
        memory_wheel_path: Path to trw-memory wheel. Auto-finds latest if None.
        shared_wheel_path: Path to trw-shared wheel. Auto-finds latest if None.
        fmt: Output format — "py" (Python) or "sh" (bash).

    Returns:
        Path to the generated installer.
    """
    template_path, output_name = TEMPLATES[fmt]

    if not template_path.exists():
        print(f"ERROR: Template not found: {template_path}", file=sys.stderr)
        sys.exit(1)
    template = template_path.read_text(encoding="utf-8")

    # Find wheels
    if wheel_path is None:
        wheel_path = find_latest_wheel(DIST_DIR, "trw_mcp-*.whl", "trw-mcp")
    elif not wheel_path.exists():
        print(f"ERROR: Wheel not found: {wheel_path}", file=sys.stderr)
        sys.exit(1)

    version = extract_version(wheel_path)
    print(f"Format:             {fmt}")
    print(f"Wheel (trw-mcp):    {wheel_path}")
    print(f"Version:            {version}")

    if memory_wheel_path is None:
        memory_wheel_path = find_latest_wheel(DIST_DIR, "trw_memory-*.whl", "trw-memory")
    elif not memory_wheel_path.exists():
        print(f"ERROR: Memory wheel not found: {memory_wheel_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Wheel (trw-memory): {memory_wheel_path}")

    if shared_wheel_path is None:
        shared_wheel_path = find_latest_wheel(DIST_DIR, "trw_shared-*.whl", "trw-shared")
    elif not shared_wheel_path.exists():
        print(f"ERROR: Shared wheel not found: {shared_wheel_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Wheel (trw-shared): {shared_wheel_path}")

    # Base64-encode wheels
    wheel_b64 = base64.b64encode(wheel_path.read_bytes()).decode("ascii")
    memory_b64 = base64.b64encode(memory_wheel_path.read_bytes()).decode("ascii")
    shared_b64 = base64.b64encode(shared_wheel_path.read_bytes()).decode("ascii")

    wheel_mb = len(wheel_path.read_bytes()) / (1024 * 1024)
    mem_mb = len(memory_wheel_path.read_bytes()) / (1024 * 1024)
    shared_kb = len(shared_wheel_path.read_bytes()) / 1024
    print(f"Size (trw-mcp):     {wheel_mb:.1f} MB")
    print(f"Size (trw-memory):  {mem_mb:.1f} MB")
    print(f"Size (trw-shared):  {shared_kb:.1f} KB")

    # Substitute placeholders
    output = template.replace("{{VERSION}}", version)
    output = output.replace("{{WHEEL_FILENAME}}", wheel_path.name)
    output = output.replace("{{MEMORY_WHEEL_FILENAME}}", memory_wheel_path.name)
    output = output.replace("{{SHARED_WHEEL_FILENAME}}", shared_wheel_path.name)

    # Python template: wheel data is in comment-prefixed lines
    output = output.replace("# {{SHARED_WHEEL_BASE64}}", _format_b64_for_python(shared_b64))
    output = output.replace("# {{MEMORY_WHEEL_BASE64}}", _format_b64_for_python(memory_b64))
    output = output.replace("# {{WHEEL_BASE64}}", _format_b64_for_python(wheel_b64))

    # Write output
    DIST_DIR.mkdir(exist_ok=True)
    output_path = DIST_DIR / output_name
    output_path.write_text(output, encoding="utf-8")

    # Make executable
    current = output_path.stat().st_mode
    output_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Output:             {output_path} ({size_mb:.1f} MB)")

    # Validate round-trip: verify embedded wheels can be extracted
    if fmt == "py":
        _validate_py_installer(output_path, wheel_path, memory_wheel_path, shared_wheel_path)

    return output_path


def _validate_py_installer(
    installer_path: Path,
    expected_mcp_wheel: Path,
    expected_memory_wheel: Path,
    expected_shared_wheel: Path,
) -> None:
    """Verify the built Python installer can extract valid wheels."""
    text = installer_path.read_text(encoding="utf-8", errors="replace")

    for marker, expected_wheel, label in [
        ("__SHARED_WHEEL_DATA__", expected_shared_wheel, "trw-shared"),
        ("__MEMORY_WHEEL_DATA__", expected_memory_wheel, "trw-memory"),
        ("__WHEEL_DATA__", expected_mcp_wheel, "trw-mcp"),
    ]:
        collecting = False
        chunks: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == f"# {marker}":
                collecting = True
                continue
            if collecting:
                if stripped.startswith("# __") and stripped.endswith("__"):
                    break
                if stripped.startswith("# "):
                    chunks.append(stripped[2:])
                elif stripped == "#":
                    continue
                else:
                    break

        if not chunks:
            print(f"ERROR: No base64 data found for {marker}", file=sys.stderr)
            sys.exit(1)

        joined = "".join(chunks)
        try:
            decoded = base64.b64decode(joined)
        except Exception as exc:
            print(f"ERROR: base64 decode failed for {label} ({marker}): {exc}", file=sys.stderr)
            sys.exit(1)

        expected_size = expected_wheel.stat().st_size
        if len(decoded) != expected_size:
            print(
                f"ERROR: {label} size mismatch: decoded {len(decoded)} vs expected {expected_size}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"Verified:           {label} wheel round-trip OK ({len(decoded)} bytes)")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build self-contained TRW installer from template + wheels",
    )
    parser.add_argument(
        "--wheel", type=Path, default=None,
        help="Path to trw-mcp .whl file (default: latest in dist/)",
    )
    parser.add_argument(
        "--memory-wheel", type=Path, default=None,
        help="Path to trw-memory .whl file (default: latest in dist/)",
    )
    parser.add_argument(
        "--shared-wheel", type=Path, default=None,
        help="Path to trw-shared .whl file (default: latest in dist/)",
    )
    parser.add_argument(
        "--format", choices=["py"], default=DEFAULT_FORMAT,
        help="Output format (default: py)",
    )
    args = parser.parse_args()

    output = build_installer(args.wheel, args.memory_wheel, args.shared_wheel, args.format)
    print(f"\nDone! Distribute: {output}")


if __name__ == "__main__":
    main()
