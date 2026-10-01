"""Proprietary installs never resolve a proprietary name from a public index (PROPRIETARY-PIPELINE-ROT item 5).

trw-distill 0.10.0 requires trw-llm. With ``--find-links`` plus the PyPI fallback, a proprietary dependency that is
missing from the downloaded set (or a higher-versioned squat on PyPI) would be resolved from the public index. The
installer therefore installs each downloaded wheel with ``--no-index --no-deps``, installs only the wheel's PUBLIC
requirements from the index, and refuses a wheel whose proprietary requirement is not among the downloaded wheels.
"""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests._install_trw_pip_target_contract_support import (
    _INSTALLER_PATHS,
    _load_installer_module,
)

_PIP = ["/opt/py/bin/python3", "-m", "pip", "install"]


def _wheel(directory: Path, name: str, version: str, requires: list[str]) -> Path:
    dist = name.replace("-", "_")
    path = directory / f"{dist}-{version}-py3-none-any.whl"
    meta = "".join(f"Requires-Dist: {r}\n" for r in requires)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"{dist}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{meta}"
        )
    return path


def _capture_runs(module, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    monkeypatch.setattr(module, "_INSTALL_BACKEND", ("pip", list(_PIP)))
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_trw_llm_is_distributed_and_installs_before_distill(installer_path: Path) -> None:
    module = _load_installer_module(installer_path)
    names = module.PROPRIETARY_PACKAGES_TUPLE
    assert "trw-llm" in names
    assert names.index("trw-llm") < names.index("trw-distill")


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_installs_with_no_index_and_only_public_requirements_reach_the_index(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    calls = _capture_runs(module, monkeypatch)
    wheel = _wheel(
        tmp_path,
        "trw-distill",
        "0.10.0",
        ["trw-llm>=0.1.0", "trw_metaharness>=0.1.5", "trw-memory>=5.1.0", 'jedi>=0.19; extra == "lsp"'],
    )

    assert module._install_proprietary_wheel(_PIP[0], wheel, "trw-distill", "", MagicMock())

    wheel_cmd, deps_cmd = calls
    assert str(wheel) in wheel_cmd
    assert "--no-index" in wheel_cmd
    assert "--no-deps" in wheel_cmd
    assert "--no-index" not in deps_cmd
    assert "--find-links" not in deps_cmd
    assert deps_cmd[-1:] == ["trw-memory>=5.1.0"]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_without_public_requirements_makes_one_call(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_installer_module(installer_path)
    calls = _capture_runs(module, monkeypatch)
    wheel = _wheel(tmp_path, "trw-llm", "0.1.0", [])

    assert module._install_proprietary_wheel(_PIP[0], wheel, "trw-llm", "", MagicMock())
    assert len(calls) == 1 and "--no-index" in calls[0]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_missing_proprietary_requirement_fails_closed(installer_path: Path, tmp_path: Path) -> None:
    module = _load_installer_module(installer_path)
    wheel = _wheel(tmp_path, "trw-distill", "0.10.0", ["trw-llm>=0.1.0", "trw-memory>=5.1.0"])

    assert module._missing_proprietary_requirements(wheel, {"trw-distill"}) == ["trw-llm"]
    assert module._missing_proprietary_requirements(wheel, {"trw-distill", "trw-llm"}) == []


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_failed_prerequisite_install_refuses_its_dependents(
    installer_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex DISTILL-0.10 r1 KI: a private dependency that failed to install must not count as available."""
    module = _load_installer_module(installer_path)
    wheels = {
        "trw-llm": _wheel(tmp_path, "trw-llm", "0.1.0", []),
        "trw-metaharness": _wheel(tmp_path, "trw-metaharness", "0.1.5", []),
        "trw-distill": _wheel(tmp_path, "trw-distill", "0.10.0", ["trw-llm>=0.1.0", "trw-metaharness>=0.1.5"]),
        "trw-loop": _wheel(tmp_path, "trw-loop", "0.3.7", []),
        "trw-swarm": _wheel(tmp_path, "trw-swarm", "0.5.0", []),
    }
    versions = {name: path.name.split("-")[1] for name, path in wheels.items()}
    monkeypatch.setattr(
        module,
        "_post_proprietary_entitlement",
        lambda _url, _key, pkg, _ver: {"version": versions[pkg], "sha256": "x", "url": f"https://h/{pkg}"},
    )
    monkeypatch.setattr(module, "_download_proprietary_wheel", lambda url, *_a: wheels[url.rsplit("/", 1)[1]])
    attempted: list[str] = []

    def fake_install(_python, _wheel_path, package, *_a, **_k):
        attempted.append(package)
        return package != "trw-llm"

    monkeypatch.setattr(module, "_install_proprietary_wheel", fake_install)
    monkeypatch.setattr(module, "_emit_install_completed_event", lambda *_a: None)
    monkeypatch.setattr(module, "_write_proprietary_console_wrappers", lambda *_a: [])

    installed = module.phase_install_proprietary(
        MagicMock(), 1, 1, _PIP[0], "key", {}, "https://b", auto_confirm=True, project_dir=tmp_path
    )

    names = [entry.split(" ", 1)[0] for entry in installed]
    assert "trw-distill" not in names and "trw-distill" not in attempted
    assert names == ["trw-metaharness", "trw-loop", "trw-swarm"]
