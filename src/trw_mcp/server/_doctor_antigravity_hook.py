"""``trw-mcp doctor`` row for the AG-03 Antigravity hook location (UF-BOOT-08).

Belongs to the ``_subcommands_doctor.py`` facade.

Earlier TRW versions wrote a PreToolUse hook to ``.antigravitycli/hooks.json`` (flat
``{"PreToolUse": [{"matcher", "command"}]}``).  agy 1.2.14 lists workspace hooks from
``<workspace>/.agents/hooks.json`` in a grouped, named-hook schema and does not list the
legacy file (observed with ``agy -p /hooks --output-format json`` in a scratch workspace,
2026-10-02), so that registration never fires.  The bootstrap no longer writes it; this row
tells an operator who still has one so they can delete it.  Read-only.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["antigravity_hook_row"]

_LEGACY_HOOKS_JSON = ".antigravitycli/hooks.json"
_LEGACY_SCRIPT = ".antigravitycli/hooks/trw_before_edit_telemetry.py"
_MARKER = "trw_before_edit_telemetry"


def _legacy_registration_present(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: read-only probe; unreadable means no hook to report
        return False
    if not isinstance(data, dict):
        return False
    for entries in data.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and _MARKER in str(entry.get("command", "")):
                return True
    return False


def antigravity_hook_row(target: Path) -> tuple[str, str]:
    """Return ``(status, message)``: WARN when a TRW AG-03 hook sits at the path agy ignores."""
    found: list[str] = []
    if _legacy_registration_present(target / _LEGACY_HOOKS_JSON):
        found.append(_LEGACY_HOOKS_JSON)
    if (target / _LEGACY_SCRIPT).is_file():
        found.append(_LEGACY_SCRIPT)
    if not found:
        return "PASS", "no TRW AG-03 hook installed (agy reads hooks from .agents/hooks.json; TRW writes none)."
    return (
        "WARN",
        f"{', '.join(found)} is a TRW AG-03 hook left by an earlier version; agy 1.2.14 reads "
        ".agents/hooks.json (grouped named-hook schema), so it never fires. Remove the TRW hook entry/script (keep any "
        "of your own entries); TRW no longer writes them.",
    )
