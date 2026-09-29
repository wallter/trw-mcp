"""Characterization of the pre-edit hint's anchored + text merge (PRD-CORE-332 FR06/FR07).

Pins ``compute_before_edit_hint``'s lessons on fixed fixtures (anchored hits,
text-only hits, overlap, a full anchored page, 1-3 anchors) and every degrade
path (a client without ``anchored``, a daemon that does not serve
``memory_anchored``, another anchored error, a text error, no daemon).

Written for CORE-332-CONCURRENT, which measured running the anchored lookup
concurrently with the text recall and found it slower (the daemon calls contend
and the client-side work shares the GIL), so the collector stays sequential. Any
later change to how the requests are sent must keep these outputs exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._anchor_daemon_fake import AnchoredDaemon, TextOnlyDaemon, lesson, use_daemon
from tests._structlog_capture import captured_structlog  # noqa: F401  (fixture, imported by name)
from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint

_FILE = "httpx/_client.py"


def _hint_ids(repo: Path) -> list[str]:
    return [item.id for item in compute_before_edit_hint(file_path=str(repo / _FILE), repo_root=str(repo)).learnings]


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry.get("event") == name]


def _anchored(n: int, *, prefix: str = "L-a") -> list[Any]:
    """*n* rows anchored to the file, importance descending, text never naming it."""
    return [
        lesson(f"{prefix}{i}", f"anchored finding {i}", anchors=(_FILE,), importance=0.9 - i * 0.01) for i in range(n)
    ]


def _text(n: int) -> list[Any]:
    """*n* rows whose text names the basename, anchored nowhere."""
    return [lesson(f"L-t{i}", f"_client.py text note {i}", importance=0.5) for i in range(n)]


_CASES: dict[str, tuple[list[Any], list[str]]] = {
    "text_only_hits": (_text(2), ["L-t0", "L-t1"]),
    "one_anchor": (_anchored(1) + _text(2), ["L-a0", "L-t0", "L-t1"]),
    "two_anchors": (_anchored(2) + _text(2), ["L-a0", "L-a1", "L-t0", "L-t1"]),
    "three_anchors": (_anchored(3) + _text(3), ["L-a0", "L-a1", "L-a2", "L-t0", "L-t1"]),
    # A row that is both anchored and a text hit is shown once, in its anchored place.
    "overlap": (
        [lesson("L-both", "_client.py anchored and named", anchors=(_FILE,), importance=0.8), *_anchored(1), *_text(1)],
        ["L-a0", "L-both", "L-t0"],
    ),
    # A full anchored page (2 x DEFAULT_TOP_N) ends collection: no text hit is shown.
    "full_anchored_page": (_anchored(12) + _text(2), ["L-a0", "L-a1", "L-a2", "L-a3", "L-a4"]),
    "no_hits": ([lesson("L-x", "unrelated")], []),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_hint_lessons_are_pinned(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows, expected = _CASES[case]
    repo = tmp_path / "repo"
    daemon = AnchoredDaemon(rows)
    use_daemon(monkeypatch, repo, daemon)

    assert _hint_ids(repo) == expected
    # A full anchored page costs one more anchored call that only counts rows for the hub test
    # (hint_hub_downrank, ANCHOR-HUB-DOWNRANK); the page the hint shows is still the first one.
    anchored_calls = 2 if case == "full_anchored_page" else 1
    assert daemon.files == [_FILE] * anchored_calls
    assert daemon.calls.count(("memory_anchored", "project:anchor-test")) == anchored_calls


def test_client_without_anchored_is_the_text_only_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
) -> None:
    repo = tmp_path / "repo"
    use_daemon(monkeypatch, repo, TextOnlyDaemon(_anchored(2) + _text(2)))

    assert _hint_ids(repo) == ["L-t0", "L-t1"]
    assert len(_events(captured_structlog, "anchor_lookup_unsupported")) == 1
    assert _events(captured_structlog, "recall_learnings_failed") == []


@pytest.mark.parametrize(
    ("message", "unsupported", "failed"),
    [
        ("Unknown tool: 'memory_anchored'", 1, 0),  # the old daemon: probe says unsupported
        ("Error calling tool 'memory_anchored': boom", 0, 1),  # any other anchored error
    ],
)
def test_anchored_errors_degrade_to_the_text_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_structlog: list[dict[str, Any]],
    message: str,
    unsupported: int,
    failed: int,
) -> None:
    from fastmcp.exceptions import ToolError

    repo = tmp_path / "repo"
    use_daemon(monkeypatch, repo, AnchoredDaemon(_anchored(2) + _text(2), error=ToolError(message)))

    assert _hint_ids(repo) == ["L-t0", "L-t1"]
    assert len(_events(captured_structlog, "anchor_lookup_unsupported")) == unsupported
    assert [e["query"] for e in _events(captured_structlog, "recall_learnings_failed")] == [_FILE] * failed


class _FailingTextDaemon(AnchoredDaemon):
    """``memory_recall`` fails for every query; ``memory_anchored`` answers."""

    async def recall(self, query: str, namespace: str, **kwargs: Any) -> Any:
        raise RuntimeError("recall exploded")


def test_text_errors_keep_the_anchored_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
) -> None:
    repo = tmp_path / "repo"
    use_daemon(monkeypatch, repo, _FailingTextDaemon(_anchored(2) + _text(2)))

    assert _hint_ids(repo) == ["L-a0", "L-a1"]
    assert [e["query"] for e in _events(captured_structlog, "recall_learnings_failed")] == [
        str(repo / _FILE),
        "_client.py",
    ]


def test_daemon_absent_yields_no_lessons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
) -> None:
    from trw_mcp.state import _store_selection
    from trw_mcp.state._store_selection import StoreUnavailableError

    repo = tmp_path / "repo"
    use_daemon(monkeypatch, repo, AnchoredDaemon([]))

    def _unreachable(_trw_dir: Path) -> Any:
        raise StoreUnavailableError("the memory daemon is not running. Run trw-mcp doctor.")

    monkeypatch.setattr(_store_selection, "selected_store", _unreachable)

    assert _hint_ids(repo) == []
    # One degrade per request (anchored + path + basename), none escalated to a collector failure.
    assert len(_events(captured_structlog, "memory_recall_store_unavailable")) == 3
    assert _events(captured_structlog, "recall_learnings_failed") == []


def test_collector_without_anchor_file_stays_sequential_and_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers without an anchor (session status, risk report) keep the early stop: no extra query runs."""
    from trw_mcp.state import learning_injection
    from trw_mcp.tools._learnings_collector import collect_learnings

    asked: list[str] = []

    def _recall(query: str, **_: Any) -> list[dict[str, object]]:
        asked.append(query)
        return [{"id": f"{query}-{i}", "summary": "s"} for i in range(10)]

    monkeypatch.setattr(learning_injection, "recall_learnings", _recall)

    assert [item.id for item in collect_learnings(["q1", "q2", "q3"])] == [f"q1-{i}" for i in range(5)]
    assert asked == ["q1"]
