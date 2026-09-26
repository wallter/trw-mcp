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

from trw_mcp.state._checkout_servers import stray_servers

__all__ = ["claude_code_version_row", "foreign_client_paths_row", "gnu_timeout_row", "stray_servers_row"]

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


def stray_servers_row(target: Path) -> Row:
    """sprint-mcp7 W12: trw-mcp servers still holding this checkout that no client will use again."""
    lines = stray_servers(target / ".trw")
    if not lines:
        return "PASS", "no stray trw-mcp server recorded for this checkout."
    return "WARN", f"{len(lines)} stray trw-mcp server(s) hold this checkout's store: {'; '.join(lines[:5])}"


#: PRD-CORE-289 FR07: Claude Code 2.1.280 is the first release that runs Claude Opus 5.5,
#: its default model since 2026-09-22.
_CLAUDE_FLOOR = (2, 1, 280)
_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def claude_code_version_row(timeout_s: int) -> Row:
    """WARN below the Claude Code floor, and whenever the version cannot be read; never PASS on a guess."""
    from trw_mcp.dispatch._client_specs import client_spec_for
    from trw_mcp.server._doctor_formation_readiness import probe_version

    if shutil.which("claude") is None:
        return "SKIP", "no claude binary on PATH."
    floor = ".".join(map(str, _CLAUDE_FLOOR))
    version, failure = probe_version("claude", client_spec_for("claude"), timeout_s)
    match = _SEMVER.search(version or "")
    if match is None:
        return "WARN", f"could not read the Claude Code version ({failure or repr(version)}); Opus 5.5 needs {floor}+."
    found = tuple(int(part) for part in match.groups())
    if found < _CLAUDE_FLOOR:
        installed = ".".join(map(str, found))
        return (
            "WARN",
            f"Claude Code {installed} is older than {floor}, the first release that runs Opus 5.5; upgrade it.",
        )
    return "PASS", f"Claude Code {'.'.join(map(str, found))} runs Opus 5.5 (floor {floor})."
