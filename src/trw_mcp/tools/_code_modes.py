"""``trw_code`` mode registry: the live modes and the retired ones, in one place.

Dependency-free on purpose. ``scripts/lint-instruction-surfaces.py`` loads this file
by path to fail any shipped instruction surface that still tells an agent to call a
retired mode, so the lint and the tool that refuses the call read one list. Add a
retirement here and both change; never copy these values into a second list.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: Modes ``trw_code`` serves.
LIVE_MODES: tuple[str, ...] = ("symbol", "hint")

#: Retired modes, each mapped to the actionable error its call now returns. The
#: replacement it names is also what a surface that mentioned the mode should say.
RETIRED_MODES: Mapping[str, str] = MappingProxyType(
    {
        "search": (
            "trw_code search was retired in 8.0; use `rg`/`grep` for text search, or `trw-distill` CLI verbs "
            "for codebase intelligence."
        ),
    }
)
