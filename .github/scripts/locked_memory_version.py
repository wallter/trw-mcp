#!/usr/bin/env python3
"""Print the trw-memory version this tree's ``uv.lock`` names, or refuse.

The publish workflow builds and smoke-tests trw-mcp against trw-memory. It used to install
``trw-memory @ git+https://github.com/wallter/trw-memory.git@main``, whatever ``main`` held at that
moment, so the wheel that went to PyPI was built and tested against code nobody had locked or
released. The workflow now installs ``trw-memory==<what this prints>``: the released version the
committed lock names. The release tool loads this same file to refuse a cut whose tree would
install anything else, so the check and the install cannot drift.

    python3 .github/scripts/locked_memory_version.py [path/to/uv.lock]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import tomllib

PACKAGE = "trw-memory"
#: The one index whose ``trw-memory`` is the released one; a lock naming any other registry is refused.
REGISTRY = "https://pypi.org/simple"
#: A plain release: no local label (``+...``), so it names something PyPI can serve.
_RELEASE = re.compile(r"^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?$")


class LockError(ValueError):
    """The lock does not name one released trw-memory."""


def locked_version(lock_text: str) -> str:
    """The version of the one PyPI ``trw-memory`` entry in *lock_text*, or ``LockError``."""
    try:
        lock = tomllib.loads(lock_text)
    except tomllib.TOMLDecodeError as exc:
        raise LockError(f"uv.lock is not valid TOML: {exc}") from exc
    packages = lock.get("package", [])
    if not isinstance(packages, list):
        raise LockError("uv.lock's package table is not a list")
    entries = [p for p in packages if isinstance(p, dict) and p.get("name") == PACKAGE]
    if len(entries) != 1:
        raise LockError(f"uv.lock names {PACKAGE} {len(entries)} times, not once")
    entry = entries[0]
    source = entry.get("source")
    if not isinstance(source, dict) or source.get("registry") != REGISTRY:
        raise LockError(f"uv.lock's {PACKAGE} does not come from {REGISTRY} (source: {source!r})")
    version = entry.get("version")
    if not isinstance(version, str) or not _RELEASE.match(version):
        raise LockError(f"uv.lock's {PACKAGE} version {version!r} is not a released version")
    return version


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "uv.lock")
    try:
        print(locked_version(path.read_text(encoding="utf-8")))
    except (OSError, LockError) as exc:
        print(f"::error::cannot pin {PACKAGE}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
