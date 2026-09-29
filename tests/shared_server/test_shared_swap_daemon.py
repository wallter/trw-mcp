"""``swap --daemon``: drain the env's trw-memory daemon by handshake, never by signal."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
from trw_memory.daemon import DaemonInfo, DaemonPaths, DiscoveryAbsent, DiscoveryInvalid, _discovery, _grants, _upgrade
from trw_memory.exceptions import DaemonAuthError

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _cli, _ops
from trw_mcp.shared_server._records import SharedPaths, SharedServerError


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    p = SharedPaths.resolve(tmp_path / ".trw", SharedMcpConfig(envs_dir=str(tmp_path / "envs")))
    (tmp_path / "envs" / "lane" / "memory").mkdir(parents=True)
    return p


def _info(pid: int = 4242, version: str = "5.0.0") -> DaemonInfo:
    return DaemonInfo(
        pid=pid,
        url="http://127.0.0.1:1/mcp",
        started_at="2026-09-28T00:00:00+00:00",
        version=version,
        process_start="1.0",
        capabilities=["drain"],
    )


class _Fake:
    def __init__(self) -> None:
        self.drains: list[dict[str, Any]] = []
        self.probes = 0


def _fake(monkeypatch: pytest.MonkeyPatch, record: Any, answer: str = "", version: str = "5.1.0") -> _Fake:
    fake = _Fake()
    monkeypatch.setattr(_discovery, "read_live_discovery", lambda _p: record)
    monkeypatch.setattr(_grants, "read_checkout_grant", lambda _p: "tok")

    def drain(dpaths: DaemonPaths, *, token: str, mine: str, timeout: float | None = None) -> str:
        fake.drains.append({"dir": dpaths.user_memory_dir, "token": token, "mine": mine})
        return answer

    def probe(*_a: Any) -> str:
        fake.probes += 1
        return version

    monkeypatch.setattr(_upgrade, "drain_daemon", drain)
    monkeypatch.setattr(_ops, "env_memory_version", probe)
    return fake


def test_swap_daemon_drains_and_refuses_on_mismatch(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(monkeypatch, _info())
    out = _ops.drain_env_daemon(paths, "lane", project_root=tmp_path)
    assert out == "daemon pid 4242 (v5.0.0) drained; next recall starts v5.1.0"
    # The env's own memory dir (its TRW_USER_DIR) decides which daemon.json is read.
    assert fake.drains == [{"dir": tmp_path / "envs" / "lane" / "memory", "token": "tok", "mine": "5.1.0"}]

    _fake(monkeypatch, _info(), answer="process 4242 could not be proven to be the daemon its record names")
    with pytest.raises(SharedServerError, match="could not be proven"):
        _ops.drain_env_daemon(paths, "lane", project_root=tmp_path)


def test_missing_record_is_nothing_to_drain_without_grant_or_probe(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(monkeypatch, DiscoveryAbsent())

    def no_grant(_p: Path) -> str:
        raise DaemonAuthError("none")

    monkeypatch.setattr(_grants, "read_checkout_grant", no_grant)
    out = _ops.drain_env_daemon(paths, "lane", project_root=tmp_path)
    assert out == "no daemon running for env lane; nothing to drain"
    assert fake.drains == [] and fake.probes == 0


def test_untrusted_record_refuses_without_draining(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(monkeypatch, DiscoveryInvalid(path=Path("daemon.json"), reason="malformed"))
    with pytest.raises(SharedServerError, match="malformed"):
        _ops.drain_env_daemon(paths, "lane", project_root=tmp_path)
    assert fake.drains == []


def test_missing_grant_names_its_path_and_never_drains(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(monkeypatch, _info())

    def no_grant(_p: Path) -> str:
        raise DaemonAuthError("no memory grant")

    monkeypatch.setattr(_grants, "read_checkout_grant", no_grant)
    with pytest.raises(SharedServerError, match="memory-token"):
        _ops.drain_env_daemon(paths, "lane", project_root=tmp_path)
    assert fake.drains == [] and fake.probes == 0


def test_stable_refuses_when_the_swapper_has_trw_user_dir(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _fake(monkeypatch, _info())
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "elsewhere"))
    with pytest.raises(SharedServerError, match="TRW_USER_DIR"):
        _ops.drain_env_daemon(paths, "stable", project_root=tmp_path)
    assert fake.drains == []
    # A non-stable env has its own dir, so the swapper's variable does not matter to it.
    assert _ops.drain_env_daemon(paths, "lane", project_root=tmp_path).startswith("daemon pid 4242")


def _args(**over: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "env": "lane", "python": None, "src": None, "swap_version": None,
        "daemon": False, "with_distill": None, "expect_version": None,
    }  # fmt: skip
    return argparse.Namespace(**{**base, **over})


def _run_cli(paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: argparse.Namespace) -> int:
    monkeypatch.setattr(_cli, "_paths", lambda: (paths, SharedMcpConfig()))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    try:
        _cli.run_swap(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


@pytest.mark.parametrize(
    ("answer", "code", "stream", "text"),
    [("", 0, "out", "drained; next recall starts v5.1.0"), ("pid mismatch", 1, "err", "pid mismatch")],
)
def test_daemon_alone_exit_codes(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    answer: str, code: int, stream: str, text: str,
) -> None:  # fmt: skip
    _fake(monkeypatch, _info(), answer)
    assert _run_cli(paths, tmp_path, monkeypatch, _args(daemon=True)) == code
    assert text in getattr(capsys.readouterr(), stream)


def test_swap_without_source_or_daemon_refuses(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run_cli(paths, tmp_path, monkeypatch, _args()) == 1
    assert "--daemon" in capsys.readouterr().err


def test_expect_version_with_daemon_alone_refuses(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _fake(monkeypatch, _info())
    assert _run_cli(paths, tmp_path, monkeypatch, _args(daemon=True, expect_version="9")) == 1
    assert "--expect-version" in capsys.readouterr().err and fake.drains == []


def _with_swap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, result: str | Exception) -> None:
    def fake_swap(*_a: Any, **_k: Any) -> str:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(_ops, "swap", fake_swap)
    monkeypatch.setattr(_ops, "resolve_python", lambda p: p)
    py = tmp_path / "py"
    py.write_text("")


def test_daemon_after_a_swap_drains_and_reports_both(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _fake(monkeypatch, _info())
    _with_swap(monkeypatch, tmp_path, "lane: swapped")
    assert _run_cli(paths, tmp_path, monkeypatch, _args(daemon=True, python=tmp_path / "py")) == 0
    out = capsys.readouterr().out
    assert "lane: swapped" in out and "drained; next recall starts v5.1.0" in out and len(fake.drains) == 1


def test_drain_refusal_after_a_good_swap_says_so(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake(monkeypatch, _info(), answer="pid mismatch")
    _with_swap(monkeypatch, tmp_path, "lane: swapped")
    assert _run_cli(paths, tmp_path, monkeypatch, _args(daemon=True, python=tmp_path / "py")) == 1
    captured = capsys.readouterr()
    assert "lane: swapped" in captured.out
    assert "swap succeeded; daemon drain refused:" in captured.err and "pid mismatch" in captured.err


def test_no_drain_when_the_swap_raises(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _fake(monkeypatch, _info())
    _with_swap(monkeypatch, tmp_path, SharedServerError("runs trw-mcp 1, not 2; nothing changed"))
    assert _run_cli(paths, tmp_path, monkeypatch, _args(daemon=True, python=tmp_path / "py")) == 1
    assert fake.drains == [] and "nothing changed" in capsys.readouterr().err


def test_memory_version_probe_env_is_clean_unless_the_env_records_a_src(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, str]] = []

    def run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
        seen.append(env or {})
        return "5.1.0"

    monkeypatch.setattr(_ops, "_run", run)
    monkeypatch.setenv("PYTHONPATH", "/swapper/src")
    monkeypatch.setenv("PYTHONHOME", "/swapper/home")
    _ops.set_env_python(paths, "lane", Path("/py"))
    assert _ops.env_memory_version(paths, "lane") == "5.1.0"
    assert "PYTHONPATH" not in seen[0] and "PYTHONHOME" not in seen[0]
    _ops.set_env_python(paths, "lane", Path("/py"), pythonpath="/wt/trw-mcp/src:/wt/trw-memory/src")
    _ops.env_memory_version(paths, "lane")
    assert seen[1]["PYTHONPATH"] == "/wt/trw-mcp/src:/wt/trw-memory/src" and "PYTHONHOME" not in seen[1]
