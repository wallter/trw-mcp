"""Where this test suite lives: the trw-mcp package root, and the monorepo root if any.

The public ``wallter/trw-mcp`` repository is the package alone, so anything
under ``src/trw_mcp`` must be addressed from :data:`PACKAGE_ROOT`; only tests
that need the monorepo (its git history, sibling packages, canon mirrors) use
:data:`MONOREPO_ROOT` and must carry :data:`requires_monorepo`.
"""

from __future__ import annotations

import os
import shutil
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

#: The hooks read JSON fields with lib-trw.sh ``_json_get`` (jq, else python3).
#: post-tool-degenerate-result.sh alone runs a jq filter with no fallback, so the
#: tests that pin its advisory output must say so rather than fail without jq.
HAS_JQ: bool = shutil.which("jq") is not None
requires_jq = pytest.mark.skipif(
    not HAS_JQ, reason="post-tool-degenerate-result.sh runs a jq filter and has no fallback"
)


def path_without(tmp_path: Path, drop: set[str], path: str | None = None) -> str:
    """*path* (default: this process's PATH) with every executable named in *drop* hidden."""
    parts: list[str] = []
    for index, directory in enumerate((path if path is not None else os.environ.get("PATH", "")).split(os.pathsep)):
        folder = Path(directory)
        if not folder.is_dir() or not any((folder / name).exists() for name in drop):
            parts.append(directory)
            continue
        shadow = tmp_path / f"shadow{index}"
        shadow.mkdir()
        for entry in folder.iterdir():
            if entry.name not in drop:
                (shadow / entry.name).symlink_to(entry)
        parts.append(str(shadow))
    return os.pathsep.join(parts)


#: ``chmod 000``/read-only-directory assertions are vacuous for uid 0, which
#: bypasses the permission bits entirely. CI runners are non-root, so these keep
#: their meaning where it counts.
requires_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses the permission bits this test asserts on"
)

#: The one host-resource budget marker (PRD-QUAL-141): a real, selectable marker whose CI
#: skip policy lives in ``tests/_timing.py``. Kept here as an alias so existing imports work.
requires_local_timing = pytest.mark.requires_local_timing
