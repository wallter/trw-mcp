"""Darwin branch of ``capture_identity``: ``ps`` + ``sysctl`` replace procfs (fail-closed)."""

from __future__ import annotations

import pytest

from trw_mcp.dispatch import _process_identity as identity

pytestmark = pytest.mark.unit

_PS_OK = " 4242  4242 Ss   Wed Sep 16 22:30:31 2026\n"
_BOOT = "{ sec = 1783820748, usec = 369428 } Sat Jul 11 19:45:48 2026\n"


def _fake_probe(ps_line: str, boot: str = _BOOT):
    def probe(argv: list[str]) -> str:
        return ps_line if argv[0] == "ps" else boot

    return probe


@pytest.fixture(autouse=True)
def _fresh_boot_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_boot_id_cache", None)


def test_darwin_leader_identity_is_captured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_probe_stdout", _fake_probe(_PS_OK))
    assert identity._capture_identity_darwin(4242) == {
        "pid": 4242,
        "starttime": "Wed Sep 16 22:30:31 2026",
        "boot_id": "1783820748",
    }


@pytest.mark.parametrize(
    "ps_line",
    [
        " 4242  4000 Ss   Wed Sep 16 22:30:31 2026\n",  # not its own group leader
        " 4242  4242 Z    Wed Sep 16 22:30:31 2026\n",  # zombie
        " 4243  4243 Ss   Wed Sep 16 22:30:31 2026\n",  # pid mismatch
        "",  # process gone (ps exits non-zero -> empty)
        " 4242  4242\n",  # malformed
    ],
)
def test_darwin_rejects_non_leader_zombie_or_malformed(monkeypatch: pytest.MonkeyPatch, ps_line: str) -> None:
    monkeypatch.setattr(identity, "_probe_stdout", _fake_probe(ps_line))
    assert identity._capture_identity_darwin(4242) is None


def test_darwin_missing_boottime_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "_probe_stdout", _fake_probe(_PS_OK, boot=""))
    assert identity._capture_identity_darwin(4242) is None


def test_darwin_probe_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(argv: list[str]) -> str:
        raise OSError("ps unavailable")

    monkeypatch.setattr(identity, "_probe_stdout", boom)
    assert identity._capture_identity_darwin(4242) is None


def test_capture_identity_routes_to_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity.sys, "platform", "darwin")
    monkeypatch.setattr(identity, "_probe_stdout", _fake_probe(_PS_OK))
    assert identity.capture_identity(4242) == {
        "pid": 4242,
        "starttime": "Wed Sep 16 22:30:31 2026",
        "boot_id": "1783820748",
    }


def test_pty_wrapper_uses_bsd_script_syntax_on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch import _runner

    monkeypatch.setattr(_runner.sys, "platform", "darwin")
    assert _runner._wrap_pty(["agy", "-p", "hi there"]) == ["script", "-q", "/dev/null", "agy", "-p", "hi there"]
    monkeypatch.setattr(_runner.sys, "platform", "linux")
    assert _runner._wrap_pty(["agy", "-p", "hi there"]) == ["script", "-qec", "agy -p 'hi there'", "/dev/null"]


def test_darwin_probe_env_is_pinned_to_utc_c_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_run(argv: list[str], **kw: object) -> object:
        seen.append(kw.get("env"))
        return type("R", (), {"returncode": 0, "stdout": _PS_OK if argv[0] == "ps" else _BOOT})()

    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    assert identity._capture_identity_darwin(4242) is not None
    assert seen and all(e == identity._PROBE_ENV for e in seen)
    assert identity._PROBE_ENV["TZ"] == "UTC" and identity._PROBE_ENV["LC_ALL"] == "C"


def test_darwin_boot_id_is_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def probe(argv: list[str]) -> str:
        calls.append(argv[0])
        return _PS_OK if argv[0] == "ps" else _BOOT

    monkeypatch.setattr(identity, "_probe_stdout", probe)
    identity._capture_identity_darwin(4242)
    identity._capture_identity_darwin(4242)
    assert calls.count("sysctl") == 1 and calls.count("ps") == 2
