"""``trw-mcp local feedback`` exit status and ``local`` project-root resolution (feedback sub_3kUbdYNtxmLWpLYm).

A script looping over submissions must see a failure when nothing was sent, and ``local`` verbs run from a
subdirectory must find the enclosing project's ``.trw/``.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._stdio_harness import pinned_server_env
from trw_mcp.server import _subcommands_misc as misc
from trw_mcp.state._project_root_binding import install_target, project_bound


def _feedback_args() -> argparse.Namespace:
    return argparse.Namespace(
        local_command="feedback", category="bugfix", subject="s", message="a long enough message", contact_email=None
    )


def _run_feedback(monkeypatch: pytest.MonkeyPatch, result: dict[str, object]) -> int:
    monkeypatch.setattr("trw_mcp.services.local_surface_service.submit_local_feedback", lambda **_: result)
    with pytest.raises(SystemExit) as exc:
        misc._run_local_verb(_feedback_args())
    return int(exc.value.code or 0)


@pytest.mark.parametrize(
    "result",
    [
        {"success": False, "error": "backend not configured (missing URL and API key)"},
        {"success": False, "error": "subject rejected", "status_code": 422},
        {"success": False, "error": "HTTP 503", "status_code": 503, "outbox_id": "20260101-abc"},
    ],
)
def test_unsent_feedback_exits_one_with_reason_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], result: dict[str, object]
) -> None:
    assert _run_feedback(monkeypatch, result) == 1
    captured = capsys.readouterr()
    assert "Feedback not submitted" in captured.err
    assert str(result["error"]) in captured.err
    assert captured.out == ""


def test_queued_but_unsent_feedback_names_the_outbox_record(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run_feedback(monkeypatch, {"success": False, "error": "HTTP 503", "outbox_id": "rec-1"})
    assert code == 1
    assert "rec-1" in capsys.readouterr().err


def test_sent_feedback_exits_zero(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run_feedback(monkeypatch, {"success": True, "submission_id": "sub_1"}) == 0
    assert "sub_1" in capsys.readouterr().out


def _repo(path: Path, *, with_trw: bool) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if with_trw:
        (path / ".trw").mkdir()
    return path


def _observed_root(monkeypatch: pytest.MonkeyPatch, cwd: Path) -> Path:
    """The root ``local`` binds for the verb, or the cwd when it binds nothing (the suite's autouse path fixture
    replaces ``resolve_project_root`` itself, so the binding is what is observable in-process)."""
    seen: list[Path] = []
    monkeypatch.setattr(misc, "_run_local_verb", lambda _args: seen.append(install_target() or Path.cwd().resolve()))
    monkeypatch.chdir(cwd)
    misc._run_local(argparse.Namespace(local_command="status"))
    return seen[0]


@pytest.fixture(autouse=True)
def _no_ambient_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)


def test_subdirectory_resolves_to_toplevel_with_trw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = _repo(tmp_path / "proj", with_trw=True)
    sub = top / "pkg" / "deep"
    sub.mkdir(parents=True)
    assert _observed_root(monkeypatch, sub) == top.resolve()


def test_toplevel_without_trw_stays_at_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = _repo(tmp_path / "outer", with_trw=True)
    nested = _repo(top / "nested", with_trw=False)  # its own repo, no .trw: the outer .trw is not its project
    sub = nested / "src"
    sub.mkdir()
    assert _observed_root(monkeypatch, sub) == sub.resolve()


def test_non_repo_cwd_stays_at_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _observed_root(monkeypatch, plain) == plain.resolve()


def test_env_root_wins_and_binds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = _repo(tmp_path / "proj", with_trw=True)
    sub = top / "pkg"
    sub.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(other))
    assert _observed_root(monkeypatch, sub) == sub.resolve()  # unbound: TRW_PROJECT_ROOT stays the resolver's answer


def test_enclosing_install_target_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    top = _repo(tmp_path / "proj", with_trw=True)
    sub = top / "pkg"
    sub.mkdir()
    bound = tmp_path / "bound"
    bound.mkdir()
    with project_bound(bound):
        assert _observed_root(monkeypatch, sub) == bound.resolve()


def test_real_cli_from_subdirectory_reads_toplevel_config(tmp_path: Path) -> None:
    """End to end: the reporter's scenario exits 1 and names the toplevel config it checked."""
    top = _repo(tmp_path / "proj", with_trw=True)
    sub = top / "pkg"
    sub.mkdir()
    base = {
        k: v for k, v in os.environ.items() if k not in ("TRW_PROJECT_ROOT", "TRW_BACKEND_URL", "TRW_BACKEND_API_KEY")
    }
    base["HOME"] = str(tmp_path / "home")
    proc = subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", "local", "feedback", "--category", "bugfix",
         "--subject", "s", "--message", "a long enough message"],
        capture_output=True, text=True, cwd=str(sub), env=pinned_server_env(base), timeout=120,
    )  # fmt: skip
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert str(top.resolve() / ".trw" / "config.yaml") in proc.stderr


def test_feedback_list_and_flush_find_the_toplevel_outbox_from_a_subdirectory(tmp_path: Path) -> None:
    """The retry hint (`trw-mcp feedback flush`) must work from where `local feedback` was run."""
    import json

    from trw_mcp.tools import _feedback_outbox as outbox

    top = _repo(tmp_path / "proj", with_trw=True)
    sub = top / "pkg"
    sub.mkdir()
    outbox.enqueue(
        top / ".trw",
        {"category": "bugfix", "subject": "queued report", "message": "a long enough message", "metadata": {}},
        contact_dropped=False,
    )
    base = {
        k: v for k, v in os.environ.items() if k not in ("TRW_PROJECT_ROOT", "TRW_BACKEND_URL", "TRW_BACKEND_API_KEY")
    }
    base["HOME"] = str(tmp_path / "home")

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "feedback", *argv],
            capture_output=True, text=True, cwd=str(sub), env=pinned_server_env(base), timeout=120,
        )  # fmt: skip

    listed = run("list", "--json")
    assert [e["subject"] for e in json.loads(listed.stdout)["pending"]] == ["queued report"]
    flushed = run("flush")  # unconfigured: refuses, and names the toplevel config it looked in
    assert flushed.returncode == 1
    assert str(top.resolve() / ".trw" / "config.yaml") in flushed.stdout
