"""PRD-INFRA-150 FR03/FR04/FR05 — phase_install_packages downgrade guard.

The installer force-pins the bundled wheels via a ``--no-deps --no-cache-dir
--no-index`` reinstall whose comment says it "defeats PyPI downgrade". That
protects the pull-ahead case (bundled NEWER than PyPI) but had no guard for the
inverse: a bundled wheel OLDER than an already-installed version. The operator
report ``sub_x2O2h3CYyzKZWLu2#a`` saw a 0.54.0-bundled installer silently
downgrade a running 0.55.15 install, breaking ``trw_learn``'s ``scope`` kwarg.

These tests drive the runtime-used guard surface (no network, no real pip):

- ``_compare_versions(a, b) -> int`` (FR04): PEP 440 semantics via
  ``packaging.version`` — ``0.55.10 > 0.55.9`` (not lexicographic).
- ``downgrade_guard_decision(installed, bundled) -> (bool, str)`` (FR04):
  emits the decision label for the structured log line.

The template is loaded as a module by file path (``scripts/`` is not a package).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_downgrade_guard", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer() -> ModuleType:
    return _load()


# ── FR04: packaging.version semantics ────────────────────────────────────────


def test_semantic_version_compare(installer: ModuleType) -> None:
    """0.55.10 > 0.55.9 and 0.55.2 > 0.54.0 (semantic, not lexicographic)."""
    assert installer._compare_versions("0.55.10", "0.55.9") == 1
    assert installer._compare_versions("0.55.2", "0.54.0") == 1
    assert installer._compare_versions("0.55.9", "0.55.10") == -1
    assert installer._compare_versions("0.55.17", "0.55.17") == 0
    # Lexicographic compare would (wrongly) say "0.55.9" > "0.55.10".


def test_compare_versions_invalid_raises(installer: ModuleType) -> None:
    """An unparsable version raises so callers can decide the fail-open policy."""
    with pytest.raises(Exception):
        installer._compare_versions("garbage", "0.1.0")


# ── FR04: decision logging ───────────────────────────────────────────────────


def test_decision_log_newer_installed(installer: ModuleType) -> None:
    """installed 0.55.15 / bundled 0.54.0 -> kept-installed (newer), skip=True."""
    skip_force, decision = installer.downgrade_guard_decision("0.55.15", "0.54.0")
    assert skip_force is True
    assert decision == "kept-installed (newer)"


def test_decision_log_upgrade(installer: ModuleType) -> None:
    skip_force, decision = installer.downgrade_guard_decision("0.54.0", "0.55.17")
    assert skip_force is False
    assert decision == "installed-bundled (upgrade)"


def test_decision_log_equal(installer: ModuleType) -> None:
    skip_force, decision = installer.downgrade_guard_decision("0.55.17", "0.55.17")
    assert skip_force is False
    assert decision == "installed-bundled (equal)"


def test_decision_log_fresh(installer: ModuleType) -> None:
    skip_force, decision = installer.downgrade_guard_decision(None, "0.55.17")
    assert skip_force is False
    assert decision == "installed-bundled (fresh)"


def test_decision_log_probe_error_fails_open(installer: ModuleType) -> None:
    """Unparsable installed metadata -> proceed (fresh-equivalent), never skip."""
    skip_force, decision = installer.downgrade_guard_decision("not-a-version", "0.55.17")
    assert skip_force is False
    assert decision == "installed-bundled (fresh)"


def test_decision_log_line_contains_both_versions(installer: ModuleType) -> None:
    """NFR03: format_guard_log_line records installed, bundled, and decision."""
    line = installer.format_guard_log_line("trw-mcp", "0.55.15", "0.54.0", "kept-installed (newer)")
    assert "trw-mcp" in line
    assert "installed=0.55.15" in line
    assert "bundled=0.54.0" in line
    assert "decision=kept-installed (newer)" in line


# ── FR03/FR04/FR05 phase-LEVEL integration: the WIRING (PRD §7) ───────────────
#
# The unit tests above exercise only the pure helpers. PRD §7
# (``test_integration_guard_no_network``) + the FR05 AC require a phase-level
# test that proves ``phase_install_packages`` actually CONSULTS the guard and
# suppresses the force-pin / combined-install seam when a strictly-newer version
# is already installed — i.e. the helpers are wired into the install flow, not
# merely callable. No network, no real pip: the installed-version probe and
# every ``subprocess.run`` (combined install, force-pin, import verify, MCP
# preflight) are stubbed seams.


def _drive_phase_install_with_newer_installed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bundled: str = "0.54.0",
    installed: str = "0.55.15",
) -> tuple[ModuleType, list[list[str]], list[str]]:
    """Drive ``phase_install_packages`` with a strictly-NEWER installed probe.

    Returns ``(module, observed_subprocess_cmds, ui_info_lines)``. The bundled
    ``TRW_VERSION`` is monkeypatched OLDER than the probed installed version so
    the downgrade branch fires for BOTH packages.
    """
    module = _load()

    # Bundled version is read from the module-global TRW_VERSION inside
    # phase_install_packages; in the template it is the literal "{{VERSION}}"
    # placeholder, so we pin a real, OLDER bundled version to exercise the
    # strictly-newer-installed (downgrade) branch.
    monkeypatch.setattr(module, "TRW_VERSION", bundled)

    # Probe reports a strictly-NEWER installed version for both packages.
    monkeypatch.setattr(module, "_probe_installed_version", lambda _python, _pkg: installed)

    observed: list[list[str]] = []

    def fake_run(
        cmd: list[str],
        env: object = None,
        stdout: object = None,
        stderr: object = None,
        *,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
        check: bool = False,
        input: object = None,
        **_kwargs: object,
    ) -> SimpleNamespace:
        observed.append(list(cmd))
        # Import-verify probes ("import trw_memory" / "import trw_mcp") return rc 0;
        # any pip install seam would also return success — but the guard should
        # never reach one in the newer-installed path.
        return SimpleNamespace(
            returncode=0,
            stdout='{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"trw_session_start"}]}}\n',
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    info_lines: list[str] = []
    ui = MagicMock()
    ui.info.side_effect = lambda msg: info_lines.append(str(msg))

    memory_whl = tmp_path / f"trw_memory-{bundled}-py3-none-any.whl"
    mcp_whl = tmp_path / f"trw_mcp-{bundled}-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")

    # No pip_target: drives the site-packages path (no wrapper/preflight branch),
    # so the only subprocess calls are the import-verify probes inside
    # _verify_package_imports — no pip install must occur.
    module.phase_install_packages(
        ui,
        2,
        4,
        sys.executable,
        memory_whl,
        mcp_whl,
    )
    return module, observed, info_lines


def _is_pip_install_cmd(cmd: list[str]) -> bool:
    """True when *cmd* is a pip/uv install invocation (the force-pin/combined seam)."""
    return "install" in cmd and ("pip" in cmd or "uv" in cmd)


def test_integration_guard_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Newer-installed both packages -> NO pip install seam invoked, decision logged.

    Proves the guard is WIRED into phase_install_packages (not just callable):
    (1) neither the combined install nor the force-pin reinstall runs for the
    newer-installed packages, and (2) the 'keeping newer installed' decision is
    logged. No real network / pip (the probe + subprocess.run are stubbed).
    """
    _module, observed, info_lines = _drive_phase_install_with_newer_installed(tmp_path, monkeypatch)

    # (1) The force-pin / combined-install seam is NOT invoked.
    install_cmds = [cmd for cmd in observed if _is_pip_install_cmd(cmd)]
    assert install_cmds == [], (
        "downgrade guard must skip every pip install seam when both packages are "
        f"newer-installed; saw install commands: {install_cmds}"
    )
    # No bundled-wheel force-pin (the --no-index reinstall) either.
    assert not any("--no-index" in cmd for cmd in observed), (
        "the --no-index force-pin must not run in the newer-installed case"
    )

    # (2) The 'keeping newer installed' decision is logged.
    assert any("keeping newer installed" in line for line in info_lines), (
        f"expected a 'keeping newer installed' decision log line; got: {info_lines}"
    )
    # And the per-package structured decision line records both versions + label.
    assert any(
        "trw-mcp" in line
        and "installed=0.55.15" in line
        and "bundled=0.54.0" in line
        and "decision=kept-installed (newer)" in line
        for line in info_lines
    ), f"expected the structured trw-mcp guard decision line; got: {info_lines}"


def test_integration_guard_does_not_overreach_when_upgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative-control: bundled NEWER than installed -> the install seam DOES run.

    Guards against a false-positive where the guard suppresses a legitimate
    upgrade. Bundled 0.55.17 over installed 0.54.0 must reach the combined
    pip install seam (the guard does not fire).
    """
    _module, observed, info_lines = _drive_phase_install_with_newer_installed(
        tmp_path, monkeypatch, bundled="0.55.17", installed="0.54.0"
    )

    install_cmds = [cmd for cmd in observed if _is_pip_install_cmd(cmd)]
    assert install_cmds, (
        "a legitimate upgrade (bundled newer than installed) MUST reach the pip "
        f"install seam; observed subprocess cmds: {observed[:3]}"
    )
    # The combined install carries both wheels via --find-links.
    assert any("--find-links" in cmd for cmd in install_cmds), (
        "the upgrade path must use the combined --find-links install"
    )
    assert not any("keeping newer installed" in line for line in info_lines), (
        "the 'keeping newer installed' line must NOT appear on a legitimate upgrade"
    )
