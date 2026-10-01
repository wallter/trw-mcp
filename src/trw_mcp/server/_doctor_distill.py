"""The ``distill`` row of ``trw-mcp doctor``: is trw-distill installed here, and if not, why that's fine.

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling for the eLOC gate.
trw-distill is proprietary and optional (AGENTS.md IP boundary) — a project without it is a
fully-functioning free tier, so this row never FAILs, mirroring the ``jev`` row's
SKIP/WARN/PASS-only contract for the same reason: an operator's absence-of-a-tier choice
is not a doctor defect. It exists so the two ways trw-distill can be "half-there" — the
package importable but its console script missing from PATH (the case
``_post_commit_distill.py`` and the platform's distill worker now degrade truthfully around,
2026-09-26 audit) — are surfaced to an operator instead of only ever showing up as a silent
skipped git-hook step.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import shutil
from pathlib import Path
from typing import Literal

from trw_mcp.models.config import TRWConfig

__all__ = ["distill_row"]

#: Where an operator applies for the proprietary tools; the TRW repo's own docs are not in an installed project.
_PROPRIETARY_ACCESS_URL = "https://trwframework.com/waitlist"


def _installed_version() -> str:
    try:
        return importlib.metadata.version("trw-distill")
    except importlib.metadata.PackageNotFoundError:  # trw-fail-silent-allow: "unknown" is honest here
        return "unknown"


def distill_row(_target: Path, _config: TRWConfig) -> tuple[Literal["PASS", "WARN", "SKIP"], str]:
    """SKIP when absent (the free-tier default); WARN when importable but its console
    script is not on PATH; PASS when both resolve.
    """
    if importlib.util.find_spec("trw_distill") is None:
        return (
            "SKIP",
            f"trw-distill not installed (optional, proprietary tier). Apply for access at {_PROPRIETARY_ACCESS_URL}.",
        )
    version = _installed_version()
    if shutil.which("trw-distill") is None:
        return (
            "WARN",
            f"trw-distill {version} is importable but its console script is not on PATH — "
            "a post-commit hook or worker subprocess invocation of `trw-distill` will "
            "degrade to its free-tier fallback. Activate the venv that installed it, or add "
            "its bin/ directory to PATH.",
        )
    return "PASS", f"trw-distill {version} installed, console script on PATH."
