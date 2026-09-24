"""A formation member's own pin record (shared by the FR16 upgrade and the FR13 hint).

A member in a linked worktree keeps its pins in ITS store, not the main root's, so
the record is found by walking up from the member's run to the nearest pin store.
None whenever it cannot be read: every caller treats that as "cannot prove", never
as permission.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def member_pin_store(run_path: str) -> dict[str, dict[str, Any]] | None:
    """Every entry of the pin store nearest *run_path*; None when unreadable or absent."""
    for ancestor in Path(run_path).parents:
        pins = ancestor / "runtime" / "pins.json"
        if pins.is_file():
            try:
                raw = json.loads(pins.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # trw-fail-silent-allow: an unreadable pin store proves nothing; callers fail closed on None
                return None
            return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else None
    return None


def member_pin_entry(run_path: str, pin_key: str) -> dict[str, Any] | None:
    for ancestor in Path(run_path).parents:
        pins = ancestor / "runtime" / "pins.json"
        if pins.is_file():
            try:
                entry = json.loads(pins.read_text(encoding="utf-8")).get(pin_key)
            except (OSError, ValueError, AttributeError):
                # trw-fail-silent-allow: an unreadable pin store proves nothing; callers fail closed on None
                return None
            return entry if isinstance(entry, dict) else None
    return None


__all__ = ["member_pin_entry", "member_pin_store"]
