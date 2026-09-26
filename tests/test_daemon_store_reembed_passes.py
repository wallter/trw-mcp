"""trw-mcp runs every bounded ``memory_reembed`` pass and sums them (rc9 sweep B2).

The daemon does one pass per call and hands back a ``cursor``; ``memory reembed`` and
``memory migrate --apply`` still report one answer for the whole namespace.
"""

from __future__ import annotations

from typing import Any

from trw_mcp.state._daemon_store import DaemonMemoryStore

_ZERO = dict.fromkeys(("already_current", "skipped", "warm_examined", "warm_reembedded"), 0)


class _Passes:
    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.cursors: list[str | None] = []

    async def reembed(self, _namespace: str, cursor: str | None = None) -> dict[str, Any]:
        self.cursors.append(cursor)
        return self.answers.pop(0)


def test_every_pass_runs_with_the_previous_cursor_and_the_counts_are_summed() -> None:
    client = _Passes(
        [
            {"status": "ok", "examined": 3, "reembedded": 2, **_ZERO, "cursor": "c1"},
            {"status": "ok", "examined": 1, "reembedded": 1, **_ZERO, "cursor": None, "outside_active_space": 0},
        ]
    )

    answer = DaemonMemoryStore(client, "project:t").reembed("project:t")  # type: ignore[arg-type]

    assert client.cursors == [None, "c1"]
    assert (answer["examined"], answer["reembedded"], answer["outside_active_space"]) == (4, 3, 0)


def test_a_pass_that_is_not_ok_is_the_answer() -> None:
    refused = {"status": "unavailable", "reason": "embedder_error"}
    client = _Passes([{"status": "ok", "examined": 3, "reembedded": 3, **_ZERO, "cursor": "c1"}, refused])

    assert DaemonMemoryStore(client, "project:t").reembed("project:t") == refused  # type: ignore[arg-type]


def test_a_pass_that_hands_back_its_own_cursor_stops_instead_of_looping() -> None:
    stuck = {"status": "ok", "examined": 1, "reembedded": 0, **_ZERO, "cursor": "c1"}
    client = _Passes([dict(stuck), dict(stuck), dict(stuck)])

    answer = DaemonMemoryStore(client, "project:t").reembed("project:t")  # type: ignore[arg-type]

    assert (answer["status"], answer["reason"], client.cursors) == ("unavailable", "reembed_stalled", [None, "c1"])
