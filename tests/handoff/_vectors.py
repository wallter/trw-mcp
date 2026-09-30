"""Shared helpers for the vendored AHR 1.0-rc.1 vectors (PRD-CORE-347).

The vectors are copied unchanged from the AHR reference spec's handoff/vectors/ directory
(valid/ and invalid/; lifecycle/ belongs to PRD-CORE-349). The handoff-selection rule mirrors
the reference runner specs/handoff/tools/run_vectors.sh.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

VECTORS = Path(__file__).parent / "vectors"
VALID = sorted((VECTORS / "valid").glob("*.json"))
INVALID = sorted((VECTORS / "invalid").glob("*.json"))
STANDARD = VECTORS / "valid" / "01-standard-handoff.json"
CRITICAL = VECTORS / "valid" / "04-critical-supersedes-01.json"

_NEEDS = (
    "*readback*",
    "*x12-placeholder-reverify*",
    "*x11-pointer-index*",
    "*x12-invisible-constraint*",
    "*x07*",
    "*x08*",
    "*x10*",
    "*x11*",
    "*x13*",
    "*x14*",
    "*x18*",
    "*i15*",
    "*i16*",
)
_CRIT = (
    "*critical*",
    "*x12-placeholder-reverify*",
    "*07-readback-for-04*",
    "*10-critical-questions*",
    "*x12-invisible-constraint*",
    "*x09*",
    "*x18*",
    "*x11-match-with-diff*",
    "*x11-critical-drift*",
    "*x11-critical-pointer-missing*",
    "*x14-critical*",
)


def handoff_for(path: Path) -> Path | None:
    name = str(path)
    if not any(fnmatch(name, p) for p in _NEEDS):
        return None
    return CRITICAL if any(fnmatch(name, p) for p in _CRIT) else STANDARD


def expected_rule(path: Path) -> str:
    name = path.name
    prefix = {"i": "schema", "p": "R-INT-4", "r": "R-TIME-1"}.get(name[0])
    if prefix:
        return prefix
    match = re.match(r"^x0?(\d+)", name)
    assert match, name
    return f"X-{match.group(1)}"
