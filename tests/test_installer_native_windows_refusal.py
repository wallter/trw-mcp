"""Every installer refuses native Windows and names WSL2 (7.0.0 drops the Windows claim).

The memory store's file-safety checks need POSIX, so a native Windows install would
break at first use. The two bash bootstraps run under Git Bash, MSYS2 or Cygwin there,
and ``install-trw.py`` under a Windows CPython; each must stop before installing.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._layout import requires_monorepo

_REPO = Path(__file__).resolve().parents[2]
_BOOTSTRAPS = (_REPO / "scripts" / "install.sh", _REPO / "platform" / "public" / "install.sh")
_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"

_UNAME_STUB = '#!/bin/sh\necho "$TRW_TEST_UNAME"\n'
_RECORDING_STUB = '#!/bin/sh\necho "$0 $*" >> "$TRW_TEST_MARKERS/called"\nexit 0\n'


# The bash bootstraps live outside the package, so the public trw-mcp layout has neither.
@requires_monorepo
@pytest.mark.parametrize("bootstrap", _BOOTSTRAPS, ids=["repo", "served"])
@pytest.mark.parametrize("uname", ["MINGW64_NT-10.0-19045", "MSYS_NT-10.0-19045", "CYGWIN_NT-10.0-19045", "Windows_NT"])
def test_a_bash_bootstrap_refuses_native_windows_before_installing(bootstrap: Path, uname: str, tmp_path: Path) -> None:
    stub_bin = tmp_path / "bin"
    markers = tmp_path / "markers"
    stub_bin.mkdir()
    markers.mkdir()
    (stub_bin / "uname").write_text(_UNAME_STUB, encoding="utf-8")
    for tool in ("python3", "python", "pipx", "uv", "curl", "trw-mcp"):
        (stub_bin / tool).write_text(_RECORDING_STUB, encoding="utf-8")
    for stub in stub_bin.iterdir():
        stub.chmod(0o755)

    result = subprocess.run(
        ["bash", str(bootstrap), "--allow-unauthenticated"],
        cwd=str(tmp_path),
        env={
            "PATH": f"{stub_bin}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TERM": "dumb",
            "TRW_TEST_UNAME": uname,
            "TRW_TEST_MARKERS": str(markers),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1
    assert "Native Windows is not supported" in result.stderr
    assert "WSL2" in result.stderr
    assert not (markers / "called").exists(), "the bootstrap ran an install tool before refusing"


def _load_template() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_windows_refusal", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_install_trw_refuses_native_windows_before_parsing_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _load_template()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["install-trw.py", "--script"])

    with pytest.raises(SystemExit) as refused:
        installer.main()

    assert isinstance(refused.value.code, str)
    assert "native Windows is not supported" in refused.value.code
    assert "WSL2" in refused.value.code
