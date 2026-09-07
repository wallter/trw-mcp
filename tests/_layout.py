"""Where this test suite lives: the trw-mcp package root, and the monorepo root if any.

The public ``wallter/trw-mcp`` repository is the package alone, so anything
under ``src/trw_mcp`` must be addressed from :data:`PACKAGE_ROOT`; only tests
that need the monorepo (its git history, sibling packages, canon mirrors) use
:data:`MONOREPO_ROOT` and must carry :data:`requires_monorepo`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PACKAGE_ROOT: Path = Path(__file__).resolve().parents[1]
_candidate = PACKAGE_ROOT.parent
MONOREPO_ROOT: Path | None = (
    _candidate if (_candidate / "trw-mcp").is_dir() and _candidate.name != "site-packages" else None
)
requires_monorepo = pytest.mark.skipif(
    MONOREPO_ROOT is None, reason="needs the monorepo checkout (public repo is the package alone)"
)
