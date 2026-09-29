"""The shared ``initialized_repo`` fixture copies a per-worker template; prove the copy is a real init.

A copied tree must carry nothing from the template's path, must be pinned and
granted for its OWN path exactly as ``init_project`` would, and must not share
state with any other test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ._bootstrap_test_support import _TEMPLATES, fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.integration


def _texts(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()
    }


def test_copied_tree_is_pinned_and_granted_for_its_own_path(initialized_repo: Path) -> None:
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH
    from trw_memory.namespaces.identity import resolve_project_namespace

    from trw_mcp.bootstrap._namespace_pin import written_pin

    assert written_pin(initialized_repo) == resolve_project_namespace(initialized_repo)
    assert (initialized_repo / CHECKOUT_TOKEN_RELPATH).is_file()
    grants = DaemonPaths.resolve().grants.read_text(encoding="utf-8")
    assert str(initialized_repo.resolve()) in grants
    assert "initialized_repo_template" not in grants

    template = next(iter(_TEMPLATES.values()))
    assert template != initialized_repo
    for rel, data in _texts(initialized_repo).items():
        assert str(template).encode() not in data, rel
        assert str(template.resolve()).encode() not in data, rel
    assert (template / CHECKOUT_TOKEN_RELPATH).read_bytes() != (initialized_repo / CHECKOUT_TOKEN_RELPATH).read_bytes()


def test_copied_tree_matches_a_fresh_init(initialized_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    from trw_mcp.bootstrap import init_project

    fresh = tmp_path_factory.mktemp("fresh") / initialized_repo.name
    (fresh / ".git").mkdir(parents=True)
    assert not init_project(fresh, ide="claude-code")["errors"]

    copied, expected = _texts(initialized_repo), _texts(fresh)
    # Test-isolation dirs the conftest roots under tmp_path are not init output.
    copied = {k: v for k, v in copied.items() if not k.startswith((".home", ".trw-user", ".tmp"))}
    assert sorted(k for k in copied if ".rollback" not in k) == sorted(k for k in expected if ".rollback" not in k)
    stamped = {".trw/channels/manifest.yaml", ".trw/frameworks/DEPLOYMENT.json", ".trw/frameworks/VERSION.yaml"}
    stamped |= {".trw/installer-meta.yaml", ".trw/config.yaml", ".trw/runtime/memory-token"}
    unpinned = [ln for ln in expected[".trw/config.yaml"].splitlines() if not ln.startswith(b"project_namespace:")]
    assert [
        ln for ln in copied[".trw/config.yaml"].splitlines() if not ln.startswith(b"project_namespace:")
    ] == unpinned
    for rel, data in expected.items():
        if rel in stamped or ".rollback" in rel:
            continue
        assert copied[rel] == data, rel


def test_mutating_one_copy_leaves_the_template_pristine(initialized_repo: Path) -> None:
    template = next(iter(_TEMPLATES.values()))
    before = _texts(template)
    (initialized_repo / ".trw" / "config.yaml").write_text("mutated: true\n", encoding="utf-8")
    (initialized_repo / "AGENTS.md").unlink()
    assert _texts(template) == before
    assert os.path.exists(template / "AGENTS.md")
