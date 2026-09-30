"""``collect_version_status(release_tree=True)``: measure the tree being released, not the machine.

``publish-release.sh`` from a worktree used to measure the MAIN checkout (its installed distributions, its
recorded manifest, the running process), so uncommitted work there blocked a release it was not part of
and could stand in for the tree that was. Release-tree scope compares only the version surfaces the tree
carries, and says which comparisons it did not make.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INSTALLATION_MISMATCHES = (
    "manifest_packages_missing",
    "trw_mcp_installed_vs_manifest",
    "trw_memory_installed_vs_manifest",
    "trw_mcp_manifest_entry_missing",
    "trw_memory_manifest_entry_missing",
)


def _tree(root: Path, *, mcp_version: str) -> Path:
    """A minimal release tree: pyprojects and the tracked framework manifest, and NO installed state."""
    from trw_mcp.models.config import TRWConfig

    for package, version in (("trw-mcp", mcp_version), ("trw-memory", "4.0.0")):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "pyproject.toml").write_text(
            f'[project]\nname = "{package}"\nversion = "{version}"\n', encoding="utf-8"
        )
    frameworks = root / ".trw" / "frameworks"
    frameworks.mkdir(parents=True, exist_ok=True)
    (frameworks / "VERSION.yaml").write_text(f"framework_version: {TRWConfig().framework_version}\n", encoding="utf-8")
    return root


@pytest.fixture()
def live_version() -> str:
    from trw_mcp import __version__

    return __version__


def test_release_tree_scope_needs_no_installed_state(tmp_path: Path, live_version: str) -> None:
    from trw_mcp.server._subcommands_release import collect_version_status

    tree = _tree(tmp_path, mcp_version=live_version)

    status = collect_version_status(tree, release_tree=True)

    assert not [m for m in status["mismatches"] if m in _INSTALLATION_MISMATCHES or m.startswith("live_process")], (
        status
    )
    assert status["versions"]["packages"]["trw-mcp"] == live_version
    assert any("release-tree scope" in w for w in status["warnings"]), "the skipped comparisons must be stated"


def test_the_installation_comparisons_run_when_the_scope_is_not_release_tree(tmp_path: Path, live_version: str) -> None:
    from trw_mcp.server._subcommands_release import collect_version_status

    tree = _tree(tmp_path, mcp_version=live_version)

    status = collect_version_status(tree)

    assert "manifest_packages_missing" in status["mismatches"]
    assert not any("release-tree scope" in w for w in status["warnings"])


def test_release_tree_scope_still_fails_a_tree_whose_own_version_disagrees_with_its_code(tmp_path: Path) -> None:
    """The relaxation must not make the gate vacuous: the tree's pyproject is compared with the tree's code."""
    from trw_mcp.server._subcommands_release import assert_version_status_compatible

    tree = _tree(tmp_path, mcp_version="0.0.1-not-the-live-version")

    with pytest.raises(SystemExit) as raised:
        assert_version_status_compatible(tree, release_tree=True)

    assert "trw_mcp_package_vs_live_server" in str(raised.value)


def test_release_tree_scope_fails_a_tree_whose_framework_manifest_disagrees(tmp_path: Path, live_version: str) -> None:
    from trw_mcp.server._subcommands_release import assert_version_status_compatible

    tree = _tree(tmp_path, mcp_version=live_version)
    (tree / ".trw" / "frameworks" / "VERSION.yaml").write_text("framework_version: v0.0_TRW\n", encoding="utf-8")

    with pytest.raises(SystemExit) as raised:
        assert_version_status_compatible(tree, release_tree=True)

    assert "framework_protocol_vs_installed_asset" in str(raised.value)
