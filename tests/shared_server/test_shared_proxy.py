"""The stdio pass-through: forwards verbatim, resends only what never applied, never drops silently."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from trw_mcp.shared_server._proxy import Proxy, Target, client_identity, pump
from trw_mcp.shared_server._records import BUSY_HEADER, DRAINING_HEADER, SharedServerError

pytestmark = pytest.mark.unit

CALL = b'{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"trw_learn"}}'
LIST = b'{"jsonrpc":"2.0","id":8,"method":"tools/list"}'
NOTE = b'{"jsonrpc":"2.0","method":"notifications/initialized"}'
REQ = httpx.Request("POST", "http://127.0.0.1:1/mcp")


def _ok(body: bytes = b'{"jsonrpc":"2.0","id":7,"result":{}}\n') -> httpx.Response:
    return httpx.Response(200, content=body, request=REQ)


def _refused(header: str) -> httpx.Response:
    return httpx.Response(503, json={"error": "door says no"}, headers={header: "1"}, request=REQ)


class _Upstream:
    """A fake server port: answers (or raises) from a script, and records what it was sent."""

    def __init__(self, *outcomes: httpx.Response | Exception) -> None:
        self.outcomes = list(outcomes)
        self.sent: list[tuple[str, bytes, dict[str, str]]] = []
        self.resolved = 0

    async def resolve(self) -> Target:
        self.resolved += 1
        return Target(url="http://127.0.0.1:1/mcp", token="tok")

    async def post(self, url: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
        self.sent.append((url, body, headers))
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _proxy(upstream: _Upstream, *, budget: float = 30.0) -> Proxy:
    async def no_sleep(_: float) -> None:
        return None

    return Proxy(
        resolve=upstream.resolve,
        post=upstream.post,
        identity="client-a",
        env="stable",
        budget_seconds=budget,
        sleep=no_sleep,
    )


def _error_of(answer: bytes | None) -> dict[str, object]:
    assert answer is not None
    payload = json.loads(answer)
    return dict(payload["error"])


async def test_forwards_the_line_verbatim_with_bearer_and_client_identity() -> None:
    upstream = _Upstream(_ok())
    answer = await _proxy(upstream).forward(CALL)
    assert json.loads(answer or b"")["result"] == {}
    _, body, headers = upstream.sent[0]
    assert body == CALL
    assert headers["authorization"] == "Bearer tok"
    assert headers["mcp-session-id"] == "client-a"


async def test_an_accepted_notification_writes_nothing() -> None:
    assert await _proxy(_Upstream(httpx.Response(202, request=REQ))).forward(NOTE) is None


@pytest.mark.parametrize(
    "first",
    [httpx.ConnectError("refused"), _refused(DRAINING_HEADER), _refused(BUSY_HEADER), httpx.Response(401, request=REQ)],
    ids=["server-gone", "draining-for-swap", "busy", "token-rotated"],
)
async def test_a_never_applied_request_is_resent_after_rediscovery(first: object) -> None:
    upstream = _Upstream(first, _ok())  # type: ignore[arg-type]
    answer = await _proxy(upstream).forward(CALL)
    assert json.loads(answer or b"")["id"] == 7
    assert len(upstream.sent) == 2
    assert upstream.resolved == 2, "each attempt re-reads discovery, so a swapped server is found"


async def test_a_mutating_call_lost_after_sending_is_reported_not_resent() -> None:
    upstream = _Upstream(httpx.ReadError("reset"), _ok())
    error = _error_of(await _proxy(upstream).forward(CALL))
    assert error["code"] == -32001
    assert "may or may not have applied" in str(error["message"])
    assert len(upstream.sent) == 1


async def test_a_read_only_call_lost_after_sending_is_resent() -> None:
    upstream = _Upstream(httpx.ReadError("reset"), _ok(b'{"jsonrpc":"2.0","id":8,"result":{"tools":[]}}'))
    assert json.loads(await _proxy(upstream).forward(LIST) or b"")["id"] == 8
    assert len(upstream.sent) == 2


async def test_a_server_that_stays_away_yields_a_loud_error_naming_the_remedy() -> None:
    upstream = _Upstream(httpx.ConnectError("refused"))
    error = _error_of(await _proxy(upstream, budget=0.0).forward(CALL))
    assert error["code"] == -32002
    assert "NOT applied" in str(error["message"])
    assert "trw-mcp status --shared" in str(error["message"])


@pytest.mark.parametrize(
    ("response", "needle"),
    [
        (httpx.Response(503, text="overloaded", request=REQ), "HTTP 503"),  # unmarked: proves nothing, not resent
        (httpx.Response(500, text="boom", request=REQ), "HTTP 500"),
    ],
)
async def test_an_unexpected_status_is_an_error_without_resend(response: httpx.Response, needle: str) -> None:
    upstream = _Upstream(response)
    assert needle in str(_error_of(await _proxy(upstream).forward(CALL))["message"])
    assert len(upstream.sent) == 1


async def test_a_refused_resolution_is_answered_with_its_remedy() -> None:
    async def refuse() -> Target:
        raise SharedServerError("env 'dev' has no interpreter; run `trw-mcp swap --env dev --python <path>`")

    proxy = Proxy(resolve=refuse, post=_Upstream(_ok()).post, identity="a", env="dev")
    assert "trw-mcp swap --env dev" in str(_error_of(await proxy.forward(CALL))["message"])


async def test_a_failed_notification_is_reported_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    assert await _proxy(_Upstream(httpx.ConnectError("x")), budget=0.0).forward(NOTE) is None
    assert "trw-mcp-proxy:" in capsys.readouterr().err


async def test_a_non_json_line_is_a_parse_error_not_a_forward() -> None:
    upstream = _Upstream(_ok())
    answer = await _proxy(upstream).forward(b"{not json")
    assert answer is None and upstream.sent == []


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"TRW_SESSION_ID": "sess-1"}, lambda v: v == "sess-1"), ({}, lambda v: v.startswith("proxy-"))],
    ids=["operator-forced", "fresh-per-process"],
)
def test_client_identity_matches_the_stdio_pin_layers(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: Callable[[str], bool]
) -> None:
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert expected(client_identity())


async def test_pump_answers_every_line_concurrently_until_eof() -> None:
    reader = asyncio.StreamReader()
    for line in (CALL, b"\n", LIST):
        reader.feed_data(line + b"\n")
    reader.feed_eof()
    written: list[bytes] = []
    await pump(_proxy(_Upstream(_ok())), reader, written.append)
    assert len(written) == 2 and all(chunk.endswith(b"\n") for chunk in written)
