"""Shared fixtures/helpers for split bootstrap tests."""

from __future__ import annotations

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


@pytest.fixture()
def initialized_repo(fake_git_repo: Path) -> Path:
    """Create a repo with TRW already initialized for Claude Code.

    The client is named EXPLICITLY. A bare ``init_project`` resolves its
    targets through ``detect_ide``, which reports cursor-ide whenever a
    ``cursor`` binary is on the developer's PATH — so on some machines this
    fixture produced a cursor project and on others a Claude Code one. That was
    invisible while agents were written to ``.claude/agents`` regardless of the
    selected client; since PRD-CORE-252-FR03 routes each client's agents to its
    own destination, an unpinned client makes every ``.claude/agents``
    assertion depend on the host's PATH.
    """
    result = init_project(fake_git_repo, ide="claude-code")
    assert not result["errors"]
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
