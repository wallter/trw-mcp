"""DISPATCH-TEST-LAUNCH-GUARD: the suite-wide guard refuses a real client launch and nothing else.

A fake client written into ``tmp_path`` is what tests legitimately run, so each test here makes that fake
look "installed" by emptying the guard's safe roots, then launches it the ways the product does.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

import tests._launch_guard as guard


def _fake(tmp_path: Path, name: str = "codex") -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho launched\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat every path as outside the safe roots, as a real ~/.nvm or /usr/local install is."""
    monkeypatch.setattr(guard, "safe_roots", lambda: ())


@pytest.mark.usefixtures("installed")
@pytest.mark.parametrize(
    "launch",
    [
        lambda p: subprocess.run([str(p), "exec", "hi"], capture_output=True, check=False),
        lambda p: subprocess.Popen([str(p), "exec", "hi"]).wait(),
        lambda p: subprocess.run(f"{p} exec hi", shell=True, capture_output=True, check=False),
        lambda p: subprocess.run(
            ["codex", "exec", "hi"], env={"PATH": str(p.parent)}, capture_output=True, check=False
        ),
    ],
    ids=["run", "popen", "shell", "bare-name-on-PATH"],
)
def test_a_real_client_launch_is_refused_however_it_is_spawned(tmp_path: Path, launch: object) -> None:
    fake = _fake(tmp_path)
    with pytest.raises(AssertionError, match="tried to launch the real client"):
        launch(fake)  # type: ignore[operator]


@pytest.mark.usefixtures("installed")
def test_a_version_probe_and_a_non_client_program_still_run(tmp_path: Path) -> None:
    fake = _fake(tmp_path)
    assert subprocess.run([str(fake), "--version"], capture_output=True, check=False).returncode == 0
    other = _fake(tmp_path, "not-a-client")
    assert subprocess.run([str(other), "exec"], capture_output=True, text=True, check=False).stdout == "launched\n"


def test_a_fake_client_in_the_tests_temp_dir_still_runs(tmp_path: Path) -> None:
    """The existing dispatch tests' fakes live in tmp_path; the guard must leave them alone."""
    fake = _fake(tmp_path)
    out = subprocess.run([str(fake), "exec", "hi"], capture_output=True, text=True, check=False)
    assert out.stdout == "launched\n"


def test_every_registered_client_binary_is_guarded() -> None:
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    expected = {name for spec in CLIENT_SPECS.values() for name in spec.binary_names}
    assert expected and guard.client_binaries() == frozenset(expected)
    assert {"codex", "claude"} <= expected


@pytest.mark.usefixtures("installed")
def test_a_read_only_mcp_list_passes_but_a_run_is_still_refused(tmp_path: Path) -> None:
    """Lead ruling (option A): ``codex mcp list/get`` read config and call no model; ``exec`` or ``mcp add`` still may not."""
    fake = _fake(tmp_path)
    for lookup in (["mcp", "list", "--json"], ["mcp", "get", "trw"]):
        listed = subprocess.run([str(fake), *lookup], capture_output=True, text=True, check=False)
        assert listed.stdout == "launched\n"
    for run in (["exec", "mcp", "list"], ["mcp", "add", "x", "--", "cmd"]):
        with pytest.raises(AssertionError, match="tried to launch the real client"):
            subprocess.run([str(fake), *run], capture_output=True, check=False)
