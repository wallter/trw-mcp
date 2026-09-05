"""PRD-FIX-133: ``trw-mcp doctor`` reports live Antigravity MCP registration.

Antigravity CLI reads MCP servers from a GLOBAL config file the installer now
writes correctly (see ``test_antigravity_cli_bootstrap.py``); this suite covers
the separate live-verification row that asks the running ``agy`` binary
directly via ``agy mcp list``, rather than re-reading the file TRW just wrote.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.server._doctor_antigravity_mcp import antigravity_mcp_row


def _antigravity_project(tmp_path: Path) -> Path:
    """A project tree whose recorded target client is antigravity-cli."""
    (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trw" / "config.yaml").write_text("target_platforms:\n  - antigravity-cli\n", encoding="utf-8")
    return tmp_path


@pytest.mark.unit
def test_skip_when_antigravity_not_selected(tmp_path: Path) -> None:
    """A project that never selected antigravity-cli has nothing to verify."""
    status, message = antigravity_mcp_row(tmp_path)
    assert status == "SKIP"
    assert "not a selected client" in message


@pytest.mark.unit
def test_skip_never_pass_when_binary_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: an absent 'agy' binary is UNKNOWN, never a confirmed PASS."""
    project = _antigravity_project(tmp_path)
    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.shutil.which", lambda _name: None)

    status, message = antigravity_mcp_row(project)

    assert status == "SKIP"
    assert status != "PASS"
    assert "not found on PATH" in message


@pytest.mark.unit
def test_pass_when_agy_reports_trw_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _antigravity_project(tmp_path)
    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.shutil.which", lambda _name: "/usr/bin/agy")

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["agy", "mcp", "list"], returncode=0, stdout="trw (stdio)\n", stderr="")

    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.subprocess.run", _fake_run)

    status, message = antigravity_mcp_row(project)
    assert status == "PASS"
    assert "trw" in message


@pytest.mark.unit
def test_warn_when_agy_reports_trw_not_registered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _antigravity_project(tmp_path)
    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.shutil.which", lambda _name: "/usr/bin/agy")

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["agy", "mcp", "list"], returncode=0, stdout="No MCP servers configured.\n", stderr=""
        )

    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.subprocess.run", _fake_run)

    status, message = antigravity_mcp_row(project)
    assert status == "WARN"
    assert "not registered" in message


@pytest.mark.unit
def test_warn_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _antigravity_project(tmp_path)
    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.shutil.which", lambda _name: "/usr/bin/agy")

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="agy mcp list", timeout=5.0)

    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.subprocess.run", _fake_run)

    status, message = antigravity_mcp_row(project)
    assert status == "WARN"
    assert "timed out" in message


@pytest.mark.unit
def test_warn_on_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _antigravity_project(tmp_path)
    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.shutil.which", lambda _name: "/usr/bin/agy")

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["agy", "mcp", "list"], returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.subprocess.run", _fake_run)

    status, message = antigravity_mcp_row(project)
    assert status == "WARN"
    assert "boom" in message


@pytest.mark.unit
def test_antigravity_mcp_is_registered_in_the_doctor_catalogue() -> None:
    """Deleting the ``_CHECKS`` row must fail a test, not just a changelog claim."""
    from trw_mcp.server import _subcommands_doctor as doctor

    assert ("antigravity_mcp", "_check_antigravity_mcp") in doctor._CHECKS


@pytest.mark.unit
@pytest.mark.parametrize(
    "stdout",
    [
        "trw-memory (stdio)\n",
        "No MCP servers configured. Try: agy mcp add trw trw-mcp\n",
        "  other (stdio)\n  trw2 (http)\n",
    ],
)
def test_substring_lookalikes_do_not_confirm_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: str
) -> None:
    """Only a line whose first token is ``trw`` proves the server is registered."""
    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.shutil.which", lambda _name: "/usr/bin/agy")

    def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["agy", "mcp", "list"], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr("trw_mcp.server._doctor_antigravity_mcp.subprocess.run", _fake_run)
    status, _message = antigravity_mcp_row(_antigravity_project(tmp_path))

    assert status == "WARN"
