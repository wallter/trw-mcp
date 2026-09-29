"""Doctor row for PRD-INFRA-200 FR05's retired-artifact notice.

Belongs to the ``_subcommands_doctor.py`` facade. The single source of truth
for which artifact is retired, and for the row text, is
``trw_mcp.bootstrap._retired_artifacts`` -- this module wraps it into a
``CheckResult`` so ``_subcommands_doctor.py``'s ``_CHECKS`` registry can
import the check function directly (rather than defining a same-shaped
wrapper inline, which would push that already-350-eLOC file over its gate).
``CheckResult`` is imported lazily, inside the function body, to avoid a
module-level import cycle with the facade this module belongs to.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from trw_mcp.bootstrap._retired_artifacts import retired_artifact_row as retired_artifact_row
from trw_mcp.models.config import TRWConfig

if TYPE_CHECKING:
    from trw_mcp.server._subcommands_doctor import CheckResult


def check_retired_artifacts(target: Path, _config: TRWConfig) -> CheckResult:
    """PRD-INFRA-200 FR05: WARN naming a retired dead file this run never deletes."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("retired_artifacts", *retired_artifact_row(target))


__all__ = ["check_retired_artifacts", "retired_artifact_row"]
