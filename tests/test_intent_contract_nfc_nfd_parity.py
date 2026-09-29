"""NFC/NFD tree-entry names must read identically through the batch (stdin) and per-spawn (argv) readers.

The tree bytes are exact (``git mktree``), so any divergence belongs to the readers, not the filesystem. On macOS
git's ``core.precomposeunicode=true`` NFC-normalises argv, which used to make the per-spawn reader fail to list an
NFD-stored tree (an absent leaf under it read as unreadable while the batch said absent). ``_git_run`` now forces
the option off for every per-spawn git, so the repo's own setting must not matter: the whole product below is run
with it both ways. (Linux git does not precompose, so the flag is a no-op there; the matrix is host-agnostic.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._intent_contract_git import pytest_skip_no_git
from tests.test_intent_contract_tree_erasure import _batched, _hazard_repo, _outcome, _per_spawn, _plumb

pytestmark = pytest_skip_no_git

_NFC = "café"
_NFD = "café"
assert _NFC != _NFD


@pytest.mark.parametrize("precompose", ["true", "false"])
@pytest.mark.parametrize("stored", [_NFC, _NFD], ids=["stored-NFC", "stored-NFD"])
@pytest.mark.parametrize("asked", [_NFC, _NFD], ids=["asked-NFC", "asked-NFD"])
@pytest.mark.parametrize("state", ["present", "absent-leaf", "deleted-tree"])
def test_unicode_normalisation_readers_agree(
    tmp_path: Path, precompose: str, stored: str, asked: str, state: str
) -> None:
    repo, tree = _hazard_repo(tmp_path, stored)
    _plumb(repo, "config", "core.precomposeunicode", precompose)
    if state == "deleted-tree":
        (repo / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
    leaf = "absent.yaml" if state == "absent-leaf" else "contract.yaml"
    per_spawn = _outcome(_per_spawn, repo, f"{asked}/{leaf}")
    batch = _outcome(_batched, repo, f"{asked}/{leaf}")
    assert per_spawn == batch
    # Different spellings are different tree names: a query in the other form names nothing, so it is absent.
    expected: object = "absent"
    if stored == asked:
        expected = {
            "present": f"content of {stored!r}\n".encode(),
            "absent-leaf": "absent",
            "deleted-tree": "unreadable",
        }[state]
    assert per_spawn == expected


def test_every_per_spawn_git_argv_carries_the_exact_argv_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag lives in the one helper that builds argv, so a new per-spawn call cannot miss it."""
    from trw_mcp.security.intent_contract import _git_run

    repo, _tree = _hazard_repo(tmp_path, _NFD)
    seen: list[list[str]] = []
    real_run = _git_run.subprocess.run

    def _spy(argv: list[str], *a: object, **k: object) -> object:
        seen.append(list(argv))
        return real_run(argv, *a, **k)  # type: ignore[call-overload]

    monkeypatch.setattr(_git_run.subprocess, "run", _spy)
    assert _outcome(_per_spawn, repo, f"{_NFD}/absent.yaml") == "absent"
    assert {argv[3] for argv in seen} >= {"rev-parse", "ls-tree"}
    assert all(argv[:3] == ["git", "-c", "core.precomposeunicode=false"] for argv in seen)
