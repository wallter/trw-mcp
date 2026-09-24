"""G2 (installer refinement 5.1.0): every client's directory-rooted surface must
be inside ``_TRANSACTION_DIRS``, or update-project's change counter cannot see
files written under it.

Root cause: ``.grok`` was missing from ``_TRANSACTION_DIRS`` when grok became a
client — the counter's file discovery is this allow-listed directory scan, so
none of the 12 files a fresh ``--ide grok`` run wrote were ever diffed, and the
run reported "0 created" while doing real work (reproduced live in the interop
audit). This test parametrizes over every currently-registered client so a
future client addition fails HERE instead of repeating the same silent miss.
"""

from __future__ import annotations

import pytest

from trw_mcp.bootstrap._update_transaction import _TRANSACTION_DIRS
from trw_mcp.client_profiles.catalog import client_surfaces
from trw_mcp.models.config._profiles import builtin_client_ids


@pytest.mark.unit
@pytest.mark.parametrize("client_id", builtin_client_ids())
def test_every_client_directory_surface_is_a_transaction_dir(client_id: str) -> None:
    for surface in client_surfaces(client_id):
        if surface.home_scoped:
            # Writes to Path.home(), not the project tree — out of scope for
            # a project-relative update transaction by design (PRD-FIX-133).
            continue
        rel = surface.relpath
        if "/" not in rel:
            # A bare top-level file (e.g. ".mcp.json", ".gitignore") is listed
            # verbatim in _TRANSACTION_FILES, not scanned as a directory.
            continue
        if rel.startswith(".trw/"):
            # .trw is snapshotted through _MANAGED_TRW_FILES + explicit
            # _TRANSACTION_FILES entries, never bulk-scanned as a directory.
            continue
        root = rel.split("/", 1)[0]
        assert root in _TRANSACTION_DIRS, (
            f"{client_id}'s surface {rel!r} is rooted under {root!r}, which "
            "_TRANSACTION_DIRS does not scan — every file update-project writes "
            "under it is invisible to the change-counter diff (G2). Add "
            f"{root!r} to _TRANSACTION_DIRS in bootstrap/_update_transaction.py."
        )


@pytest.mark.unit
def test_transaction_dirs_includes_grok() -> None:
    """Direct regression pin for the reproduced defect."""
    assert ".grok" in _TRANSACTION_DIRS
