"""Tests for registry-driven client integration dispatch."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import trw_mcp.bootstrap._client_integrations as integrations_mod
from trw_mcp.bootstrap._client_integrations import (
    CLIENT_INTEGRATIONS,
    ClientIntegration,
    InstallFn,
    UpdateFn,
    iter_matching_integrations,
    run_install_integrations,
    run_update_integrations,
)


def test_iter_matching_integrations_returns_stable_order() -> None:
    integrations = iter_matching_integrations(["copilot", "opencode", "cursor-cli", "codex"])
    assert [integration.name for integration in integrations] == ["opencode", "cursor", "codex", "copilot"]


def test_iter_matching_integrations_ignores_retired_ids() -> None:
    # Retired ids (gemini/aider) match no integration — they were removed from
    # CLIENT_INTEGRATIONS on retirement.
    integrations = iter_matching_integrations(["gemini", "aider", "opencode"])
    assert [integration.name for integration in integrations] == ["opencode"]


def test_registry_contains_expected_client_integrations() -> None:
    assert [integration.name for integration in CLIENT_INTEGRATIONS] == [
        "opencode",
        "cursor",
        "codex",
        "copilot",
        "antigravity-cli",
    ]


def test_run_install_integrations_dispatches_only_matching(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def _record(name: str) -> InstallFn:
        def _inner(
            target_dir: Path,
            force: bool,
            result: dict[str, list[str]],
            ide_targets: list[str] | None,
        ) -> None:
            assert target_dir == tmp_path
            assert force is True
            assert result == {"created": [], "skipped": [], "errors": []}
            calls.append((name, tuple(ide_targets or [])))

        return _inner

    patched = tuple(
        ClientIntegration(integration.name, integration.platform_ids, _record(integration.name), integration.update)
        for integration in CLIENT_INTEGRATIONS
    )
    monkeypatch.setattr(integrations_mod, "CLIENT_INTEGRATIONS", patched)

    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
    run_install_integrations(tmp_path, ["cursor-cli", "copilot"], force=True, result=result)
    assert calls == [("cursor", ("cursor-cli", "copilot")), ("copilot", ("cursor-cli", "copilot"))]


def test_run_update_integrations_dispatches_only_matching(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str | None, dict[str, str] | None]] = []

    def _record(name: str) -> UpdateFn:
        def _inner(
            target_dir: Path,
            result: dict[str, list[str]],
            ide_override: str | None,
            manifest_hashes: dict[str, str] | None,
        ) -> None:
            assert target_dir == tmp_path
            assert result == {"created": [], "updated": [], "preserved": [], "errors": []}
            calls.append((name, ide_override, manifest_hashes))

        return _inner

    patched = tuple(
        ClientIntegration(integration.name, integration.platform_ids, integration.install, _record(integration.name))
        for integration in CLIENT_INTEGRATIONS
    )
    monkeypatch.setattr(integrations_mod, "CLIENT_INTEGRATIONS", patched)

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
    manifest_hashes = {"foo": "bar"}
    run_update_integrations(
        tmp_path,
        ["opencode", "copilot"],
        ide_override=None,
        result=result,
        manifest_hashes=manifest_hashes,
    )
    assert calls == [("opencode", None, manifest_hashes), ("copilot", None, manifest_hashes)]


def test_update_wrapper_preserves_update_project_patch_seam(tmp_path: Path) -> None:
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
    with patch("trw_mcp.bootstrap._update_project._update_opencode_artifacts") as updater:
        integrations_mod._update_opencode(tmp_path, result, "opencode", {"a": "b"})

    updater.assert_called_once_with(tmp_path, result, ide_override="opencode", manifest_hashes={"a": "b"})


def test_init_project_routes_client_artifacts_through_registry(monkeypatch, tmp_path: Path) -> None:
    import trw_mcp.bootstrap._init_project as init_mod

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    calls: list[tuple[list[str], bool]] = []

    def capture_dispatch(
        _target_dir: Path,
        ide_targets: list[str],
        *,
        force: bool,
        result: dict[str, list[str]],
    ) -> None:
        assert result["errors"] == []
        calls.append((ide_targets, force))

    monkeypatch.setattr(init_mod, "run_install_integrations", capture_dispatch)
    with patch("trw_mcp.bootstrap._init_project._install_copilot_artifacts") as direct_installer:
        init_mod.init_project(tmp_path, ide="copilot", force=True)

    assert calls == [(["copilot"], True)]
    direct_installer.assert_not_called()


def test_update_post_phases_dispatches_registry_before_distill_channels(tmp_path: Path) -> None:
    import trw_mcp.bootstrap._update_project as update_mod

    calls: list[object] = []
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

    def dispatch(
        target_dir: Path,
        ide_targets: list[str],
        *,
        ide_override: str | None,
        result: dict[str, list[str]],
        manifest_hashes: dict[str, str] | None,
    ) -> None:
        calls.append(("registry", target_dir, ide_targets, ide_override, result, manifest_hashes))

    with (
        patch("trw_mcp.models.config._credentials.migrate_for_update_project"),
        patch.object(update_mod, "_write_installer_metadata"),
        patch.object(update_mod, "_write_version_yaml"),
        patch.object(update_mod, "resolve_ide_targets", return_value=["claude-code", "copilot"]),
        patch.object(update_mod, "_update_config_target_platforms"),
        patch.object(update_mod, "_run_claude_md_sync"),
        patch.object(update_mod, "_run_auto_maintenance"),
        patch.object(update_mod, "run_update_integrations", side_effect=dispatch),
        patch(
            "trw_mcp.bootstrap._claude_code_distill_channels.install_claude_code_distill_channels",
            side_effect=lambda *_args, **_kwargs: calls.append("claude") or {},
        ),
        patch.object(update_mod, "_rewrite_hook_env_for_primary_profile"),
        patch.object(update_mod, "_write_manifest"),
        patch.object(update_mod, "_verify_installation"),
    ):
        update_mod._run_post_update_phases(
            tmp_path,
            False,
            "copilot",
            result,
            None,
            manifest_hashes={"a": "b"},
        )

    assert calls[0] == ("registry", tmp_path, ["claude-code", "copilot"], "copilot", result, {"a": "b"})
    assert calls[1:] == ["claude"]
