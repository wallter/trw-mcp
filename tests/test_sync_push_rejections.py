"""INC-147: one learning the backend rejects must not hold back the whole push batch.

A 422 used to fail the batch as a unit; the cycle kept every entry dirty and
re-sent the same batch forever, so nothing from the project left the host. The
pusher now isolates the rejected entries (by the server's per-entry locations,
or by splitting the batch when the 422 names none), pushes the rest, and
returns the rejected ids with the server's reason.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from structlog.testing import capture_logs

from tests._contact_support import payload_trw_dir
from tests.test_sync_push import _make_mock_entry

pytestmark = pytest.mark.usefixtures("governing_project")

_URL = "http://localhost:5002/v1/sync/learnings"


def _entry(entry_id: str, entry_type: str = "pattern") -> MagicMock:
    entry = _make_mock_entry(entry_id)
    entry.to_dict.return_value["type"] = entry_type
    return entry


def _client_cls(handler: Any) -> tuple[MagicMock, list[list[str]]]:
    """An httpx.AsyncClient stand-in whose post() answers through *handler*; records each batch's ids."""
    sent: list[list[str]] = []

    async def post(url: str, json: dict[str, Any], headers: object = None) -> httpx.Response:
        ids = [str(item["source_learning_id"]) for item in json["entries"]]
        sent.append(ids)
        status, body = handler(json["entries"])
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    client = MagicMock()
    client.post = AsyncMock(side_effect=post)
    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=client)
    cls.return_value.__aexit__ = AsyncMock(return_value=None)
    return cls, sent


def _pusher() -> Any:
    from trw_mcp.sync.push import SyncPusher

    return SyncPusher(
        backend_url="http://localhost:5002",
        api_key="test",
        client_id="sync-test",
        learning_sharing_enabled=True,
        source_trw_dir=payload_trw_dir(),
    )


def _accept_all(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    return 200, {"inserted": len(entries), "updated": 0, "skipped": 0, "errors": 0}


async def test_a_422_naming_one_entry_quarantines_it_and_pushes_the_rest() -> None:
    calls = iter(
        [
            (
                422,
                {
                    "detail": [
                        {
                            "loc": ["body", "entries", 1, "type"],
                            "msg": "Input should be 'pattern' or 'incident'",
                            "type": "literal_error",
                        }
                    ]
                },
            )
        ]
    )

    def handler(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        return next(calls, None) or _accept_all(entries)

    cls, sent = _client_cls(handler)
    with patch("httpx.AsyncClient", cls):
        result = await _pusher().push_learnings([_entry("L-0"), _entry("L-1", "decision"), _entry("L-2")])

    assert sent == [["L-0", "L-1", "L-2"], ["L-0", "L-2"]]
    assert result.pushed == 2
    assert result.failed == 0
    assert list(result.rejected) == ["L-1"]
    assert "type: Input should be 'pattern'" in result.rejected["L-1"]


async def test_an_unnamed_422_is_split_until_the_bad_entry_is_isolated() -> None:
    def handler(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        if any(item["type"] == "decision" for item in entries):
            return 422, {"detail": "unsupported learning type"}
        return _accept_all(entries)

    cls, sent = _client_cls(handler)
    entries = [_entry("L-0"), _entry("L-1"), _entry("L-2", "decision"), _entry("L-3")]
    with patch("httpx.AsyncClient", cls):
        result = await _pusher().push_learnings(entries)

    assert result.pushed == 3
    assert result.failed == 0
    assert result.rejected == {"L-2": "HTTP 422: unsupported learning type"}
    assert len(sent) <= 5  # whole batch, two halves, two quarters of the bad half


async def test_a_422_about_the_request_itself_fails_the_batch_and_quarantines_nothing() -> None:
    def handler(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        return 422, {"detail": [{"loc": ["body", "client_id"], "msg": "Field required", "type": "missing"}]}

    cls, sent = _client_cls(handler)
    with patch("httpx.AsyncClient", cls):
        result = await _pusher().push_learnings([_entry("L-0"), _entry("L-1")])

    assert result.failed == 2
    assert result.rejected == {}
    assert len(sent) == 1
    assert "client_id: Field required" in (result.last_error or "")


async def test_an_unnamed_422_that_rejects_every_entry_is_a_batch_failure_not_a_quarantine() -> None:
    def handler(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        return 422, {"detail": "schema version unsupported"}

    cls, _sent = _client_cls(handler)
    with patch("httpx.AsyncClient", cls):
        result = await _pusher().push_learnings([_entry("L-0"), _entry("L-1"), _entry("L-2")])

    assert result.rejected == {}
    assert result.failed == 3
    assert result.last_error == "HTTP 422: schema version unsupported"


async def test_the_422_body_is_logged() -> None:
    def handler(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        if len(entries) > 1:
            return 422, {"detail": [{"loc": ["body", "entries", 0, "type"], "msg": "bad type", "type": "x"}]}
        return _accept_all(entries)

    cls, _sent = _client_cls(handler)
    with patch("httpx.AsyncClient", cls), capture_logs() as logs:
        await _pusher().push_learnings([_entry("L-0", "decision"), _entry("L-1")])

    rejected = [log for log in logs if log["event"] == "sync_push_rejected"]
    assert rejected, logs
    assert "bad type" in str(rejected[0]["response_body"])
    assert rejected[0]["status_code"] == 422


async def test_a_server_error_names_its_status_and_body_in_last_error() -> None:
    def handler(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        return 503, {"detail": "maintenance window"}

    cls, _sent = _client_cls(handler)
    with patch("httpx.AsyncClient", cls):
        result = await _pusher().push_learnings([_entry("L-0")])

    assert result.failed == 1
    assert result.last_error == "HTTP 503: maintenance window"


async def test_a_split_or_re_send_asks_the_egress_switch_before_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex r1 block: the re-sends a 422 triggers are new requests, so each one asks the live switch."""
    from trw_mcp.sync import push as push_module

    switch = {"on": True}
    monkeypatch.setattr(push_module, "platform_contact_enabled", lambda _trw_dir: switch["on"])

    def handler(entries: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        switch["on"] = False  # the operator turns contact off while the first request is in flight
        return 422, {"detail": "unsupported learning type"}

    cls, sent = _client_cls(handler)
    with patch("httpx.AsyncClient", cls):
        result = await _pusher().push_learnings([_entry("L-0"), _entry("L-1", "decision")])

    assert len(sent) == 1
    assert result.rejected == {}
    assert result.failed == 2


async def test_a_malformed_success_body_fails_the_batch_instead_of_raising() -> None:
    """Codex r2: push_learnings never raises; an unreadable 200 body keeps the batch dirty."""
    client = MagicMock()
    client.post = AsyncMock(
        return_value=httpx.Response(200, text="<html>proxy</html>", request=httpx.Request("POST", _URL))
    )
    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=client)
    cls.return_value.__aexit__ = AsyncMock(return_value=None)
    with patch("httpx.AsyncClient", cls):
        result = await _pusher().push_learnings([_entry("L-0")])

    assert result.failed == 1
    assert result.pushed == 0
    assert result.last_error is not None
