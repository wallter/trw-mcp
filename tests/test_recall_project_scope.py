"""SYNC-PROJECT-IDENTITY (c): recall ranks another project's team learnings lower, and never hides them.

Pull stores every row (a peer-written project stamp must not be able to delete anything). In recall, a row that
another project provably wrote, meaning a different portable ``git:`` project id, scores a quarter of its relevance
where an unrecorded row scores half, so the operator's other repositories stop crowding this one's results while a
query that names them still finds them. ``team_sync_all_projects`` ranks them like unrecorded rows instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_project_identity import _repo


@pytest.fixture
def mine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A git checkout is the current project; returns its stamped id."""
    from trw_mcp.state import _paths, _project_identity

    repo, _ = _repo(tmp_path / "mine")
    _project_identity.reset_cache()
    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: repo / ".trw")
    return _project_identity.project_id(repo, namespace="project:mine-1")


def _row(row_id: str, score: float, origin: str | None) -> dict[str, object]:
    metadata = {} if origin is None else {"origin_project": origin}
    return {
        "id": f"team-sync-{row_id}",
        "source": "team_sync",
        "namespace": "project:mine-1",
        "combined_score": score,
        "metadata": metadata,
    }


def _order(rows: list[dict[str, object]], **kwargs: object) -> list[str]:
    from trw_mcp.tools._recall_order import order_ranked_for_response

    return [str(r["id"]).removeprefix("team-sync-") for r in order_ranked_for_response(rows, None, **kwargs)]


def test_another_projects_row_ranks_below_an_unrecorded_one_of_equal_relevance(mine: str) -> None:
    rows = [_row("other", 1.0, "git:feedfacefeedface"), _row("unknown", 1.0, "unknown")]

    assert _order(rows) == ["unknown", "other"]


def test_a_strong_match_in_another_project_still_beats_a_weak_unrecorded_one(mine: str) -> None:
    """The query names the other project: its rows are what was asked for."""
    rows = [_row("weak", 0.2, "unknown"), _row("other", 1.0, "git:feedfacefeedface")]

    assert _order(rows) == ["other", "weak"]


def test_this_projects_own_row_from_another_machine_is_not_demoted(mine: str) -> None:
    rows = [_row("other", 1.0, "git:feedfacefeedface"), _row("mine", 1.0, mine), _row("unknown", 1.0, "unknown")]

    assert _order(rows)[0] == "mine"


def test_the_opt_in_ranks_other_projects_like_unrecorded_ones(mine: str) -> None:
    rows = [_row("other", 1.0, "git:feedfacefeedface"), _row("unknown", 1.0, "unknown")]

    assert _order(rows, all_projects=True) == ["other", "unknown"]  # a tie keeps the upstream order


def test_a_host_that_cannot_name_its_own_project_demotes_no_one_further(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a portable id of its own, nothing is provably another project's."""
    from trw_mcp.state import _paths, _project_identity

    plain = tmp_path / "plain"
    plain.mkdir()
    _project_identity.reset_cache()
    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: plain / ".trw")
    rows = [_row("other", 1.0, "git:feedfacefeedface"), _row("unknown", 1.0, "unknown")]

    assert _order(rows) == ["other", "unknown"]
