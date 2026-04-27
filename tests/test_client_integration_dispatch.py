"""Tests for registry-driven client integration dispatch."""

from __future__ import annotations

from pathlib import Path

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
    integrations = iter_matching_integrations(["gemini", "opencode", "cursor-cli", "codex"])
    assert [integration.name for integration in integrations] == ["opencode", "cursor", "codex", "gemini"]


def test_registry_contains_expected_client_integrations() -> None:
    assert [integration.name for integration in CLIENT_INTEGRATIONS] == [
        "opencode",
        "cursor",
        "codex",
        "copilot",
        "gemini",
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
        ["opencode", "gemini"],
        ide_override=None,
        result=result,
        manifest_hashes=manifest_hashes,
    )
    assert calls == [("opencode", None, manifest_hashes), ("gemini", None, manifest_hashes)]
