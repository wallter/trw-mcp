"""Bootstrap rung-resolution regressions (2026-09-07 independent review).

Companion to ``test_install_sh_flow.py``: that module proves the PEP 668 ladder
picks the right rung; this one proves each rung reports its OUTCOME and its
INSTALLED VERSION truthfully. Three defects of the same shape lived here — a
best-effort lookup decided a load-bearing answer:

* ``_install_via_uv`` ended on ``[ -x "$uvbin/trw-mcp" ] && …``, so a launcher
  outside ``uv tool dir --bin`` turned a SUCCESSFUL install into "Could not
  install trw-mcp";
* ``_verify_no_stale_shadow`` captured ``trw-mcp --version`` under
  ``set -euo pipefail``, so a broken shadow (the archetypal one) killed the
  installer silently;
* the pipx rung left the fresh version unset, so the guard read it from the
  bootstrap interpreter pipx never touched and INVERTED its own warning.

Every test runs the real bootstrap with stubs on PATH and asserts on observed
output/exit status; the shared stub helpers come from ``test_install_sh_flow``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.test_install_sh_flow import (
    _CURL_STUB,
    _PYTHON3_VENV_FRESH_STUB,
    _REPO_BOOTSTRAP,
    _SERVED_BOOTSTRAP,
    _write_stub,
)

# ── S5: a successful `uv tool install` whose launcher is NOT under `uv tool dir --bin` ──
#
# `_install_via_uv` ended on `[ -x "$uvbin/trw-mcp" ] && TRW_FRESH_VERSION=…`,
# so the function's exit status WAS that existence test. Older uv has no
# `--bin` subcommand (and a custom UV_TOOL_BIN_DIR puts the launcher elsewhere),
# in which case the rung returned 1 after a SUCCESSFUL install and the bootstrap
# dead-ended at "Could not install trw-mcp".

# python3 stub: PEP 668 pip AND no working `-m venv`, so the ladder must reach uv.
_PYTHON3_NO_PIP_NO_VENV_STUB = """#!/usr/bin/env bash
args="$*"
case "$args" in
  *'print(f"'*)                         echo "3.12" ;;
  *'print(sys.version_info.major)'*)    echo "3" ;;
  *'print(sys.version_info.minor)'*)    echo "12" ;;
  *"-m venv "*)                         exit 1 ;;   # no python3-venv / ensurepip
  *"importlib.metadata"*)               echo "" ;;
  *"-m pip install"*)                   exit 1 ;;   # externally managed (PEP 668)
  *"-m pip"*)                           exit 1 ;;
  *"-m trw_mcp.server"*)                exit 1 ;;
  *)                                    exit 0 ;;
esac
exit 0
"""

# uv stub: `tool install` succeeds and drops the launcher into TRW_TEST_UVBIN,
# but `uv tool dir [--bin]` is unsupported (older uv) — exactly the case where
# the old code returned 1 after a successful install.
_UV_STUB_NO_TOOL_DIR = """#!/usr/bin/env bash
echo "uv $*" >> "$TRW_TEST_MARKERS/uv_calls"
case "$1 $2" in
  "tool install")
      mkdir -p "$TRW_TEST_UVBIN"
      cp "$TRW_TEST_TRWMCP_SRC" "$TRW_TEST_UVBIN/trw-mcp"
      chmod +x "$TRW_TEST_UVBIN/trw-mcp"
      exit 0 ;;
  "tool dir") exit 1 ;;
esac
exit 0
"""

_TRW_MCP_VERSIONED_STUB = """#!/usr/bin/env bash
[ "$1" = "--version" ] && { echo "trw-mcp 9.9.9"; exit 0; }
echo "trw-mcp $*" >> "$TRW_TEST_MARKERS/trw_mcp_calls"
exit 0
"""


def test_served_bootstrap_uv_rung_succeeds_when_launcher_is_not_under_uv_tool_dir(tmp_path: Path) -> None:
    """`uv tool install` succeeded, so the rung must report success — the
    launcher's location is a version-discovery detail, not the install result."""
    stub_bin = tmp_path / "bin"
    uvbin = tmp_path / "custom-uv-bin"  # NOT what `uv tool dir --bin` would say
    markers = tmp_path / "markers"
    project = tmp_path / "project"
    home = tmp_path / "home"
    for d in (stub_bin, uvbin, markers, project, home):
        d.mkdir(parents=True)

    _write_stub(stub_bin / "python3", _PYTHON3_NO_PIP_NO_VENV_STUB)
    _write_stub(stub_bin / "curl", _CURL_STUB)
    _write_stub(stub_bin / "uv", _UV_STUB_NO_TOOL_DIR)
    _write_stub(tmp_path / "trw-mcp-src", _TRW_MCP_VERSIONED_STUB)
    (project / ".git").mkdir()

    env = {
        "PATH": f"{stub_bin}:{uvbin}:/usr/bin:/bin",  # no pipx anywhere
        "HOME": str(home),
        "TRW_TEST_MARKERS": str(markers),
        "TRW_TEST_UVBIN": str(uvbin),
        "TRW_TEST_TRWMCP_SRC": str(tmp_path / "trw-mcp-src"),
        "TRW_ALLOW_SYSTEM_PYTHON": "false",
        "TERM": "dumb",
    }
    result = subprocess.run(
        ["bash", str(_SERVED_BOOTSTRAP), "--allow-unauthenticated"],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = result.stdout + result.stderr

    assert "uv tool install" in (markers / "uv_calls").read_text(encoding="utf-8")
    assert "trw-mcp installed (uv tool)" in output, f"the uv rung reported failure.\n--- output ---\n{output}"
    assert "Could not install trw-mcp" not in output, output
    assert result.returncode == 0, output
    assert "Open-source package installed" in output, output


# ── S6: a shadow whose `--version` FAILS must not kill the installer ─────────

# The archetypal stale shadow is a BROKEN install (missing deps), so `--version`
# exits non-zero. Under `set -euo pipefail` the guard's version capture then
# failed and terminated the whole script — silently, mid-run, after a successful
# install.
_TRW_MCP_BROKEN_SHADOW_STUB = """#!/usr/bin/env bash
if [ "$1" = "--version" ]; then
    echo "ModuleNotFoundError: No module named 'mcp'" >&2
    exit 1
fi
echo "trw-mcp $*" >> "$TRW_TEST_MARKERS/trw_mcp_calls"
exit 0
"""


def test_served_bootstrap_survives_a_shadow_whose_version_command_fails(tmp_path: Path) -> None:
    """A broken trw-mcp first on PATH must produce a 'cannot determine' warning
    and let the install finish — not abort the bootstrap with no explanation."""
    stub_bin = tmp_path / "bin"
    markers = tmp_path / "markers"
    project = tmp_path / "project"
    home = tmp_path / "home"
    localbin = home / ".local" / "bin"
    for d in (stub_bin, markers, project, localbin):
        d.mkdir(parents=True)

    _write_stub(stub_bin / "python3", _PYTHON3_VENV_FRESH_STUB)
    _write_stub(stub_bin / "curl", _CURL_STUB)
    _write_stub(stub_bin / "trw-mcp", _TRW_MCP_BROKEN_SHADOW_STUB)
    (project / ".git").mkdir()

    env = {
        "PATH": f"{stub_bin}:{localbin}:/usr/bin:/bin",
        "HOME": str(home),
        "TRW_TEST_MARKERS": str(markers),
        "TRW_ALLOW_SYSTEM_PYTHON": "false",
        "TERM": "dumb",
    }
    result = subprocess.run(
        ["bash", str(_SERVED_BOOTSTRAP), "--allow-unauthenticated"],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = result.stdout + result.stderr

    assert "does not report a version" in output, f"the guard did not degrade honestly.\n--- output ---\n{output}"
    assert result.returncode == 0, f"an unreadable shadow version killed the installer.\n--- output ---\n{output}"
    assert "Open-source package installed" in output, output


# ── S8: the fresh version must come from the interpreter that OWNS the install ──
#
# The pipx rung left TRW_FRESH_VERSION empty, so the shadow guard fell back to
# `_pkg_ver "$PYTHON"` — the bootstrap interpreter pipx deliberately never
# touches. When that interpreter held an OLD trw-mcp, the warning INVERTED:
# "stale <new> shadowing freshly-installed <old>".

# python3 stub: PEP 668 pip, and importlib.metadata reports an OLD trw-mcp — the
# unrelated copy that used to be mistaken for "what we just installed".
_PYTHON3_PEP668_OLD_METADATA_STUB = """#!/usr/bin/env bash
args="$*"
case "$args" in
  *'print(f"'*)                         echo "3.12" ;;
  *'print(sys.version_info.major)'*)    echo "3" ;;
  *'print(sys.version_info.minor)'*)    echo "12" ;;
  *"importlib.metadata"*)               echo "0.0.1" ;;
  *"-m pip install"*)                   exit 1 ;;
  *"-m pip"*)                           exit 1 ;;
  *"-m trw_mcp.server"*)                exit 1 ;;
  *)                                    exit 0 ;;
esac
exit 0
"""

# pipx stub distinguishing the two `pipx environment` values the bootstraps read.
_PIPX_VENVS_STUB = """#!/usr/bin/env bash
case "$1" in
  environment)
    case "$3" in
      PIPX_LOCAL_VENVS) echo "$TRW_TEST_PIPX_VENVS" ;;
      *)                echo "$TRW_TEST_PIPX_BINDIR" ;;
    esac ;;
  ensurepath)  : > "$TRW_TEST_MARKERS/pipx_ensurepath" ;;
  install)     echo "install $*" >> "$TRW_TEST_MARKERS/pipx_install" ;;
  inject)      echo "inject $*" >> "$TRW_TEST_MARKERS/pipx_inject" ;;
  upgrade)     echo "upgrade $*" >> "$TRW_TEST_MARKERS/pipx_upgrade" ;;
esac
exit 0
"""

# The interpreter INSIDE the pipx venv — the one that actually owns the install.
_PIPX_VENV_PYTHON_STUB = """#!/usr/bin/env bash
case "$*" in
  *"importlib.metadata"*) echo "9.9.9" ;;
esac
exit 0
"""


@pytest.mark.parametrize("bootstrap", [_SERVED_BOOTSTRAP, _REPO_BOOTSTRAP], ids=["served", "repo"])
def test_pipx_rung_reads_the_fresh_version_from_the_pipx_venv_not_the_bootstrap_python(
    bootstrap: Path, tmp_path: Path
) -> None:
    """pipx installs 9.9.9 into its own venv; the bootstrap interpreter still
    holds an unrelated 0.0.1. The PATH-resolved trw-mcp IS the fresh 9.9.9, so
    there is no shadow — and the guard must not invent one (it used to print
    'stale 9.9.9 shadowing freshly-installed 0.0.1')."""
    stub_bin = tmp_path / "bin"
    pipx_bindir = tmp_path / "pipx-bin"
    pipx_venvs = tmp_path / "pipx-venvs"
    markers = tmp_path / "markers"
    project = tmp_path / "project"
    home = tmp_path / "home"
    for d in (stub_bin, pipx_bindir, markers, project, home):
        d.mkdir(parents=True)
    (pipx_venvs / "trw-mcp" / "bin").mkdir(parents=True)

    _write_stub(stub_bin / "python3", _PYTHON3_PEP668_OLD_METADATA_STUB)
    _write_stub(stub_bin / "pipx", _PIPX_VENVS_STUB)
    _write_stub(stub_bin / "curl", _CURL_STUB)
    _write_stub(pipx_venvs / "trw-mcp" / "bin" / "python", _PIPX_VENV_PYTHON_STUB)
    _write_stub(pipx_bindir / "trw-mcp", _TRW_MCP_VERSIONED_STUB)
    (project / ".git").mkdir()

    env = {
        "PATH": f"{stub_bin}:/usr/bin:/bin",
        "HOME": str(home),
        "TRW_TEST_MARKERS": str(markers),
        "TRW_TEST_PIPX_BINDIR": str(pipx_bindir),
        "TRW_TEST_PIPX_VENVS": str(pipx_venvs),
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
    output = result.stdout + result.stderr

    assert "trw-mcp installed (pipx)" in output, output
    assert "stale trw-mcp is first on your PATH" not in output, (
        f"the shadow guard compared against the bootstrap interpreter, not the pipx venv.\n--- output ---\n{output}"
    )
    assert "0.0.1" not in output, f"the unrelated bootstrap-interpreter version leaked into the guard.\n{output}"
    assert result.returncode == 0, output
