"""H1 (R9 #7): a checkout must not get shell execution through ``.trw/runtime/hook-env.d/<key>.sh``.

Every hook sources ``lib-trw.sh``, and ``lib-trw.sh`` sourced that generated file with only a
``[ -f ... ]`` guard, so a hostile branch that force-added one ran its shell as the user the next time
any installed hook fired. The file is now sourced only when TRW itself could have written it: a
regular file with no symlink from ``.trw`` down, owned by the current user, not group- or
world-writable, and not tracked by git. On refusal the hook skips the file, prints one warning line,
and carries on.

These run the REAL shipped ``lib-trw.sh``. Each refusal case plants a file whose body drops a marker;
the marker's absence proves the shell never ran.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh"
_KEY = "claude"
_WARNING = "TRW: not sourcing .trw/runtime/hook-env.d/claude.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None or shutil.which("git") is None, reason="sh or git unavailable"
)


class _Checkout:
    def __init__(self, root: Path, marker: Path) -> None:
        self.root = root
        self.marker = marker
        self.env_dir = root / ".trw" / "runtime" / "hook-env.d"
        self.env_file = self.env_dir / f"{_KEY}.sh"

    @property
    def body(self) -> str:
        return f': > "{self.marker}"\nTRW_HOOK_ENV_SOURCED=yes\nexport TRW_HOOK_ENV_SOURCED\n'

    def plant(self, mode: int = 0o600) -> Path:
        self.env_dir.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text(self.body, encoding="utf-8")
        self.env_file.chmod(mode)
        return self.env_file

    def git(self, *args: str) -> None:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            env=env,
        )

    def source(self, *, path_prefix: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("TRW_", "CLAUDE_"))}
        env["CLAUDE_PROJECT_DIR"] = str(self.root)
        env["TRW_HOOK_CLIENT"] = _KEY
        if path_prefix is not None:
            env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["sh", "-c", f'. "{_LIB}"; printf "LIB_LOADED sourced=%s\\n" "${{TRW_HOOK_ENV_SOURCED:-no}}"'],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

    def ran(self) -> bool:
        return self.marker.exists()


@pytest.fixture()
def checkout(tmp_path: Path) -> _Checkout:
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    return _Checkout(root, tmp_path / "MARKER")


def _refused(result: subprocess.CompletedProcess[str], checkout: _Checkout, why: str) -> None:
    assert result.returncode == 0, result.stderr
    assert "LIB_LOADED sourced=no" in result.stdout
    assert not checkout.ran(), "the planted file ran as shell"
    warnings = [line for line in result.stderr.splitlines() if line.startswith(_WARNING)]
    assert len(warnings) == 1, result.stderr
    assert why in warnings[0]


def test_a_file_trw_could_have_written_is_still_sourced(checkout: _Checkout) -> None:
    checkout.plant(0o600)

    result = checkout.source()

    assert result.returncode == 0, result.stderr
    assert "LIB_LOADED sourced=yes" in result.stdout
    assert checkout.ran()
    assert _WARNING not in result.stderr


def test_a_missing_file_is_silent(checkout: _Checkout) -> None:
    result = checkout.source()

    assert result.returncode == 0
    assert "LIB_LOADED sourced=no" in result.stdout
    assert result.stderr == ""


def test_a_symlinked_file_is_refused(checkout: _Checkout, tmp_path: Path) -> None:
    real = tmp_path / "elsewhere" / "real.sh"
    real.write_text(checkout.body, encoding="utf-8")
    real.chmod(0o600)
    checkout.env_dir.mkdir(parents=True)
    checkout.env_file.symlink_to(real)

    _refused(checkout.source(), checkout, "a symlink at .trw/runtime/hook-env.d/claude.sh")


@pytest.mark.parametrize("linked", [".trw", ".trw/runtime", ".trw/runtime/hook-env.d"])
def test_a_symlinked_directory_on_the_path_is_refused(checkout: _Checkout, tmp_path: Path, linked: str) -> None:
    real_root = tmp_path / "elsewhere" / "state"
    real = _Checkout(checkout.root, checkout.marker)
    real.env_dir = real_root / ".trw" / "runtime" / "hook-env.d"
    real.env_file = real.env_dir / f"{_KEY}.sh"
    real.plant(0o600)
    target = real_root / linked
    link = checkout.root / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    _refused(checkout.source(), checkout, f"a symlink at {linked}")


@pytest.mark.parametrize("mode", [0o660, 0o620, 0o606, 0o666, 0o777])
def test_a_group_or_world_writable_file_is_refused(checkout: _Checkout, mode: int) -> None:
    checkout.plant(mode)
    assert stat.S_IMODE(checkout.env_file.stat().st_mode) == mode

    _refused(checkout.source(), checkout, "writable by group or others")


def test_a_file_owned_by_someone_else_is_refused(checkout: _Checkout, tmp_path: Path) -> None:
    """The one case a non-root test cannot create: a fake ``id`` makes the file look foreign."""
    checkout.plant(0o600)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    shim = fake / "id"
    shim.write_text("#!/bin/sh\nprintf '4242\\n'\n", encoding="utf-8")
    shim.chmod(0o755)

    _refused(checkout.source(path_prefix=fake), checkout, "not owned by the current user")


def test_a_file_committed_to_the_repository_is_refused(checkout: _Checkout) -> None:
    """The real attack: the hook-env file arrives with a branch."""
    checkout.plant(0o600)
    checkout.git("add", "-f", ".trw/runtime/hook-env.d/claude.sh")
    checkout.git("commit", "-q", "-m", "hostile branch adds the file")

    _refused(checkout.source(), checkout, "tracked by git")


def test_a_file_staged_but_not_committed_is_refused(checkout: _Checkout) -> None:
    checkout.plant(0o600)
    checkout.git("add", "-f", ".trw/runtime/hook-env.d/claude.sh")

    _refused(checkout.source(), checkout, "tracked by git")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
def test_a_fifo_is_refused_and_does_not_hang_the_hook(checkout: _Checkout) -> None:
    checkout.env_dir.mkdir(parents=True)
    os.mkfifo(checkout.env_file, 0o600)

    _refused(checkout.source(), checkout, "not a regular file")


def test_an_untracked_file_in_a_checkout_that_tracks_other_files_is_still_sourced(checkout: _Checkout) -> None:
    (checkout.root / "README.md").write_text("x\n", encoding="utf-8")
    checkout.git("add", "README.md")
    checkout.git("commit", "-q", "-m", "init")
    checkout.plant(0o600)

    result = checkout.source()

    assert "LIB_LOADED sourced=yes" in result.stdout
    assert checkout.ran()
