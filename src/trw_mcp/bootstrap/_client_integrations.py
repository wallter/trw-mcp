"""Registry-driven dispatch for client-specific bootstrap/update integrations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

InstallFn = Callable[[Path, bool, dict[str, list[str]], list[str] | None], None]
UpdateFn = Callable[[Path, dict[str, list[str]], str | None, dict[str, str] | None], None]


@dataclass(frozen=True, slots=True)
class ClientIntegration:
    """Client-specific bootstrap/update integration binding."""

    name: str
    platform_ids: tuple[str, ...]
    install: InstallFn
    update: UpdateFn

    def matches(self, ide_targets: Iterable[str]) -> bool:
        target_set = set(ide_targets)
        return any(platform_id in target_set for platform_id in self.platform_ids)


def _install_opencode(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_opencode_artifacts

    _install_opencode_artifacts(target_dir, force=force, result=result)


def _update_opencode(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._ide_targets import _update_opencode_artifacts

    _update_opencode_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


def _install_cursor(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    ide_targets: list[str] | None,
) -> None:
    from ._init_project import _install_cursor_artifacts

    _install_cursor_artifacts(target_dir, force=force, result=result, ide_targets=ide_targets)


def _update_cursor(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    _: dict[str, str] | None,
) -> None:
    from ._ide_targets import _update_cursor_artifacts

    _update_cursor_artifacts(target_dir, result, ide_override=ide_override)


def _install_codex(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_codex_artifacts

    _install_codex_artifacts(target_dir, force=force, result=result)


def _update_codex(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._ide_targets import _update_codex_artifacts

    _update_codex_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


def _install_copilot(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_copilot_artifacts

    _install_copilot_artifacts(target_dir, force=force, result=result)


def _update_copilot(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._ide_targets import _update_copilot_artifacts

    _update_copilot_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


def _install_gemini(target_dir: Path, force: bool, result: dict[str, list[str]], _: list[str] | None) -> None:
    from ._init_project import _install_gemini_artifacts

    _install_gemini_artifacts(target_dir, force=force, result=result)


def _update_gemini(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None,
    manifest_hashes: dict[str, str] | None,
) -> None:
    from ._ide_targets import _update_gemini_artifacts

    _update_gemini_artifacts(target_dir, result, ide_override=ide_override, manifest_hashes=manifest_hashes)


CLIENT_INTEGRATIONS: tuple[ClientIntegration, ...] = (
    ClientIntegration("opencode", ("opencode",), _install_opencode, _update_opencode),
    ClientIntegration("cursor", ("cursor-ide", "cursor-cli"), _install_cursor, _update_cursor),
    ClientIntegration("codex", ("codex",), _install_codex, _update_codex),
    ClientIntegration("copilot", ("copilot",), _install_copilot, _update_copilot),
    ClientIntegration("gemini", ("gemini",), _install_gemini, _update_gemini),
)


def iter_matching_integrations(ide_targets: Iterable[str]) -> tuple[ClientIntegration, ...]:
    """Return integrations activated by the provided target platform IDs."""
    return tuple(integration for integration in CLIENT_INTEGRATIONS if integration.matches(ide_targets))


def run_install_integrations(
    target_dir: Path,
    ide_targets: list[str],
    *,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Run matching bootstrap installers in stable registry order."""
    for integration in iter_matching_integrations(ide_targets):
        integration.install(target_dir, force, result, ide_targets)


def run_update_integrations(
    target_dir: Path,
    ide_targets: list[str],
    *,
    ide_override: str | None,
    result: dict[str, list[str]],
    manifest_hashes: dict[str, str] | None,
) -> None:
    """Run matching update dispatchers in stable registry order."""
    for integration in iter_matching_integrations(ide_targets):
        integration.update(target_dir, result, ide_override, manifest_hashes)
