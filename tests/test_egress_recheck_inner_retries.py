"""PRD-CORE-333: a retry or a 422 recovery re-send asks the quarantine check again (EGRESS-RECHECK-INNER-RETRIES).

The send-time check ran once per learning (publisher) or per batch (sync push), but the
publisher's 429 retries and sync push's 422 split-recovery re-sends POSTed again without it,
so a learning quarantined between the first attempt and a retry still left the host.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest


def test_a_publisher_retry_is_not_sent_once_the_learning_is_quarantined(tmp_path: Path) -> None:
    from trw_mcp.telemetry import publisher

    rate_limited = MagicMock(status_code=429, headers={"Retry-After": "0"})
    accepted = MagicMock(status_code=201)
    checks = iter([True, False])  # clean at the first attempt, quarantined before the retry

    with (
        patch.object(publisher, "platform_contact_enabled", return_value=True),
        patch.object(publisher, "send_policy", return_value=MagicMock(learning_sharing=True)),
        patch.object(publisher, "platform_auth_headers", return_value={}),
        patch.object(publisher.httpx, "Client") as client_cls,
    ):
        post = client_cls.return_value.__enter__.return_value.post
        post.side_effect = [rate_limited, accepted]
        sent = publisher._post_learning(
            "https://platform.invalid", {"summary": "s"}, source_trw_dir=tmp_path, sendable=lambda: next(checks)
        )

    assert sent is False
    assert post.call_count == 1, "the retry after the 429 was not POSTed"


async def test_a_422_recovery_resend_is_not_sent_once_a_row_is_quarantined() -> None:
    from trw_mcp.sync._push_batch import send_learning_batch

    posted: list[list[str]] = []

    async def post(payloads: list[dict[str, object]]) -> Any:
        posted.append([str(p["source_learning_id"]) for p in payloads])
        # The whole batch is refused with no entry named: recovery splits and re-sends the halves.
        return httpx.Response(422, json={"detail": "bad batch"}, request=httpx.Request("POST", "https://b.invalid"))

    quarantined: set[str] = set()

    def refused(ids: list[str]) -> bool:
        if posted:  # after the first POST, L-2 is quarantined
            quarantined.add("L-2")
        return any(i in quarantined for i in ids)

    items = [(f"L-{n}", {"source_learning_id": f"L-{n}"}) for n in (1, 2, 3, 4)]
    outcome = await send_learning_batch(post, items, client_id="c", refused=refused)

    assert ["L-1", "L-2"] not in posted and not any("L-2" in batch for batch in posted[1:]), posted
    assert outcome.failed >= 2 and outcome.pushed == 0


@pytest.mark.parametrize("sendable", [None, lambda: True])
def test_a_clean_learning_still_retries_after_a_429(tmp_path: Path, sendable: Any) -> None:
    from trw_mcp.telemetry import publisher

    with (
        patch.object(publisher, "platform_contact_enabled", return_value=True),
        patch.object(publisher, "send_policy", return_value=MagicMock(learning_sharing=True)),
        patch.object(publisher, "platform_auth_headers", return_value={}),
        patch.object(publisher.httpx, "Client") as client_cls,
    ):
        post = client_cls.return_value.__enter__.return_value.post
        post.side_effect = [MagicMock(status_code=429, headers={"Retry-After": "0"}), MagicMock(status_code=201)]
        sent = publisher._post_learning(
            "https://platform.invalid", {"summary": "s"}, source_trw_dir=tmp_path, sendable=sendable
        )

    assert sent is True and post.call_count == 2
