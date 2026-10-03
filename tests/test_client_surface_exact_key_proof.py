"""CLIENT-SURFACE-SUFFIX-PROOF: on a client agent surface only the file's own repo-relative key proves TRW wrote it.

``recorded_digests`` falls back to a SUFFIX match when no exact key exists, because ``.claude/*`` keys come in
shorter shapes (``<agent>.md``). On a client surface that let a bare key recorded for a *different* file (a
``.claude/agents`` record) authorize trashing an unrecorded client copy that merely shares the filename. Every
client agent surface is written and recorded under its exact repo-relative key, so the fallback there only ever
over-reaches. A file kept this way is reported ``not_installer_owned``, never removed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_SURFACES = [
    (".cursor/agents", "trw-gone.md"),
    (".codex/agents", "trw-gone.toml"),
    (".github/agents", "trw-gone.agent.md"),
    (".agents/agents", "trw-gone.md"),
    (".grok/agents", "trw-gone.md"),
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sweep(tmp_path: Path, manifest_hashes: dict[str, str]) -> dict[str, list[str]]:
    from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}
    _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=manifest_hashes)
    return result


@pytest.mark.parametrize(("surface", "filename"), _SURFACES)
def test_a_suffix_key_never_proves_a_client_copy(tmp_path: Path, surface: str, filename: str) -> None:
    """A bare key with the same bytes (as a .claude record would be) does not authorize removing the client copy."""
    copy = tmp_path / surface / filename
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"the user's own copy")

    result = _sweep(tmp_path, {filename: _sha(b"the user's own copy")})

    assert copy.read_bytes() == b"the user's own copy"
    assert f"{surface}/{filename} (not_installer_owned)" in result["preserved"]
    trash = tmp_path / ".trw" / "trash"
    assert not trash.exists() or not any(trash.iterdir()), "nothing may be captured for an unproven copy"


@pytest.mark.parametrize(("surface", "filename"), _SURFACES)
def test_the_exact_key_still_proves_and_sweeps(tmp_path: Path, surface: str, filename: str) -> None:
    """Non-vacuity: the file's own repo-relative record still authorizes the sweep."""
    copy = tmp_path / surface / filename
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"stale")

    result = _sweep(tmp_path, {f"{surface}/{filename}": _sha(b"stale")})

    assert not copy.exists()
    assert f"{surface}/{filename}" in result["retired"]
