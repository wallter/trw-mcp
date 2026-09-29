"""Shared fixtures/helpers for split bootstrap tests."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap import init_project


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


_TEMPLATES: dict[Path, Path] = {}


def _initialized_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One pristine ``init_project`` tree per xdist worker, built on first use.

    ``init_project`` costs ~0.33 s and ~390 tests only read or mutate its
    output; a ``copytree`` of the pristine tree costs ~0.06 s. The template is
    built lazily inside the first requesting test so that test's autouse
    daemon recorder owns anything the init starts. ``TRW_USER_DIR`` and ``HOME``
    point at template-local dirs so neither the template's daemon grant nor any
    home write lands in the first test's dirs, and the first test's home state
    cannot shape the template.
    """
    base = tmp_path_factory.getbasetemp()
    template = _TEMPLATES.get(base)
    if template is not None:
        return template
    root = tmp_path_factory.mktemp("initialized_repo_template")
    template = root / "repo"
    (template / ".git").mkdir(parents=True)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TRW_USER_DIR", str(root / ".trw-user"))
        mp.setenv("HOME", str(root / ".home"))
        result = init_project(template, ide="claude-code")
    assert not result["errors"]
    _TEMPLATES[base] = template
    return template


def _repin_copied_checkout(repo: Path) -> None:
    """Give a copied tree the namespace pin and daemon grant init would give *repo*.

    ``project_namespace`` and ``.trw/runtime/memory-token`` are the only
    path-derived outputs of ``init_project`` (the grant also lands in the
    test's own ``TRW_USER_DIR``), so drop the template's and re-run the same
    step init runs (``pin_empty_checkout``) against *repo*.
    """
    from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH

    from trw_mcp.bootstrap._namespace_pin import pin_empty_checkout
    from trw_mcp.state._store_migration import _set_pin

    _set_pin(repo / ".trw", None)
    (repo / CHECKOUT_TOKEN_RELPATH).unlink(missing_ok=True)
    result: dict[str, list[str]] = {}
    pin_empty_checkout(repo, result)
    assert not result.get("warnings"), result


@pytest.fixture()
def initialized_repo(fake_git_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a repo with TRW already initialized for Claude Code.

    Each test gets its own copy of a per-worker pristine init (see
    ``_initialized_template``), re-pinned to its own path, so no state is
    shared between tests.

    The client is named EXPLICITLY. A bare ``init_project`` resolves its
    targets through ``detect_ide``, which reports cursor-ide whenever a
    ``cursor`` binary is on the developer's PATH — so on some machines this
    fixture produced a cursor project and on others a Claude Code one. That was
    invisible while agents were written to ``.claude/agents`` regardless of the
    selected client; since PRD-CORE-252-FR03 routes each client's agents to its
    own destination, an unpinned client makes every ``.claude/agents``
    assertion depend on the host's PATH.
    """
    shutil.copytree(_initialized_template(tmp_path_factory), fake_git_repo, symlinks=True, dirs_exist_ok=True)
    _repin_copied_checkout(fake_git_repo)
    return fake_git_repo


_UPDATE_PROJECT_PATCH_TARGETS: tuple[str, ...] = (
    "trw_mcp.bootstrap._update_project._update_framework_files",
    "trw_mcp.bootstrap._update_project._update_mcp_config",
    "trw_mcp.bootstrap._update_project._cleanup_stale_artifacts",
    "trw_mcp.bootstrap._update_project._check_package_version",
    "trw_mcp.bootstrap._update_project._write_installer_metadata",
    "trw_mcp.bootstrap._update_project._write_version_yaml",
    "trw_mcp.bootstrap._update_project._verify_installation",
    "trw_mcp.bootstrap._update_project._run_claude_md_sync",
    "trw_mcp.bootstrap._update_project._ensure_dir",
)


@contextmanager
def patch_update_project_internals() -> Iterator[None]:
    """Patch heavy update internals for focused multi-IDE tests."""
    with ExitStack() as stack:
        for target in _UPDATE_PROJECT_PATCH_TARGETS:
            stack.enter_context(patch(target))
        yield
