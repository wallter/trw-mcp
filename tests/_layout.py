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
# A GitHub Actions checkout lives at ``<work>/trw-mcp/trw-mcp``, so the parent
# of the package root DOES contain a ``trw-mcp`` directory there too — that is
# the checkout itself, not a monorepo (2026-09-07: this false positive ran the
# canon-mirror tests in the public CI and failed them). The monorepo is the only
# layout with the independent release manifest or sibling/workspace identity.
# The independent marker keeps deletion of a checked canon/instruction surface
# from turning its regression tests into skips. The legacy identity also keeps
# deletion of the release manifest itself from disabling monorepo checks.
MONOREPO_ROOT: Path | None = (
    _candidate
    if (
        _candidate.name != "site-packages"
        and (
            (_candidate / "release-packages.yaml").is_file()
            or (
                (_candidate / "trw-mcp" / "pyproject.toml").is_file()
                and (_candidate / "trw-memory" / "pyproject.toml").is_file()
                and (_candidate / "CLAUDE.md").is_file()
            )
        )
    )
    else None
)
requires_monorepo = pytest.mark.skipif(
    MONOREPO_ROOT is None, reason="needs the monorepo checkout (public repo is the package alone)"
)
