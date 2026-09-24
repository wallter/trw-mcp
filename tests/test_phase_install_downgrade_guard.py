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

    # Probe reports the given installed version for both packages until a wheel install replaces it.
    resident = {"version": installed}
    monkeypatch.setattr(module, "_probe_installed_version", lambda _python, _pkg, *_a: resident["version"])

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
        if any(str(c).endswith(".whl") for c in cmd):
            resident["version"] = bundled
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


# ── I1/I2 (installer refinement 5.1.0): the decision must thread past the pip
# seam into the post-install version stamp and shadow check, not stop at
# "don't run pip". Root cause: _restart_mcp_servers ran independently of the
# downgrade guard and always compared against the raw TRW_VERSION bundle
# constant, so a kept-installed run permanently reported compatible:false and
# prescribed a destructive `pip uninstall` for the install it had just
# intentionally kept. ───────────────────────────────────────────────────────


def test_kept_installed_records_effective_version_for_downstream_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """phase_install_packages must set _MCP_EFFECTIVE_VERSION to the PROBED
    installed version (not the bundle constant) when it decides kept-installed
    — this is what I1/I2 downstream consume instead of TRW_VERSION."""
    module, _observed, _info = _drive_phase_install_with_newer_installed(tmp_path, monkeypatch)
    assert module._MCP_EFFECTIVE_VERSION == "0.55.15"


def test_upgrade_records_effective_version_as_bundled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: a legitimate upgrade decides bundled is the effective version."""
    module, _observed, _info = _drive_phase_install_with_newer_installed(
        tmp_path, monkeypatch, bundled="0.55.17", installed="0.54.0"
    )
    assert module._MCP_EFFECTIVE_VERSION == "0.55.17"


def _seed_deployment_for_restart(target: Path) -> None:
    frameworks = target / ".trw" / "frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "FRAMEWORK.md").write_text("v99.9_TRW\n", encoding="utf-8")
    (frameworks / "AARE-F-FRAMEWORK.md").write_text("**Version**: 3.2.1\n", encoding="utf-8")


def test_restart_mcp_servers_kept_installed_no_false_shadow_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2: when the downgrade guard decided kept-installed, and PATH resolves to
    exactly the kept version, no 'shadowing' warning must fire. PRD-INFRA-192
    FR12: VERSION.yaml no longer stamps a package version at all —
    that record moved to managed-artifacts.yaml `packages`."""
    module = _load()
    _seed_deployment_for_restart(tmp_path)
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", "0.55.15")
    monkeypatch.setattr(module, "_resolve_path_trw_mcp_version", lambda: "0.55.15")

    warnings: list[str] = []
    ui = MagicMock()
    ui.step_warn.side_effect = lambda msg: warnings.append(str(msg))

    module._restart_mcp_servers(tmp_path, ui)

    assert not any("shadowing" in w.lower() for w in warnings), (
        f"a kept-installed run must not warn about shadowing its own kept version; got: {warnings}"
    )
    stamp = (tmp_path / ".trw" / "frameworks" / "VERSION.yaml").read_text(encoding="utf-8")
    assert "trw_mcp_version" not in stamp, f"the stamp is retired; nothing writes it anymore: {stamp}"


def test_restart_mcp_servers_still_warns_on_a_real_shadow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: a genuine mismatch (something ELSE on PATH) must still warn."""
    module = _load()
    _seed_deployment_for_restart(tmp_path)
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", "0.55.17")
    monkeypatch.setattr(module, "_resolve_path_trw_mcp_version", lambda: "0.40.0")

    warnings: list[str] = []
    ui = MagicMock()
    ui.step_warn.side_effect = lambda msg: warnings.append(str(msg))

    module._restart_mcp_servers(tmp_path, ui)

    assert any("shadowing" in w.lower() for w in warnings), (
        f"a real PATH shadow must still be warned about; got: {warnings}"
    )


def _restart_warnings(module: ModuleType, target: Path) -> list[str]:
    warnings: list[str] = []
    ui = MagicMock()
    ui.step_warn.side_effect = lambda msg: warnings.append(str(msg))
    module._restart_mcp_servers(target, ui)
    return warnings


def test_restart_mcp_servers_warns_on_a_different_binary_at_the_same_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INFRA-192-FR02: a version match is not identity; another binary on PATH still shadows."""
    module = _load()
    _seed_deployment_for_restart(tmp_path)
    installed, stale = tmp_path / "venv-trw-mcp", tmp_path / "user-trw-mcp"
    installed.write_text("#!/bin/sh\n", encoding="utf-8")
    stale.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", "5.0.0")
    monkeypatch.setattr(module, "_MCP_TARGET_BINARY", str(installed))
    monkeypatch.setattr(module, "_resolve_path_trw_mcp_version", lambda: "5.0.0")
    monkeypatch.setattr(module.shutil, "which", lambda name, *a, **k: str(stale) if name == "trw-mcp" else None)

    warnings = _restart_warnings(module, tmp_path)

    assert any("shadowing" in w.lower() for w in warnings), warnings
    assert any(str(stale) in w for w in warnings) and any(str(installed) in w for w in warnings), warnings


def test_restart_mcp_servers_accepts_a_symlink_to_the_installed_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    _seed_deployment_for_restart(tmp_path)
    installed = tmp_path / "venv-trw-mcp"
    installed.write_text("#!/bin/sh\n", encoding="utf-8")
    link = tmp_path / "on-path-trw-mcp"
    link.symlink_to(installed)
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", "5.0.0")
    monkeypatch.setattr(module, "_MCP_TARGET_BINARY", str(installed))
    monkeypatch.setattr(module, "_resolve_path_trw_mcp_version", lambda: "5.0.0")
    monkeypatch.setattr(module.shutil, "which", lambda name, *a, **k: str(link) if name == "trw-mcp" else None)

    assert not any("shadowing" in w.lower() for w in _restart_warnings(module, tmp_path))


def test_restart_mcp_servers_strips_a_stale_trw_memory_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-INFRA-192 FR12: trw-memory's version is no longer stamped into
    VERSION.yaml at all (the manifest's ``packages`` map is authoritative now) —
    an old stamp left by a prior installer is stripped, not refreshed."""
    module = _load()
    _seed_deployment_for_restart(tmp_path)
    stamp_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
    stamp_path.write_text("trw_memory_version: 1.0.0\nkeep_me: yes\n", encoding="utf-8")
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", "5.0.0")
    monkeypatch.setattr(module, "_resolve_path_trw_mcp_version", lambda: "5.0.0")

    _restart_warnings(module, tmp_path)

    stamp = stamp_path.read_text(encoding="utf-8")
    assert "trw_mcp_version" not in stamp and "trw_memory_version" not in stamp and "keep_me: yes" in stamp, stamp


# ── I3 (installer refinement): trw-memory has its OWN version ──────────────────
# TRW_VERSION is trw-mcp's version; trw-memory versions independently (2.0.0
# shipped beside trw-mcp 5.0.0). The memory guard compared the installed
# trw-memory against trw-mcp's version and the log printed that version for it.


def test_memory_guard_compares_against_the_bundled_memory_wheel_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load()
    monkeypatch.setattr(module, "TRW_VERSION", "5.0.0")
    installed = {"trw-mcp": "5.0.0", "trw-memory": "3.0.0"}
    monkeypatch.setattr(module, "_probe_installed_version", lambda _python, pkg, *_a: installed[pkg])
    monkeypatch.setattr(module, "_probe_mcp_binary", lambda _python: "/probed/bin/trw-mcp")
    observed: list[list[str]] = []

    def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> SimpleNamespace:
        observed.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    ui = MagicMock()
    memory_whl = tmp_path / "trw_memory-2.0.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-5.0.0-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")

    module.phase_install_packages(ui, 2, 4, sys.executable, memory_whl, mcp_whl)

    printed = [str(call.args[0]) for method in ("info", "start_spinner") for call in getattr(ui, method).call_args_list]
    printed += [str(call.args[1]) for call in ui.stop_spinner.call_args_list if len(call.args) > 1]
    memory_lines = [line for line in printed if "trw-memory" in line]
    assert any("installed=3.0.0" in line and "bundled=2.0.0" in line for line in memory_lines), memory_lines
    assert not any("5.0.0" in line and "trw-memory v" in line for line in printed), printed
    assert not any(str(memory_whl) in " ".join(cmd) for cmd in observed), "the newer trw-memory was reinstalled"
    assert module._MCP_TARGET_BINARY == "/probed/bin/trw-mcp", "the shadow check needs the resident binary"


def test_a_record_naming_no_script_leaves_the_binary_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only RECORD proves ownership; a same-named script in the scripts dir proves nothing."""
    module = _load()
    dist_info = tmp_path / "trw_mcp-5.0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\nName: trw-mcp\nVersion: 5.0.0\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    venv = tmp_path / "venv"
    import subprocess as sp

    sp.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    (venv / "bin" / "trw-mcp").write_text(f"#!{venv / 'bin' / 'python'}\n", encoding="utf-8")

    assert module._probe_mcp_binary(str(venv / "bin" / "python")) is None


def test_restart_mcp_servers_warns_when_identity_cannot_be_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed comparison is 'unverified', never a silent match."""
    module = _load()
    _seed_deployment_for_restart(tmp_path)
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", "5.0.0")
    monkeypatch.setattr(module, "_resolve_path_trw_mcp_version", lambda: "5.0.0")
    monkeypatch.setattr(module.shutil, "which", lambda name, *a, **k: "/on/path/trw-mcp" if name == "trw-mcp" else None)

    for target in (None, "/vanished/bin/trw-mcp"):
        monkeypatch.setattr(module, "_MCP_TARGET_BINARY", target)
        warnings = _restart_warnings(module, tmp_path)
        assert any("could not verify" in w.lower() and "/on/path/trw-mcp" in w for w in warnings), (target, warnings)
        assert not any("shadowing" in w.lower() for w in warnings), "unverified is not a proven shadow"


def test_a_failed_memory_pin_does_not_crash_or_reinstall_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused trw-memory pin must not corrupt the trw-mcp install decision.

    PRD-INFRA-192 FR12: this used to also assert a VERSION.yaml
    read-back of the resident trw-memory version; that stamp no longer exists
    (the manifest's ``packages`` map is authoritative), so the surviving
    invariant is that the failed pin does not force-reinstall trw-mcp.
    """
    module = _load()
    monkeypatch.setattr(module, "TRW_VERSION", "5.0.0")
    installed = {"trw-mcp": "4.0.0", "trw-memory": "1.0.0"}
    monkeypatch.setattr(module, "_probe_installed_version", lambda _python, pkg, *_a: installed[pkg])
    monkeypatch.setattr(module, "_probe_mcp_binary", lambda _python: "/probed/bin/trw-mcp")
    mcp_installs: list[str] = []

    def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> SimpleNamespace:
        joined = " ".join(str(c) for c in cmd)
        if "trw_memory-2.0.0" in joined and "trw_mcp-5.0.0" not in joined:
            return SimpleNamespace(returncode=1, stdout="", stderr="pin refused")
        if "trw_mcp-5.0.0" in joined:
            installed["trw-mcp"] = "5.0.0"
            mcp_installs.append(joined)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    memory_whl = tmp_path / "trw_memory-2.0.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-5.0.0-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")

    module.phase_install_packages(MagicMock(), 2, 4, sys.executable, memory_whl, mcp_whl)

    assert mcp_installs, "trw-mcp must still be installed even when the memory pin was refused"
    assert module._MCP_EFFECTIVE_VERSION == "5.0.0"


def test_pip_target_is_probed_not_the_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """INFRA-192 r5 P0: a newer interpreter must not hide an older --pip-target from the guard."""
    module = _load()
    monkeypatch.setattr(module, "TRW_VERSION", "5.0.0")
    target = tmp_path / "target"
    resident = {
        None: {"trw-mcp": "9.0.0", "trw-memory": "9.0.0"},
        str(target): {"trw-mcp": "4.0.0", "trw-memory": "1.0.0"},
    }
    monkeypatch.setattr(module, "_probe_installed_version", lambda _python, pkg, where=None: resident[where][pkg])
    installs: list[str] = []

    def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> SimpleNamespace:
        joined = " ".join(str(c) for c in cmd)
        if "trw_mcp-5.0.0" in joined:
            installs.append(joined)
            resident[str(target)] = {"trw-mcp": "5.0.0", "trw-memory": "2.0.0"}
        return SimpleNamespace(returncode=0, stdout="trw_session_start", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    memory_whl = tmp_path / "trw_memory-2.0.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-5.0.0-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")

    module.phase_install_packages(MagicMock(), 2, 4, sys.executable, memory_whl, mcp_whl, pip_target=str(target))

    assert installs, "the older target was left in place because the interpreter held newer versions"
    assert module._MCP_EFFECTIVE_VERSION == "5.0.0"


def test_keeping_both_packages_stamps_the_read_back_not_the_guard_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INFRA-192 r6 P2 (1): the kept-both path reads back the runtime versions like the install path."""
    module = _load()
    monkeypatch.setattr(module, "TRW_VERSION", "5.0.0")
    answers = iter(["9.0.0", "9.0.0", None, None])
    monkeypatch.setattr(module, "_probe_installed_version", lambda *_a: next(answers))
    monkeypatch.setattr(module, "_verify_package_imports", lambda *_a: {})
    monkeypatch.setattr(module, "_probe_mcp_binary", lambda _python: None)
    memory_whl = tmp_path / "trw_memory-2.0.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-5.0.0-py3-none-any.whl"

    module.phase_install_packages(MagicMock(), 2, 4, sys.executable, memory_whl, mcp_whl)

    assert module._MCP_EFFECTIVE_VERSION is None


def test_a_failed_mcp_read_back_omits_the_mcp_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """INFRA-192 r6 P2 (2): an unverified trw-mcp version is never stamped from the bundle constant."""
    import json

    module = _load()
    _seed_deployment_for_restart(tmp_path)
    stamp_path = tmp_path / ".trw" / "frameworks" / "VERSION.yaml"
    stamp_path.write_text("trw_mcp_version: 1.0.0\nkeep_me: yes\n", encoding="utf-8")
    monkeypatch.setattr(module, "TRW_VERSION", "5.0.0")
    monkeypatch.setattr(module, "_MCP_EFFECTIVE_VERSION", None)
    monkeypatch.setattr(module, "_resolve_path_trw_mcp_version", lambda: None)

    warnings = _restart_warnings(module, tmp_path)

    stamp = stamp_path.read_text(encoding="utf-8")
    assert "trw_mcp_version" not in stamp and "keep_me: yes" in stamp, stamp
    sentinel = json.loads((tmp_path / ".trw" / "installed-version.json").read_text(encoding="utf-8"))
    assert "5.0.0" not in sentinel.values() and "version" not in sentinel, sentinel
    assert not any("5.0.0" in w for w in warnings), warnings


def _dist(site: Path, name: str, version: str) -> None:
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")


def test_the_real_probe_prefers_the_pip_target_over_the_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INFRA-192 r6 P2 (3): conflicting metadata on both paths; the unmocked probe reads the target."""
    module = _load()
    interpreter_site, target = tmp_path / "interpreter-site", tmp_path / "target"
    for package, (old, new) in {"trw-mcp": ("4.0.0", "9.0.0"), "trw-memory": ("1.0.0", "9.0.0")}.items():
        _dist(interpreter_site, package, new)
        _dist(target, package, old)
    monkeypatch.setenv("PYTHONPATH", str(interpreter_site))

    assert module._probe_installed_version(sys.executable, "trw-mcp") == "9.0.0", "the interpreter path is visible"
    assert module._probe_installed_version(sys.executable, "trw-mcp", str(target)) == "4.0.0"
    assert module._probe_installed_version(sys.executable, "trw-memory", str(target)) == "1.0.0"


def test_post_install_lines_report_the_read_back_memory_version_not_the_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-INFRA-192 FR03: after the pip step, every line reports what the target interpreter holds.

    The combined install "succeeds" but trw-memory stays at 1.0.0 and the
    bundled-wheel pin is refused, so the pinned wheel (2.0.0) is intent only.
    No line after the install may present 2.0.0 as installed or pinned, and
    one line must name the read-back 1.0.0.
    """
    module = _load()
    monkeypatch.setattr(module, "TRW_VERSION", "5.0.0")
    installed = {"trw-mcp": "4.0.0", "trw-memory": "1.0.0"}
    monkeypatch.setattr(module, "_probe_installed_version", lambda _python, pkg, *_a: installed[pkg])
    monkeypatch.setattr(module, "_probe_mcp_binary", lambda _python: "/probed/bin/trw-mcp")

    def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> SimpleNamespace:
        joined = " ".join(str(c) for c in cmd)
        if "trw_memory-2.0.0" in joined and "trw_mcp-5.0.0" not in joined:
            return SimpleNamespace(returncode=1, stdout="", stderr="pin refused")
        if "trw_mcp-5.0.0" in joined:
            installed["trw-mcp"] = "5.0.0"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    memory_whl = tmp_path / "trw_memory-2.0.0-py3-none-any.whl"
    mcp_whl = tmp_path / "trw_mcp-5.0.0-py3-none-any.whl"
    memory_whl.write_bytes(b"stub")
    mcp_whl.write_bytes(b"stub")
    lines: list[str] = []
    ui = MagicMock()
    for method in ("info", "step_ok", "step_warn"):
        getattr(ui, method).side_effect = lambda msg, *_a: lines.append(str(msg))
    ui.stop_spinner.side_effect = lambda _ok, msg="", *_a: lines.append(str(msg))

    module.phase_install_packages(ui, 2, 4, sys.executable, memory_whl, mcp_whl)

    # The guard's "decision=" line is printed before the install and labels itself as the plan.
    claims = [line for line in lines if "2.0.0" in line and "decision=" not in line and "pin failed" not in line]
    assert claims == [], f"post-install lines must not present the pinned wheel as installed: {claims}"
    assert any("trw-memory 1.0.0" in line for line in lines), lines
    assert any("trw-mcp 5.0.0" in line for line in lines), lines
