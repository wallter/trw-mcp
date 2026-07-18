"""Functional (subprocess) harness for the curl|bash bootstrap PEP 668 pipx rung.

The grep-level wiring guards in ``test_installer_api_key_validation.py`` prove the
pipx rung *exists* in the ladder, but they cannot prove it *runs*: a flipped
``||``/``&&``, a deleted PATH helper, or a broken ``pipx ensurepath`` would ship
undetected. This harness actually EXECUTES both bootstraps
(``platform/public/install.sh`` and ``scripts/install.sh``) with stubbed
``python3`` / ``pipx`` / ``trw-mcp`` / ``curl`` on PATH, simulates a PEP 668
externally-managed Python (the ``python3`` stub's ``-m pip install`` exits
non-zero), and asserts the bootstrap:

  1. falls through the pip / --user rungs and INVOKES ``pipx install trw-mcp``;
  2. runs ``pipx ensurepath`` to persist the bin dir (Codex MEDIUM / finding 2);
  3. adds the pipx bin dir to PATH — proven behaviorally: the ``trw-mcp`` stub
     lives ONLY in the (off-PATH) pipx bin dir, so a later ``trw-mcp
     init-project`` only resolves if the bin dir was prepended to PATH;
  4. flows on to a clean success exit (``--allow-unauthenticated`` open-source
     path).

These complement — do not replace — the grep guards (finding 4: "keep the grep
guards too").
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent.parent
_SERVED_BOOTSTRAP = _REPO_ROOT / "platform" / "public" / "install.sh"
_REPO_BOOTSTRAP = _REPO_ROOT / "scripts" / "install.sh"

# python3 stub: passes the 3.10+ version gate but REFUSES every pip install
# (PEP 668) and the `-m trw_mcp.server` fallback (so the PATH-resolved trw-mcp
# from the pipx bin dir is the only thing that can succeed downstream).
_PYTHON3_STUB = """#!/usr/bin/env bash
args="$*"
case "$args" in
  *'print(f"'*)                         echo "3.12" ;;
  *'print(sys.version_info.major)'*)    echo "3" ;;
  *'print(sys.version_info.minor)'*)    echo "12" ;;
  *'-m pip install'*)                   exit 1 ;;   # externally managed (PEP 668)
  *'-m pip'*)                           exit 1 ;;
  *'-m trw_mcp.server'*)                exit 1 ;;   # force PATH-resolved trw-mcp to win
  *)                                    exit 0 ;;
esac
exit 0
"""

# pipx stub: records install / ensurepath invocations and reports its bin dir.
_PIPX_STUB = """#!/usr/bin/env bash
case "$1" in
  environment) echo "$TRW_TEST_PIPX_BINDIR" ;;
  ensurepath)  : > "$TRW_TEST_MARKERS/pipx_ensurepath" ;;
  install)     echo "install $*" >> "$TRW_TEST_MARKERS/pipx_install" ;;
  upgrade)     echo "upgrade $*" >> "$TRW_TEST_MARKERS/pipx_upgrade" ;;
esac
exit 0
"""

# trw-mcp stub: lives ONLY in the pipx bin dir (off the initial PATH). Any
# invocation records a marker — its presence proves the bin dir reached PATH.
_TRW_MCP_STUB = """#!/usr/bin/env bash
echo "trw-mcp $*" >> "$TRW_TEST_MARKERS/trw_mcp_calls"
exit 0
"""

_CURL_STUB = """#!/usr/bin/env bash
exit 0
"""


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_bootstrap(bootstrap: Path, tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    stub_bin = tmp_path / "bin"
    pipx_bindir = tmp_path / "pipx-bin"  # deliberately NOT on the initial PATH
    markers = tmp_path / "markers"
    project = tmp_path / "project"
    home = tmp_path / "home"
    for d in (stub_bin, pipx_bindir, markers, project, home):
        d.mkdir(parents=True)

    _write_stub(stub_bin / "python3", _PYTHON3_STUB)
    _write_stub(stub_bin / "pipx", _PIPX_STUB)
    _write_stub(stub_bin / "curl", _CURL_STUB)
    # trw-mcp resolves ONLY via the pipx bin dir once it is added to PATH.
    _write_stub(pipx_bindir / "trw-mcp", _TRW_MCP_STUB)
    # A git repo (dir sentinel) so the open-source init path runs.
    (project / ".git").mkdir()

    env = {
        "PATH": f"{stub_bin}:/usr/bin:/bin",
        "HOME": str(home),
        "TRW_TEST_MARKERS": str(markers),
        "TRW_TEST_PIPX_BINDIR": str(pipx_bindir),
        # Keep the destructive --break-system-packages rung disabled so the pipx
        # rung is the one that must fire.
        "TRW_ALLOW_SYSTEM_PYTHON": "false",
        "TERM": "dumb",
    }
    result = subprocess.run(
        ["bash", str(bootstrap), "--allow-unauthenticated"],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, markers


@pytest.mark.parametrize(
    "bootstrap, invokes_trw_mcp",
    [
        pytest.param(_SERVED_BOOTSTRAP, True, id="served"),
        # The repo bootstrap's --allow-unauthenticated path stops at the
        # open-source banner without calling trw-mcp, so only the served
        # bootstrap exercises the end-to-end PATH-resolution proof (rung 3).
        pytest.param(_REPO_BOOTSTRAP, False, id="repo"),
    ],
)
def test_bootstrap_pep668_uses_pipx_and_persists_path_and_succeeds(
    bootstrap: Path, invokes_trw_mcp: bool, tmp_path: Path
) -> None:
    if not Path("/bin/bash").exists() and not Path("/usr/bin/bash").exists():
        pytest.skip("bash unavailable")

    result, markers = _run_bootstrap(bootstrap, tmp_path)
    output = result.stdout + result.stderr

    # 1. The pipx rung actually fired (not just present in the source).
    assert (markers / "pipx_install").is_file(), f"pipx install was never invoked.\n--- output ---\n{output}"
    install_log = (markers / "pipx_install").read_text(encoding="utf-8")
    assert "trw-mcp" in install_log
    assert "trw-mcp installed (pipx)" in output, output

    # 2. PATH persistence: pipx ensurepath ran (finding 2).
    assert (markers / "pipx_ensurepath").is_file(), f"pipx ensurepath was never invoked.\n--- output ---\n{output}"

    # 3. The pipx bin dir was added to PATH — proven behaviorally where the
    #    bootstrap goes on to invoke trw-mcp: the stub lives ONLY in the off-PATH
    #    pipx bin dir, so its resolution means the dir was prepended to PATH.
    if invokes_trw_mcp:
        assert (markers / "trw_mcp_calls").is_file(), (
            f"trw-mcp never resolved — the pipx bin dir was NOT added to PATH.\n--- output ---\n{output}"
        )
        assert "init-project" in (markers / "trw_mcp_calls").read_text(encoding="utf-8")

    # 4. Honest warning printed because the bin dir was not on the inherited PATH.
    assert "was not on your PATH" in output, output

    # 5. Success flows on to a clean exit.
    assert result.returncode == 0, f"bootstrap did not exit cleanly.\n--- output ---\n{output}"
    assert "Open-source package installed" in output, output


# ── Regression harness for the authenticated (--api-key) path ────────────────
#
# Exercises scripts/install.sh's AUTH_OK=true branch, where the telemetry-consent
# handling (fix 1) and the init-project success-guard (fix 2) live. pip install
# is stubbed to SUCCEED (first rung) so the ladder is not the subject here; the
# `trw-mcp` binary stub resolves on the base PATH and drives init-project.

# python3 stub: passes the 3.10+ gate, lets `-m pip install` succeed, and fails
# the `-m trw_mcp.server` fallbacks so the PATH `trw-mcp` binary owns the run.
_PYTHON3_PIP_OK_STUB = """#!/usr/bin/env bash
args="$*"
case "$args" in
  *'print(f"'*)                        echo "3.12" ;;
  *'print(sys.version_info.major)'*)   echo "3" ;;
  *'print(sys.version_info.minor)'*)   echo "12" ;;
  *'-m pip install'*)                  exit 0 ;;   # pip install succeeds
  *'-m trw_mcp.server'*)               exit 1 ;;   # force PATH trw-mcp to own it
  *)                                   exit 0 ;;
esac
exit 0
"""

# trw-mcp stub on the base PATH: init-project honors TRW_TEST_INIT_FAIL so a test
# can drive the failure path (fix 2); everything else succeeds.
_TRW_MCP_INIT_STUB = """#!/usr/bin/env bash
echo "trw-mcp $*" >> "$TRW_TEST_MARKERS/trw_mcp_calls"
case "$1" in
  init-project) exit "${TRW_TEST_INIT_FAIL:-0}" ;;
  *)            exit 0 ;;
esac
"""


def _run_repo_authed(
    tmp_path: Path,
    extra_args: list[str],
    *,
    config_seed: str | None = None,
    init_fail: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    stub_bin = tmp_path / "bin"
    markers = tmp_path / "markers"
    project = tmp_path / "project"
    home = tmp_path / "home"
    for d in (stub_bin, markers, project, home):
        d.mkdir(parents=True)

    _write_stub(stub_bin / "python3", _PYTHON3_PIP_OK_STUB)
    _write_stub(stub_bin / "trw-mcp", _TRW_MCP_INIT_STUB)
    (project / ".git").mkdir()
    if config_seed is not None:
        (project / ".trw").mkdir()
        (project / ".trw" / "config.yaml").write_text(config_seed, encoding="utf-8")

    env = {
        "PATH": f"{stub_bin}:/usr/bin:/bin",
        "HOME": str(home),
        "TRW_TEST_MARKERS": str(markers),
        "TERM": "dumb",
    }
    if init_fail:
        env["TRW_TEST_INIT_FAIL"] = "1"
    result = subprocess.run(
        ["bash", str(_REPO_BOOTSTRAP), "--api-key", "trw_testkey123", *extra_args],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, project


def test_repo_no_flag_keeps_prior_telemetry_optin(tmp_path: Path) -> None:
    """Fix 1: with no telemetry flag, a prior ``platform_telemetry_enabled: true``
    must be LEFT untouched and reported honestly (not falsely "disabled")."""
    result, project = _run_repo_authed(tmp_path, [], config_seed="platform_telemetry_enabled: true\n")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "keeping prior opt-in" in output, output
    # The config file is untouched — the opt-in remains in effect.
    assert "platform_telemetry_enabled: true" in (project / ".trw" / "config.yaml").read_text(encoding="utf-8")


def test_repo_no_telemetry_flag_disables_prior_optin(tmp_path: Path) -> None:
    """Fix 1: explicit --no-telemetry must actively flip a prior opt-in to false,
    not merely print a message that leaves the true line in place."""
    result, project = _run_repo_authed(tmp_path, ["--no-telemetry"], config_seed="platform_telemetry_enabled: true\n")
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Telemetry disabled (--no-telemetry)" in output, output
    config = (project / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert "platform_telemetry_enabled: false" in config, config
    assert "platform_telemetry_enabled: true" not in config, config


def test_repo_init_failure_suppresses_framework_success_step(tmp_path: Path) -> None:
    """Fix 2: when init-project fails, the green "Framework installed" success
    line must NOT print — only the warn. The old ||-chain printed both."""
    result, _project = _run_repo_authed(tmp_path, [], init_fail=True)
    output = result.stdout + result.stderr
    assert "Project init failed" in output, output
    assert "Framework installed (skills, agents, hooks, config)" not in output, output
