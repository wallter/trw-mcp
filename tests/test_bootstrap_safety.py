"""Bootstrap/update safety hardening tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._update_transaction import _snapshot_transaction_paths
from trw_mcp.models.config._client_profile import WriteTargets
from trw_mcp.models.config._profiles import resolve_client_profile

_PROFILE_OUTPUT_CASES: tuple[tuple[str, frozenset[str], tuple[str, ...]], ...] = (
    ("claude-code", frozenset({"claude_md"}), ("CLAUDE.md",)),
    ("opencode", frozenset({"agents_md"}), (".opencode/INSTRUCTIONS.md",)),
    ("cursor-ide", frozenset({"agents_md", "cursor_rules"}), (".cursor/rules/trw-ceremony.mdc",)),
    ("cursor-cli", frozenset({"agents_md", "agents_md_primary", "cli_config"}), ("AGENTS.md", ".cursor/cli.json")),
    ("codex", frozenset({"agents_md"}), (".codex/INSTRUCTIONS.md",)),
    ("copilot", frozenset({"agents_md", "copilot_instructions"}), (".github/copilot-instructions.md",)),
    ("antigravity-cli", frozenset({"agents_md", "antigravitycli_md"}), ("ANTIGRAVITY.md",)),
)


@pytest.fixture()
def initialized_repo(tmp_path: Path) -> Path:
    """Create a minimal repo with TRW initialized."""
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="claude-code")
    assert not result["errors"]
    return tmp_path


def test_active_profiles_cover_write_target_boolean_schema() -> None:
    """Every WriteTargets boolean is declared by at least one active profile case."""
    boolean_flags = {
        name
        for name, field in WriteTargets.model_fields.items()
        if field.annotation is bool or str(field.annotation) == "bool"
    }
    exercised_flags = set().union(*(flags for _, flags, _ in _PROFILE_OUTPUT_CASES))
    assert exercised_flags == boolean_flags


@pytest.mark.parametrize(
    ("ide", "expected_flags", "expected_paths"),
    _PROFILE_OUTPUT_CASES,
)
def test_supported_profiles_generate_primary_bootstrap_surface(
    initialized_repo: Path,
    ide: str,
    expected_flags: frozenset[str],
    expected_paths: tuple[str, ...],
) -> None:
    """Supported profiles generate their primary bootstrap instruction surfaces."""
    profile = resolve_client_profile(ide)
    enabled_flags = {
        name
        for name, field in WriteTargets.model_fields.items()
        if (field.annotation is bool or str(field.annotation) == "bool") and getattr(profile.write_targets, name)
    }
    assert enabled_flags == expected_flags

    if ide != "claude-code":
        assert all(not (initialized_repo / path).exists() for path in expected_paths)

    result = update_project(initialized_repo, ide=ide)

    assert not result["errors"]
    for expected_path in expected_paths:
        instruction_path = initialized_repo / expected_path
        assert instruction_path.is_file()
        assert "TRW" in instruction_path.read_text(encoding="utf-8")


def test_update_project_rolls_back_directories_after_mid_write_failure(initialized_repo: Path) -> None:
    """A post-core write failure restores previously modified framework directories."""
    framework_path = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
    original = "custom framework content before failed update\n"
    framework_path.write_text(original, encoding="utf-8")

    with patch("trw_mcp.bootstrap._update_project._write_version_yaml", side_effect=OSError("disk full")):
        result = update_project(initialized_repo)

    assert any("update-project failed" in error for error in result["errors"])
    assert any("rolled back" in warning for warning in result["warnings"])
    assert framework_path.read_text(encoding="utf-8") == original


def test_update_project_rolls_back_new_managed_directories_after_late_failure(initialized_repo: Path) -> None:
    """A late failure removes managed directories that did not exist before the update."""
    opencode_dir = initialized_repo / ".opencode"
    assert not opencode_dir.exists()

    with patch(
        "trw_mcp.bootstrap._update_project._verify_installation",
        side_effect=RuntimeError("verify failed"),
    ):
        result = update_project(initialized_repo, ide="opencode")

    assert any("update-project failed" in error for error in result["errors"])
    assert any("rolled back" in warning for warning in result["warnings"])
    assert not opencode_dir.exists()


@pytest.mark.parametrize(
    ("ide", "relative_path"),
    [
        ("codex", ".agents/skills/trw-audit/SKILL.md"),
        ("copilot", ".vscode/mcp.json"),
    ],
)
def test_update_project_rolls_back_managed_client_files_after_late_failure(
    initialized_repo: Path,
    ide: str,
    relative_path: str,
) -> None:
    managed_path = initialized_repo / relative_path
    managed_path.parent.mkdir(parents=True, exist_ok=True)
    original = b"original managed client content\n"
    managed_path.write_bytes(original)

    with patch(
        "trw_mcp.bootstrap._update_project._verify_installation",
        side_effect=RuntimeError("verify failed"),
    ):
        result = update_project(initialized_repo, ide=ide)

    assert any("update-project failed" in error for error in result["errors"])
    assert any("rolled back" in warning for warning in result["warnings"])
    assert managed_path.read_bytes() == original


@pytest.mark.parametrize(
    ("ide", "relative_dir"),
    [("codex", ".agents"), ("copilot", ".vscode")],
)
def test_update_project_removes_new_managed_client_roots_after_late_failure(
    initialized_repo: Path,
    ide: str,
    relative_dir: str,
) -> None:
    managed_root = initialized_repo / relative_dir
    if managed_root.exists():
        import shutil

        shutil.rmtree(managed_root)

    with patch(
        "trw_mcp.bootstrap._update_project._verify_installation",
        side_effect=RuntimeError("verify failed"),
    ):
        result = update_project(initialized_repo, ide=ide)

    assert any("update-project failed" in error for error in result["errors"])
    assert any("rolled back" in warning for warning in result["warnings"])
    assert not managed_root.exists()


def test_update_project_preserves_context_transients_after_late_failure(
    initialized_repo: Path,
) -> None:
    """Context cleanup is post-transaction and must not run after rollback."""
    transient = initialized_repo / ".trw" / "context" / "velocity.yaml"
    transient.write_text("live session state\n", encoding="utf-8")

    with patch(
        "trw_mcp.bootstrap._update_project._verify_installation",
        side_effect=RuntimeError("verify failed"),
    ):
        result = update_project(initialized_repo)

    assert result["errors"]
    assert transient.read_text(encoding="utf-8") == "live session state\n"


def test_update_project_does_not_follow_context_swap_after_commit(initialized_repo: Path) -> None:
    """A final-verification parent swap cannot redirect post-commit cleanup."""
    from trw_mcp.bootstrap import _update_project

    context = initialized_repo / ".trw" / "context"
    external = initialized_repo.parent / "external-context"
    external.mkdir()
    victim = external / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    original_verify = _update_project._verify_installation

    def verify_then_swap(*args: object, **kwargs: object) -> object:
        verification = original_verify(*args, **kwargs)
        shutil.rmtree(context)
        context.symlink_to(external, target_is_directory=True)
        return verification

    with patch("trw_mcp.bootstrap._update_project._verify_installation", side_effect=verify_then_swap):
        result = update_project(initialized_repo)

    assert not result["errors"]
    assert victim.read_text(encoding="utf-8") == "keep"
    assert sorted(path.name for path in external.iterdir()) == ["victim.txt"]
    assert any("Skipped unsafe context cleanup" in warning for warning in result["warnings"])


def test_snapshot_failure_removes_partial_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "target"
    (target / ".agents").mkdir(parents=True)
    snapshot = tmp_path / "snapshot"

    with (
        patch("trw_mcp.bootstrap._update_transaction.tempfile.mkdtemp", return_value=str(snapshot)),
        patch("trw_mcp.bootstrap._update_transaction.shutil.copytree", side_effect=OSError("copy failed")),
        pytest.raises(OSError, match="copy failed"),
    ):
        _snapshot_transaction_paths(target)

    assert not snapshot.exists()
