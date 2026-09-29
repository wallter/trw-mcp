"""The shared server's door, its process environment, and the discovery records it publishes."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import pytest
from trw_memory.storage._pid_liveness import process_start

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _records
from trw_mcp.shared_server._records import SharedPaths, SharedServerError
from trw_mcp.shared_server._server import Door, prepare_process_env

pytestmark = pytest.mark.unit


class _App:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


def _scope(path: str = "/mcp", *, token: str | None = "tok", session: str | None = "client-a") -> dict[str, Any]:
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if session is not None:
        headers.append((b"mcp-session-id", session.encode()))
    return {"type": "http", "path": path, "method": "POST", "headers": headers}


async def _call(door: Door, scope: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await door(scope, None, send)
    headers = {k.decode(): v.decode() for k, v in sent[0].get("headers", [])}
    return sent[0]["status"], headers, sent[-1].get("body", b"")


def _door(app: _App, *, max_inflight: int = 4) -> Door:
    return Door(app, token="tok", env="stable", version="9.9.9", max_inflight=max_inflight)


@pytest.mark.parametrize("token", [None, "wrong"], ids=["missing", "wrong"])
async def test_a_request_without_the_bearer_never_reaches_the_app(token: str | None) -> None:
    app = _App()
    status, _, _ = await _call(_door(app), _scope(token=token))
    assert (status, app.calls) == (401, 0)


async def test_a_request_without_session_identity_is_refused_with_the_remedy() -> None:
    app = _App()
    status, _, body = await _call(_door(app), _scope(session=None))
    assert (status, app.calls) == (400, 0)
    assert b"trw-mcp-proxy" in body


async def test_an_admitted_request_reaches_the_app_and_is_counted_per_session() -> None:
    app = _App()
    door = _door(app)
    for session in ("client-a", "client-b", "client-a"):
        status, _, _ = await _call(door, _scope(session=session))
        assert status == 200
    assert app.calls == 3 and door.in_flight == 0
    assert door.status()["sessions_last_hour"] == 2


@pytest.mark.parametrize(
    ("setup", "header"),
    [
        (lambda d: setattr(d, "draining", True), "x-trw-mcp-draining"),
        (lambda d: setattr(d, "in_flight", 4), "x-trw-mcp-busy"),
    ],
    ids=["draining", "at-inflight-cap"],
)
async def test_door_refusals_are_marked_so_the_proxy_may_resend(setup: Any, header: str) -> None:
    app = _App()
    door = _door(app)
    setup(door)
    status, headers, _ = await _call(door, _scope())
    assert (status, app.calls) == (503, 0)
    assert header in headers


async def test_admin_drain_closes_the_door_and_exits_once_idle() -> None:
    app, drained = _App(), []
    door = _door(app)
    door.on_drained = lambda: drained.append(True)
    status, _, _ = await _call(door, _scope("/admin/drain"))
    assert status == 202 and door.draining
    await door._finish_drain()
    assert drained == [True]


async def test_admin_status_reports_version_and_pid() -> None:
    status, _, body = await _call(_door(_App()), _scope("/admin/status"))
    payload = json.loads(body)
    assert status == 200 and payload["version"] == "9.9.9" and payload["pid"] == os.getpid()


def test_the_server_drops_client_identity_from_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", "leak")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "leak")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    prepare_process_env()
    assert "TRW_SESSION_ID" not in os.environ and "CLAUDE_CODE_SESSION_ID" not in os.environ


def test_a_reviewer_role_server_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    with pytest.raises(SharedServerError, match="reviewer"):
        prepare_process_env()


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    return SharedPaths.resolve(tmp_path / ".trw", SharedMcpConfig(envs_dir=str(tmp_path / "envs")))


def test_a_published_record_reads_back_live_and_withdraws(paths: SharedPaths) -> None:
    _records.publish_record(paths, "stable", url="http://127.0.0.1:5555/mcp", version="1.2.3")
    info = _records.read_live_record(paths, "stable")
    assert info is not None and (info.pid, info.version) == (os.getpid(), "1.2.3")
    _records.withdraw_record(paths, "stable")
    assert _records.read_live_record(paths, "stable") is None


def test_withdraw_leaves_a_successors_record_in_place(paths: SharedPaths) -> None:
    successor = {
        "schema_version": 1,
        "pid": os.getppid(),
        "url": "http://127.0.0.1:5556/mcp",
        "started_at": "2026-09-26T00:00:00+00:00",
        "version": "2",
        "process_start": process_start(os.getppid()),
    }
    _records.write_secret_file(paths.record("dev"), json.dumps(successor))
    _records.withdraw_record(paths, "dev")
    live = _records.read_live_record(paths, "dev")
    assert live is not None and live.pid == os.getppid()


def test_a_record_naming_a_dead_process_reads_as_absent(paths: SharedPaths) -> None:
    dead = {
        "schema_version": 1,
        "pid": 2**22 - 7,
        "url": "http://127.0.0.1:5557/mcp",
        "started_at": "2026-09-26T00:00:00+00:00",
        "version": "1",
        "process_start": "never",
    }
    _records.write_secret_file(paths.record("stable"), json.dumps(dead))
    assert _records.read_live_record(paths, "stable") is None


@pytest.mark.parametrize("name", ["../x", "Stable", "", "a" * 40])
def test_env_names_are_validated(name: str) -> None:
    with pytest.raises(SharedServerError):
        _records.validate_env(name)


def test_stable_defaults_to_this_interpreter_and_other_envs_must_be_swapped_in(paths: SharedPaths) -> None:
    assert _records.env_python(paths, "stable") == sys.executable
    with pytest.raises(SharedServerError, match="trw-mcp swap --env dev"):
        _records.env_python(paths, "dev")
    _records.set_env_python(paths, "dev", Path("/opt/dev/bin/python"))
    assert _records.env_python(paths, "dev") == "/opt/dev/bin/python"
    assert paths.user_dir("stable") is None and paths.user_dir("dev") == paths.envs_dir / "dev"


def test_the_token_is_minted_once_at_0600(paths: SharedPaths) -> None:
    first = _records.ensure_token(paths)
    assert _records.ensure_token(paths) == first == _records.read_token(paths)
    assert paths.token.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("payload", ["{not json", "[1, 2]"], ids=["malformed", "not-an-object"])
def test_an_invalid_record_is_refused_not_read_as_absent(paths: SharedPaths, payload: str) -> None:
    _records.write_secret_file(paths.record("stable"), payload)
    with pytest.raises(SharedServerError, match="not a valid record"):
        _records.read_live_record(paths, "stable")
    _records.withdraw_record(paths, "stable")
    assert paths.record("stable").exists(), "withdraw never deletes a record it cannot prove is its own"


def test_serve_shared_refuses_while_the_feature_is_off() -> None:
    from trw_mcp.shared_server._server import serve_shared

    with pytest.raises(SharedServerError, match=r"shared_mcp\.enabled is false"):
        serve_shared(env="stable", successor=False)


async def test_a_failed_predecessor_drain_is_reported_not_fatal() -> None:
    import httpx

    from trw_mcp.shared_server._server import drain_predecessor

    class _Prior:
        pid, url = 4242, "http://127.0.0.1:1/mcp"

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        assert await drain_predecessor(client, _Prior(), "tok") is False
