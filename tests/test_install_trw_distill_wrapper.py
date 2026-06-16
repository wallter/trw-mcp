"""Behavioral tests for the proprietary console-script PYTHONPATH wrappers.

Under a ``--target`` (pip or uv) install the backend-generated bin scripts do
``from <module> import main`` with no PYTHONPATH setup, so the bare
``trw-distill``/``trw-loop``/``trw-swarm`` commands that trw-mcp emits as
remediation/regenerate hints fail to resolve their module. The installer
re-writes them as PYTHONPATH wrappers mirroring ``bin/trw-mcp``. These tests
verify that behavior against BOTH the template and the built artifact.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._install_trw_pip_target_contract_support import (
    _INSTALLER_PATHS,
    _load_installer_module,
)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_writes_pythonpath_wrappers_for_installed_console_scripts(installer_path: Path, tmp_path: Path) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    target = str(tmp_path / "trw-pip")
    python = "/opt/py/bin/python3"
    installed = ["trw-distill 0.3.0", "trw-metaharness 0.1.5", "trw-loop 0.1.1"]

    written = module._write_proprietary_console_wrappers(python, target, installed, ui)

    bin_dir = Path(target) / "bin"
    distill = bin_dir / "trw-distill"
    loop = bin_dir / "trw-loop"
    # trw-distill + trw-loop installed AND ship console scripts -> wrappers.
    # trw-metaharness installed but ships NO console script -> no wrapper.
    # trw-swarm NOT installed -> no wrapper.
    assert set(written) == {distill, loop}
    assert not (bin_dir / "trw-swarm").exists()
    assert not (bin_dir / "trw-metaharness").exists()

    assert distill.read_text(encoding="utf-8") == (
        "#!/bin/bash\n"
        f"export PYTHONPATH={target}:$PYTHONPATH\n"
        f'exec {python} -B -c "from trw_distill.cli import main; main()" "$@"\n'
    )
    assert loop.read_text(encoding="utf-8") == (
        "#!/bin/bash\n"
        f"export PYTHONPATH={target}:$PYTHONPATH\n"
        f'exec {python} -B -c "from trw_loop.cli import main; main()" "$@"\n'
    )
    # Executable bit set (mirrors bin/trw-mcp).
    assert distill.stat().st_mode & 0o111


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_no_wrappers_without_target_dir(installer_path: Path, tmp_path: Path) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()

    # Empty target_dir => normal site-packages install; backend creates the
    # console script on PATH directly, so the installer must NOT touch it.
    written = module._write_proprietary_console_wrappers("/opt/py/bin/python3", "", ["trw-distill 0.3.0"], ui)
    assert written == []


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_no_wrappers_when_package_not_installed(installer_path: Path, tmp_path: Path) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    target = str(tmp_path / "trw-pip")

    # A failed proprietary install (package absent from `installed`) must not
    # leave a wrapper that would shadow a later working copy.
    written = module._write_proprietary_console_wrappers(
        python="/opt/py/bin/python3", target_dir=target, installed=[], ui=ui
    )
    assert written == []
    assert not (Path(target) / "bin").exists()


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_console_script_table_excludes_harness_and_covers_clis(installer_path: Path) -> None:
    module = _load_installer_module(installer_path)
    table = {entry[0]: entry for entry in module.PROPRIETARY_CONSOLE_SCRIPTS}

    # trw-metaharness ships no console script -> intentionally absent.
    assert "trw-metaharness" not in table
    # The three CLI-shipping proprietary packages are covered with the right
    # ``module.callable`` import target.
    assert table["trw-distill"] == ("trw-distill", "trw-distill", "trw_distill.cli")
    assert table["trw-loop"] == ("trw-loop", "trw-loop", "trw_loop.cli")
    assert table["trw-swarm"] == ("trw-swarm", "trw-swarm", "trw_swarm.cli")
    # Every script-shipping package is in the install tuple.
    for package, _script, _target in module.PROPRIETARY_CONSOLE_SCRIPTS:
        assert package in module.PROPRIETARY_PACKAGES_TUPLE
