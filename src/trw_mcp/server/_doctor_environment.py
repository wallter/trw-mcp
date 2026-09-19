"""Environment-parity doctor rows (PRD-INFRA-189 FR02, FR05).

Belongs to the ``_subcommands_doctor.py`` facade (kept out of that file for the
eLOC gate). Both rows are facts a user project hits, not facts about the
monorepo's release machine; those live in ``scripts/release-preflight.sh``.

Each row is a pure function over injected facts (a ``which`` callable, the
checkout root, the home directory) and reads files only.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

__all__ = ["foreign_client_paths_row", "gnu_timeout_row"]

Row = tuple[Literal["PASS", "WARN", "SKIP"], str]

# Another machine's home directory is the failure mode (a cloned .codex/hooks.json
# carrying /home/<someone-else>/...). System paths such as /usr/bin/env are fine.
_HOME_PATH_RE = re.compile(r"/(?:home|Users)/[^/\s\"'`]+(?:/[^\s\"'`,;)]*)?")
_CLIENT_DIRS = (".codex", ".cursor")
_MAX_SCAN_BYTES = 1_000_000


def gnu_timeout_row(which: Callable[[str], str | None] = shutil.which) -> Row:
    """FR02: report whether ``timeout``/``gtimeout`` is on PATH (absence is informational)."""
    for name in ("timeout", "gtimeout"):
        found = which(name)
        if found:
            return "PASS", f"{name} on PATH ({found}); hook deadlines use it."
    return (
        "PASS",
        "INFO: no GNU timeout or gtimeout on PATH. Hooks that need a deadline use the portable "
        "watchdog in _trw_bounded_python; hooks that call timeout only when present run without an "
        "outer bound. Optional remedy: brew install coreutils (provides gtimeout).",
    )


def _foreign_paths(text: str, allowed: tuple[str, ...]) -> list[str]:
    return sorted({m for m in _HOME_PATH_RE.findall(text) if not m.startswith(allowed)})


def foreign_client_paths_row(target: Path, home: Path | None = None) -> Row:
    """FR05: flag generated client files under .codex/ or .cursor/ that name another machine's home."""
    dirs = [target / name for name in _CLIENT_DIRS if (target / name).is_dir()]
    if not dirs:
        return "SKIP", "no .codex/ or .cursor/ directory; nothing to scan."
    allowed = (str(target.resolve()), str((home or Path.home()).resolve()))
    findings: list[str] = []
    for root in dirs:
        for path in sorted(root.rglob("*")):
            try:
                if not path.is_file() or path.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                foreign = _foreign_paths(path.read_text(encoding="utf-8", errors="ignore"), allowed)
            except OSError:
                findings.append(f"{path.relative_to(target)} (unreadable)")
                continue
            if foreign:
                findings.append(f"{path.relative_to(target)} ({', '.join(foreign[:2])})")
    if not findings:
        return "PASS", f"generated client files under {', '.join(d.name for d in dirs)} carry no foreign home path."
    return (
        "WARN",
        f"{len(findings)} generated client file(s) carry an absolute path from another machine: "
        f"{'; '.join(findings[:5])}. Remedy: trw-mcp update-project.",
    )
