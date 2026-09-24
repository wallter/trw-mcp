"""A recall reads every row it returns, whole, however late or repeated in a session.

Observation masking used to rewrite responses downstream of the tools. From the
eleventh call of a session it compressed each one with a JSON-only compressor; tool
responses are YAML text, so each was cut to its first 500 characters (C9 frozen
workload, RC dry run 2026-09-23). A response identical to the tool's previous one
was replaced with "[No changes since turn N]", a pointer to content the client may
no longer hold once it has compacted its context. Response size is owned by each
tool at the source; nothing downstream cuts or elides a response.

PRD-CORE-294 FR01 changed ``trw_recall``'s row shape to a bounded stub
(``{id, claim, anchor?}``) rather than the full ``{summary, detail}`` row —
full rows are one ``trw_recall(ids=[...])`` hydration call away, by design
(see ``trw_mcp.tools._recall_presenter``). This test therefore compares
stubs, not full rows: the invariant it protects is that the LATE or REPEATED
stub is identical to the early one and is never elided or 500-char-clipped
downstream, not that the full detail rides along in every recall.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from trw_mcp.models.config import get_config
from trw_mcp.server._app import create_app
from trw_mcp.server._tools import _tool_registrars

_TOPICS = ("lanternfish", "quillwort", "basaltine")
_QUERY = {"query": "masking probe", "max_results": 10}


def _text(result: Any) -> str:
    return "".join(str(getattr(block, "text", "")) for block in result.content or [])


def _rows(result: Any) -> dict[str, tuple[str, str]]:
    """Stub rows: ``{id, claim, anchor?}`` (PRD-CORE-294 FR01) — full detail is not present."""
    return {
        str(row["id"]): (str(row["claim"]), str(row.get("anchor", "")))
        for row in result.structured_content["learnings"]
    }


def _assert_text_carries(result: Any) -> None:
    """Every returned stub's id, claim and anchor is in the text the model reads."""
    text = _text(result)
    words = " ".join(text.split())  # the YAML renderer wraps long scalars across lines
    assert "[No changes since turn" not in text
    assert "[… truncated" not in text
    for row_id, (claim, anchor) in _rows(result).items():
        assert row_id in text
        assert " ".join(claim.split()) in words
        if anchor:
            assert " ".join(anchor.split()) in words


@pytest.fixture
def server(tmp_project: Any, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon) -> Any:
    # ``tmp_project`` sits at ``tmp_path / ".trw"``, the SAME directory the
    # global ``_isolate_trw_dir`` autouse fixture forces every
    # ``resolve_trw_dir()`` call to (PRD-CORE-280 slice e1): migrate exactly
    # that checkout so trw_learn/trw_recall route through the daemon store,
    # never an in-process ``memory.db``.
    trw_dir = tmp_project / ".trw"
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    monkeypatch.setattr(
        "trw_memory.daemon.client.start_daemon_detached",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("a test tried to start a second memory daemon")),
    )
    attach_checkout(trw_dir, memory_daemon)
    monkeypatch.setattr(get_config(), "embeddings_enabled", False)
    app = create_app()
    for register in _tool_registrars():
        register(app)
    return app


async def _record_probes(client: Client) -> None:
    for topic in _TOPICS:
        recorded = await client.call_tool(
            "trw_learn",
            {
                "summary": f"Masking probe {topic}: a late recall must carry this whole summary to its caller",
                "detail": f"The {topic} row exists so the recall response is longer than any fixed cut. " * 3,
                "tags": ["maskprobe"],
            },
        )
        assert not recorded.is_error


async def test_recall_after_the_tenth_call_returns_the_same_rows_as_an_early_one(server: Any) -> None:
    async with Client(server) as client:
        await _record_probes(client)
        early = await client.call_tool("trw_recall", _QUERY)
        for turn in range(10):  # reads only: past the old compact threshold without memory writes
            await client.call_tool("trw_recall", {"query": f"unrelated filler query {turn}", "max_results": 1})
        late = await client.call_tool("trw_recall", _QUERY)

    assert not early.is_error and not late.is_error
    assert {summary.split(":")[0] for summary, _ in _rows(late).values()} >= {f"Masking probe {t}" for t in _TOPICS}
    assert _rows(late) == _rows(early)
    _assert_text_carries(early)
    _assert_text_carries(late)


async def test_a_repeated_identical_recall_returns_the_full_rows(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The server cannot know the client still holds the first answer, so it sends it again.

    Session started and nudges off, so the two responses are byte-identical: the case the
    old collapse replaced with "[No changes since turn N]".
    """
    monkeypatch.setattr(get_config(), "nudge_enabled", False)
    async with Client(server) as client:
        await client.call_tool("trw_session_start", {"query": "masking probe"})  # no start-reminder prefix
        await _record_probes(client)
        first = await client.call_tool("trw_recall", _QUERY)
        again = await client.call_tool("trw_recall", _QUERY)

    assert _rows(again) == _rows(first)
    _assert_text_carries(again)
