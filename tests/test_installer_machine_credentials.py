"""The installers reuse the computer-wide sign-in (``~/.trw/credentials.yaml``).

A project install on a computer that is already signed in must not run the device-code login
again, must not copy the computer-wide key into the project, and must refuse a machine store
that is not a private regular file. Covers both ``install.sh`` copies (their ``load_machine_key``
function, plus a full stubbed run of ``scripts/install.sh``) and the stdlib installer template.
"""

from __future__ import annotations

import importlib.util
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo
from tests.test_install_sh_flow import _PYTHON3_PIP_OK_STUB, _TRW_MCP_INIT_STUB, _write_stub

_REPO_ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
_BOOTSTRAPS = [_REPO_ROOT / "scripts" / "install.sh", _REPO_ROOT / "platform" / "public" / "install.sh"]
_TEMPLATE = PACKAGE_ROOT / "scripts" / "install-trw.template.py"
_KEY = "trw_dk_computer_wide_installer_secret"
_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner/mode bits")


def _seed_machine(home: Path, key: str = _KEY, mode: int = 0o600) -> Path:
    path = home / ".trw" / "credentials.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'# test\nplatform_api_key: "{key}"\n', encoding="utf-8")
    path.chmod(mode)
    return path


# ── install.sh: load_machine_key, both copies ─────────────────────────────────


def _machine_fn(bootstrap: Path) -> str:
    text = bootstrap.read_text(encoding="utf-8")
    m = re.search(r"^MACHINE_CREDENTIALS=.*?^load_machine_key\(\) \{\n.*?^\}\n", text, re.DOTALL | re.MULTILINE)
    assert m, f"{bootstrap} has no load_machine_key"
    return m.group(0)


def _probe(bootstrap: Path, home: Path) -> str:
    script = (
        'warn() { echo "WARN $*"; }\n'
        + _machine_fn(bootstrap)
        + ('load_machine_key\n[ "$MACHINE_KEY" = "$EXPECT" ] && echo MATCH || echo "NOMATCH len=${#MACHINE_KEY}"\n')
    )
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "EXPECT": _KEY}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30).stdout


@requires_monorepo
@_posix_only
@pytest.mark.parametrize("bootstrap", _BOOTSTRAPS, ids=lambda p: p.parent.name)
def test_bootstrap_reads_a_private_machine_store(bootstrap: Path, tmp_path: Path) -> None:
    _seed_machine(tmp_path)
    out = _probe(bootstrap, tmp_path)
    assert "MATCH" in out and "NOMATCH" not in out
    assert _KEY not in out


@requires_monorepo
@_posix_only
@pytest.mark.parametrize("bootstrap", _BOOTSTRAPS, ids=lambda p: p.parent.name)
def test_bootstrap_refuses_a_group_readable_machine_store(bootstrap: Path, tmp_path: Path) -> None:
    _seed_machine(tmp_path, mode=0o644)
    out = _probe(bootstrap, tmp_path)
    assert "NOMATCH len=0" in out
    assert "looser than 0600" in out


@requires_monorepo
@_posix_only
@pytest.mark.parametrize("bootstrap", _BOOTSTRAPS, ids=lambda p: p.parent.name)
def test_bootstrap_refuses_a_symlinked_machine_store(bootstrap: Path, tmp_path: Path) -> None:
    real = _seed_machine(tmp_path / "other")
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "credentials.yaml").symlink_to(real)
    out = _probe(bootstrap, tmp_path)
    assert "NOMATCH len=0" in out
    assert "not a regular file you own" in out


@requires_monorepo
@pytest.mark.parametrize("bootstrap", _BOOTSTRAPS, ids=lambda p: p.parent.name)
def test_bootstrap_with_no_machine_store_is_silent(bootstrap: Path, tmp_path: Path) -> None:
    out = _probe(bootstrap, tmp_path)
    assert out.strip() == "NOMATCH len=0"


@requires_monorepo
@_posix_only
def test_repo_install_with_machine_key_skips_device_login(tmp_path: Path) -> None:
    """End to end (stubbed): no project key + a machine key -> no `auth login`, key never in output."""
    stub_bin, markers, project, home = (tmp_path / d for d in ("bin", "markers", "project", "home"))
    for d in (stub_bin, markers, project, home):
        d.mkdir(parents=True)
    for name in ("python3", "python"):
        _write_stub(stub_bin / name, _PYTHON3_PIP_OK_STUB)
    _write_stub(stub_bin / "trw-mcp", _TRW_MCP_INIT_STUB)
    (project / ".git").mkdir()
    _seed_machine(home)
    env = {"PATH": f"{stub_bin}:/usr/bin:/bin", "HOME": str(home), "TRW_TEST_MARKERS": str(markers), "TERM": "dumb"}
    result = subprocess.run(
        ["bash", str(_BOOTSTRAPS[0]), "--no-embeddings"],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = result.stdout + result.stderr
    calls = (markers / "trw_mcp_calls").read_text(encoding="utf-8") if (markers / "trw_mcp_calls").exists() else ""
    assert "computer-wide, from ~/.trw/credentials.yaml" in combined, combined[-2000:]
    assert "Authentication required" not in combined
    assert "auth login" not in calls
    assert _KEY not in combined
    assert _KEY not in calls  # never in a child's argv
    assert not (project / ".trw" / "credentials.yaml").exists()


# ── installer template ────────────────────────────────────────────────────────


@pytest.fixture
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_machine_probe", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_API_KEY", raising=False)
    monkeypatch.delenv("TRW_PLATFORM_API_KEY", raising=False)


def _project(tmp_path: Path, creds_key: str | None = None) -> Path:
    target = tmp_path / "proj"
    (target / ".trw").mkdir(parents=True)
    (target / ".trw" / "config.yaml").write_text('installation_id: "proj"\n', encoding="utf-8")
    if creds_key is not None:
        (target / ".trw" / "credentials.yaml").write_text(f'platform_api_key: "{creds_key}"\n', encoding="utf-8")
    return target


@_posix_only
def test_template_falls_back_to_the_machine_key(installer: ModuleType, tmp_path: Path) -> None:
    _seed_machine(Path.home())
    assert installer._load_prior_config(_project(tmp_path))["api_key"] == _KEY


@_posix_only
def test_template_project_key_beats_machine_key(installer: ModuleType, tmp_path: Path) -> None:
    _seed_machine(Path.home())
    assert installer._resolve_prior_api_key(_project(tmp_path, "trw_dk_project")) == "trw_dk_project"


@_posix_only
def test_template_refuses_a_loose_machine_store(installer: ModuleType, tmp_path: Path) -> None:
    _seed_machine(Path.home(), mode=0o644)
    assert installer._resolve_prior_api_key(_project(tmp_path)) == ""


@_posix_only
def test_template_never_copies_the_machine_key_into_the_project(installer: ModuleType, tmp_path: Path) -> None:
    _seed_machine(Path.home())
    config_path = _project(tmp_path) / ".trw" / "config.yaml"
    assert installer.write_platform_credentials(config_path, _KEY) is None
    assert not (config_path.parent / "credentials.yaml").exists()


@_posix_only
def test_template_writes_a_project_key_0600_from_creation(installer: ModuleType, tmp_path: Path) -> None:
    config_path = _project(tmp_path) / ".trw" / "config.yaml"
    old = os.umask(0)  # a create-then-chmod writer would publish 0666 first
    try:
        written = installer.write_platform_credentials(config_path, "trw_dk_project")
    finally:
        os.umask(old)
    assert written == config_path.parent / "credentials.yaml"
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert installer._read_credentials_key(written) == "trw_dk_project"
    assert [p.name for p in config_path.parent.iterdir() if p.name.endswith(".tmp")] == []
