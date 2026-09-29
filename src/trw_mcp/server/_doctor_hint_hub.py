"""``hint_hub_downrank`` doctor row (ANCHOR-HUB-DOWNRANK).

Belongs to the ``_subcommands_doctor.py`` catalogue (registered in
``_doctor_checks_registry.py``). Reports whether the pre-edit hint moves
anchor-only lessons after text-matched ones on hub files, the threshold in
force, and the config line that switches it the other way. Never WARN or FAIL:
both settings are valid; the row exists so an operator can see which order the
hint uses without reading config.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from trw_mcp.models.config import TRWConfig

if TYPE_CHECKING:
    from trw_mcp.server._subcommands_doctor import CheckResult

__all__ = ["check_hint_hub", "hint_hub_row"]


def hint_hub_row(config: TRWConfig) -> tuple[Literal["PASS", "SKIP"], str]:
    """PASS naming the threshold and the off-switch when on; SKIP naming the on-switch when off."""
    if not config.hint_hub_downrank:
        return "SKIP", (
            "pre-edit hint hub down-rank off: lessons anchored to a file always lead its hint "
            "(enable with hint_hub_downrank: true in .trw/config.yaml)"
        )
    return "PASS", (
        f"pre-edit hint hub down-rank on: on a file with more than {config.hint_hub_threshold} anchored "
        "lessons (hint_hub_threshold), text-matched lessons lead and anchor-only lessons follow "
        "(disable with hint_hub_downrank: false in .trw/config.yaml)"
    )


def check_hint_hub(_target: Path, config: TRWConfig) -> CheckResult:
    """Doctor-registry entry point (imported by name into _subcommands_doctor.py's globals)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("hint_hub_downrank", *hint_hub_row(config))
