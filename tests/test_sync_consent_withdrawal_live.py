"""CONSENT-FLAGS-READ-LIVE: a consent withdrawn while the server runs stops the very next POST, with no restart.

The shared server builds its sync client once. ``learning_sharing_enabled: false`` written to the project's ``config.yaml``
afterwards must stop content egress at once; and a push that is already walking through several batches must stop at the
next batch, not after the last one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests._contact_support import payload_trw_dir
from tests.test_sync_consent_gate import _make_entry

pytestmark = pytest.mark.usefixtures("governing_project")


class _Spy:
    """Counts learning POSTs and runs a hook on each (to withdraw consent mid-push)."""

    def __init__(self, on_post: Any = None) -> None:
        self.posts: list[str] = []
        self._on_post = on_post

    def factory(self) -> MagicMock:
        spy = self

        class _Client:
            async def __aenter__(self_inner) -> Any:
                return self_inner

            async def __aexit__(self_inner, *exc: object) -> None:
                return None

            async def post(self_inner, url: str, **kwargs: object) -> Any:
                spy.posts.append(url)
                if spy._on_post is not None:
                    spy._on_post(len(spy.posts))
                resp = MagicMock()
                resp.status_code = 200
                resp.raise_for_status.return_value = None
                resp.json.return_value = {"inserted": 1, "updated": 0, "skipped": 0, "errors": 0}
                return resp

        return MagicMock(side_effect=lambda *a, **k: _Client())


def _withdraw(project: Path, flag: str = "learning_sharing_enabled") -> None:
    (project / ".trw" / "config.yaml").write_text(f"{flag}: false\n", encoding="utf-8")


def _pusher(**kwargs: Any) -> Any:
    from trw_mcp.sync.push import SyncPusher

    return SyncPusher(
        backend_url="http://backend.test",
        api_key="k",
        client_id="sync-test",
        learning_sharing_enabled=True,
        source_trw_dir=payload_trw_dir(),
        **kwargs,
    )


async def test_consent_withdrawn_after_the_pusher_was_built_stops_the_next_push(governing_project: Path) -> None:
    pusher = _pusher()
    _withdraw(governing_project)
    spy = _Spy()

    with patch("httpx.AsyncClient", spy.factory()):
        result = await pusher.push_learnings([_make_entry("L-1")])

    assert spy.posts == []
    assert result.pushed == 0


async def test_consent_withdrawn_during_a_multi_batch_push_stops_at_the_next_batch(governing_project: Path) -> None:
    pusher = _pusher(batch_size=1)
    spy = _Spy(on_post=lambda n: _withdraw(governing_project) if n == 1 else None)

    with patch("httpx.AsyncClient", spy.factory()):
        result = await pusher.push_learnings([_make_entry(f"L-{n}", sync_seq=n) for n in range(1, 5)])

    assert len(spy.posts) == 1, spy.posts  # batch 1 was in flight; batches 2-4 never leave the host
    assert result.pushed == 1


async def test_consent_still_granted_pushes_every_batch(governing_project: Path) -> None:
    """Positive control for the test above."""
    pusher = _pusher(batch_size=1)
    spy = _Spy()

    with patch("httpx.AsyncClient", spy.factory()):
        result = await pusher.push_learnings([_make_entry(f"L-{n}", sync_seq=n) for n in range(1, 5)])

    assert len(spy.posts) == 4
    assert result.pushed == 4


def test_publisher_post_asks_consent_live(governing_project: Path) -> None:
    from trw_mcp.telemetry import publisher

    payload: Any = {"summary": "s"}
    with patch("httpx.post") as post:
        post.return_value = MagicMock(status_code=200)
        _withdraw(governing_project)
        assert publisher._post_learning("http://backend.test", payload, "k", source_trw_dir=payload_trw_dir()) is False
    post.assert_not_called()


def test_telemetry_batch_asks_consent_live(governing_project: Path) -> None:
    from trw_mcp.telemetry.sender import BatchSender

    sender = BatchSender(
        platform_urls=["http://backend.test"],
        input_path=governing_project / "t.jsonl",
        platform_telemetry_enabled=True,
        source_trw_dir=payload_trw_dir(),
    )
    _withdraw(governing_project, "platform_telemetry_enabled")
    with patch.object(sender, "_http_post", return_value=True) as post:
        assert sender._send_batch_to("http://backend.test", [{"x": 1}]) is False
    post.assert_not_called()


def test_publisher_retry_after_429_is_not_sent_once_consent_is_withdrawn(tmp_path: Path) -> None:
    """The CORE-333 retry path: consent granted at the first attempt, withdrawn before the 429 retry -> one POST only."""
    from trw_mcp.telemetry import publisher

    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "0"})
    consent = iter([True, False])
    with (
        patch.object(publisher, "platform_contact_enabled", return_value=True),
        patch.object(publisher, "platform_auth_headers", return_value={}),
        patch.object(publisher, "send_policy", side_effect=lambda _d: MagicMock(learning_sharing=next(consent))),
        patch.object(publisher.httpx, "Client") as client_cls,
    ):
        post = client_cls.return_value.__enter__.return_value.post
        post.side_effect = [rate_limited, MagicMock(status_code=201)]
        sent = publisher._post_learning("https://platform.invalid", {"summary": "s"}, source_trw_dir=tmp_path)

    assert sent is False
    assert post.call_count == 1, "the retry was POSTed after consent was withdrawn"
