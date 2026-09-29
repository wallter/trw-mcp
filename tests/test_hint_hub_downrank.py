"""ANCHOR-HUB-DOWNRANK: on a hub file the pre-edit hint shows text-matched lessons first.

A hub is a file with more than ``hint_hub_threshold`` active anchored lessons
(calibrated in an internal pre-registration).
On a hub, a lesson whose only link to the file is its anchor moves after every
text-matched lesson; a non-hub file, the flag turned off, and an old or absent
daemon all give today's hint exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._anchor_daemon_fake import AnchoredDaemon, TextOnlyDaemon, lesson, use_daemon
from tests._structlog_capture import captured_structlog  # noqa: F401  (fixture, imported by name)
from trw_mcp.models import config as config_module
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint
from trw_mcp.tools._learnings_collector import LearningSummary

_FILE = "httpx/_client.py"


class _LimitRecordingDaemon(AnchoredDaemon):
    """Records the ``limit`` of every ``memory_anchored`` call."""

    def __init__(self, rows: list[Any], **kwargs: Any) -> None:
        super().__init__(rows, **kwargs)
        self.anchored_limits: list[int] = []

    async def anchored(self, namespace: str, file: str, limit: int, status: str | None = None) -> Any:
        self.anchored_limits.append(limit)
        return await super().anchored(namespace, file, limit, status)


def _anchored(n: int) -> list[Any]:
    return [lesson(f"L-a{i}", f"anchored finding {i}", anchors=(_FILE,), importance=0.9 - i * 0.01) for i in range(n)]


def _text(n: int) -> list[Any]:
    return [lesson(f"L-t{i}", f"_client.py text note {i}", importance=0.5) for i in range(n)]


def _use_config(monkeypatch: pytest.MonkeyPatch, **fields: Any) -> None:
    config = TRWConfig(**fields)
    monkeypatch.setattr(config_module, "get_config", lambda: config)


def _hint(repo: Path) -> list[LearningSummary]:
    return compute_before_edit_hint(file_path=str(repo / _FILE), repo_root=str(repo)).learnings


def _ids(repo: Path) -> list[str]:
    return [item.id for item in _hint(repo)]


@pytest.mark.parametrize(
    ("rows", "threshold", "expected"),
    [
        # 5 anchored > 3: the hub's text lessons lead, anchor-only lessons follow.
        (_anchored(5) + _text(2), 3, ["L-t0", "L-t1", "L-a0", "L-a1", "L-a2"]),
        # A full anchored page (13 > 12) used to end collection before any text query.
        (_anchored(13) + _text(2), 12, ["L-t0", "L-t1", "L-a0", "L-a1", "L-a2"]),
        # An anchored lesson the text route also finds keeps its text position.
        (
            [
                *_anchored(4),
                lesson("L-both", "_client.py anchored and named", anchors=(_FILE,), importance=0.1),
                *_text(1),
            ],
            3,
            ["L-both", "L-t0", "L-a0", "L-a1", "L-a2"],
        ),
        # No text hit: the held anchored lessons still fill the hint, in the daemon's order.
        (_anchored(6), 3, ["L-a0", "L-a1", "L-a2", "L-a3", "L-a4"]),
    ],
    ids=["hub_text_first", "hub_full_page", "hub_overlap_text_position", "hub_without_text_hits"],
)
def test_hub_file_shows_text_matched_lessons_first(
    rows: list[Any], threshold: int, expected: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _use_config(monkeypatch, hint_hub_threshold=threshold)
    daemon = _LimitRecordingDaemon(rows)
    use_daemon(monkeypatch, repo, daemon)

    assert _ids(repo) == expected
    # The flag-off page first; a second, threshold + 1 fetch only when that page is full and cannot decide.
    full = sum(1 for row in rows if row.anchors) >= 10
    assert daemon.anchored_limits == ([10, threshold + 1] if full and threshold >= 10 else [10])


def test_hub_downrank_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
) -> None:
    repo = tmp_path / "repo"
    _use_config(monkeypatch, hint_hub_threshold=3)
    use_daemon(monkeypatch, repo, AnchoredDaemon(_anchored(5) + _text(1)))

    _ids(repo)

    events = [e for e in captured_structlog if e.get("event") == "anchor_hub_downranked"]
    assert [(e["file"], e["held"], e["threshold"]) for e in events] == [(_FILE, 5, 3)]
    assert events[0]["disable"] == "disable with hint_hub_downrank: false in .trw/config.yaml"


@pytest.mark.parametrize(
    ("anchored", "threshold", "hub"),
    [(3, 3, False), (4, 3, True), (12, 12, False), (13, 12, True)],
    ids=["at_threshold", "one_over", "full_page_at_threshold", "full_page_one_over"],
)
def test_threshold_boundary(
    anchored: int, threshold: int, hub: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _use_config(monkeypatch, hint_hub_threshold=threshold)
    use_daemon(monkeypatch, repo, AnchoredDaemon(_anchored(anchored) + _text(2)))

    assert (_ids(repo)[0] == "L-t0") is hub


_NON_HUB_CASES: dict[str, list[Any]] = {
    "text_only": _text(2),
    "few_anchors": _anchored(3) + _text(3),
    "full_anchored_page": _anchored(12) + _text(2),
    "overlap": [lesson("L-both", "_client.py anchored and named", anchors=(_FILE,)), *_anchored(1), *_text(1)],
    "no_hits": [lesson("L-x", "unrelated")],
}


def _run(repo: Path, monkeypatch: pytest.MonkeyPatch, rows: list[Any], **fields: Any) -> tuple[Any, ...]:
    _use_config(monkeypatch, **fields)
    daemon = AnchoredDaemon(rows)
    use_daemon(monkeypatch, repo, daemon)
    return (_hint(repo), list(daemon.calls))


@pytest.mark.parametrize("case", sorted(_NON_HUB_CASES))
def test_non_hub_file_is_byte_identical_to_flag_off(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _NON_HUB_CASES[case]
    repo = tmp_path / "repo"

    shipped_hint, shipped_calls = _run(repo, monkeypatch, rows)
    off_hint, off_calls = _run(repo, monkeypatch, rows, hint_hub_downrank=False)

    assert shipped_hint == off_hint
    # Only a full anchored page adds a call: the one count fetch, right after the flag-off page.
    extra = [shipped_calls[0]] if case == "full_anchored_page" else []
    assert shipped_calls == off_calls[:1] + extra + off_calls[1:]


def test_flag_off_keeps_anchored_first_on_a_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _use_config(monkeypatch, hint_hub_downrank=False, hint_hub_threshold=3)
    daemon = _LimitRecordingDaemon(_anchored(12) + _text(2))
    use_daemon(monkeypatch, repo, daemon)

    assert _ids(repo) == ["L-a0", "L-a1", "L-a2", "L-a3", "L-a4"]
    assert daemon.anchored_limits == [10]
    assert ("memory_recall", "project:anchor-test") not in daemon.calls


def test_old_daemon_without_anchored_gives_the_text_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _use_config(monkeypatch, hint_hub_threshold=3)
    use_daemon(monkeypatch, repo, TextOnlyDaemon(_anchored(6) + _text(2)))

    assert _ids(repo) == ["L-t0", "L-t1"]


def test_daemon_not_serving_anchored_gives_the_text_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastmcp.exceptions import ToolError

    repo = tmp_path / "repo"
    _use_config(monkeypatch, hint_hub_threshold=3)
    error = ToolError("Unknown tool: 'memory_anchored'")
    use_daemon(monkeypatch, repo, AnchoredDaemon(_anchored(6) + _text(2), error=error))

    assert _ids(repo) == ["L-t0", "L-t1"]


def test_absent_daemon_gives_no_lessons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import _store_selection
    from trw_mcp.state._store_selection import StoreUnavailableError

    repo = tmp_path / "repo"
    _use_config(monkeypatch, hint_hub_threshold=3)
    use_daemon(monkeypatch, repo, AnchoredDaemon([]))

    def _unreachable(_trw_dir: Path) -> Any:
        raise StoreUnavailableError("the memory daemon is not running.")

    monkeypatch.setattr(_store_selection, "selected_store", _unreachable)

    assert _ids(repo) == []


def test_shipped_defaults() -> None:
    config = TRWConfig()

    assert config.hint_hub_downrank is True
    assert config.hint_hub_threshold == 67


@pytest.mark.parametrize(
    ("fields", "status", "names"),
    [
        ({}, "PASS", ["more than 67 anchored", "disable with hint_hub_downrank: false in .trw/config.yaml"]),
        ({"hint_hub_threshold": 8}, "PASS", ["more than 8 anchored"]),
        ({"hint_hub_downrank": False}, "SKIP", ["enable with hint_hub_downrank: true in .trw/config.yaml"]),
    ],
    ids=["on_default", "on_custom_threshold", "off"],
)
def test_doctor_row_names_the_flag(fields: dict[str, Any], status: str, names: list[str], tmp_path: Path) -> None:
    from trw_mcp.server import _subcommands_doctor as doctor

    # Resolved as the doctor loop resolves it: the registry row names a function in the module's globals.
    fn_name = dict(doctor._CHECKS)["hint_hub_downrank"]
    result = getattr(doctor, fn_name)(tmp_path, TRWConfig(**fields))

    assert result.status == status
    assert all(name in result.message for name in names)


def _page_dependent_recall(anchored: int, text: int) -> tuple[Any, list[tuple[str, int]]]:
    """A ``recall_learnings`` whose anchored order depends on ``max_results``, as recall's is not promised not to."""
    calls: list[tuple[str, int]] = []

    def recall(query: str, *, max_results: int, anchor_file: str | None = None) -> list[dict[str, object]]:
        calls.append(("anchored" if anchor_file else "text", max_results))
        if anchor_file is None:
            return [{"id": f"L-t{i}", "summary": f"text {i}"} for i in range(text)][:max_results]
        rows = [{"id": f"L-a{i}", "summary": f"anchored {i}"} for i in range(anchored)]
        return (rows if max_results <= 10 else rows[::-1])[:max_results]

    return recall, calls


@pytest.mark.parametrize("anchored", [10, 12], ids=["exactly_full_page", "more_than_a_page"])
def test_non_hub_full_page_keeps_the_flag_off_page(anchored: int, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import learning_injection
    from trw_mcp.tools._learnings_collector import collect_learnings

    results = {}
    for flag in (False, True):
        recall, calls = _page_dependent_recall(anchored, text=2)
        monkeypatch.setattr(learning_injection, "recall_learnings", recall)
        _use_config(monkeypatch, hint_hub_downrank=flag, hint_hub_threshold=67)
        results[flag] = (collect_learnings(["x.py"], anchor_file="x.py"), calls)

    assert results[True][0] == results[False][0]
    assert [item.id for item in results[True][0]] == ["L-a0", "L-a1", "L-a2", "L-a3", "L-a4"]
    # The flag-off page comes first; only a full page pays the second, threshold + 1 fetch.
    assert results[False][1] == [("anchored", 10)]
    assert results[True][1] == [("anchored", 10), ("anchored", 68)]


def test_partial_page_makes_no_second_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import learning_injection
    from trw_mcp.tools._learnings_collector import collect_learnings

    recall, calls = _page_dependent_recall(9, text=2)
    monkeypatch.setattr(learning_injection, "recall_learnings", recall)
    _use_config(monkeypatch, hint_hub_threshold=67)

    collect_learnings(["x.py"], anchor_file="x.py")

    assert [c for c in calls if c[0] == "anchored"] == [("anchored", 10)]
