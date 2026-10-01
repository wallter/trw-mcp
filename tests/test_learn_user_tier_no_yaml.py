"""L-5ist regression: a user-tier learning leaves no YAML copy in the PROJECT's ``.trw/learnings``.

Root cause: ``execute_learn`` wrote its YAML sidecar (and an ``index.yaml`` row) for EVERY stored
learning, including rows routed to the user tier (``scope="user"``, or ``scope="auto"`` resolving to
``user:*``). The installed gitignore template tracks ``learnings/``, so a personal row was committed to
whatever repo the session ran in. The store holds the canonical row; a user-tier row gets no project copy.

``store_learning`` marks a row it routed to the user tier (``tier="user"``); ``execute_learn`` reads it. A
store that says nothing fails closed for a row that asked for ``scope="user"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._tier_routing import USER_NAMESPACE

PROJECT_NAMESPACE = "project:demo"


def _entry_yaml_files(trw_dir: Path) -> list[Path]:
    entries_dir = trw_dir / "learnings" / "entries"
    return [p for p in entries_dir.glob("*.yaml") if p.name != "index.yaml"] if entries_dir.is_dir() else []


def _index_ids(trw_dir: Path) -> list[str]:
    index = trw_dir / "learnings" / "index.yaml"
    if not index.is_file():
        return []
    from trw_mcp.state.persistence import FileStateReader

    return [str(row["id"]) for row in FileStateReader().read_yaml(index).get("entries", [])]


def _run_learn(trw_dir: Path, **overrides: object) -> dict[str, object]:
    from trw_mcp.tools._learn_impl import execute_learn

    kwargs: dict[str, object] = {
        "summary": "l5ist user-tier probe summary",
        "detail": "detail body for the l5ist user-tier no-yaml probe",
        "trw_dir": trw_dir,
        "config": TRWConfig(dedup_enabled=False, embeddings_enabled=False),
        "tags": ["l5ist-probe"],
        "impact": 0.5,
        "_check_and_handle_dedup": lambda *a, **k: None,  # no duplicate: proceed to the store
    }
    kwargs.update(overrides)
    result = execute_learn(**kwargs)  # type: ignore[arg-type]
    assert isinstance(result, dict)
    return result  # type: ignore[return-value]


def _seam_store(*, marks_user: bool) -> object:
    """A ``store_learning`` stand-in that answers ``recorded``; *marks_user* adds the ``tier="user"`` a routed row carries."""

    def _store(_trw_dir: Path, *, learning_id: str, **_kw: object) -> dict[str, object]:
        answer: dict[str, object] = {
            "learning_id": learning_id,
            "path": f"sqlite://{learning_id}",
            "status": "recorded",
            "distribution_warning": "",
        }
        if marks_user:
            answer["tier"] = "user"
        return answer

    return _store


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    (path / "learnings" / "entries").mkdir(parents=True)
    return path


class TestUserTierWritesNoProjectYaml:
    def test_scope_user_writes_no_yaml_and_no_index_row(self, trw_dir: Path) -> None:
        """What would make this fail: any path that writes the sidecar for a ``scope="user"`` row."""
        result = _run_learn(trw_dir, scope="user", _adapter_store=_seam_store(marks_user=True))

        assert result["status"] == "recorded"
        assert _entry_yaml_files(trw_dir) == []
        assert _index_ids(trw_dir) == []
        assert str(result["path"]).startswith("sqlite://"), "the response names the store row, not a project file"

    def test_auto_scope_resolved_to_user_writes_no_yaml(self, trw_dir: Path) -> None:
        """``scope="auto"`` that the router sends to ``user:*`` is just as personal as an explicit user write."""
        result = _run_learn(trw_dir, scope="auto", _adapter_store=_seam_store(marks_user=True))

        assert result["status"] == "recorded"
        assert _entry_yaml_files(trw_dir) == []
        assert _index_ids(trw_dir) == []

    def test_a_store_that_does_not_mark_the_tier_fails_closed_for_scope_user(self, trw_dir: Path) -> None:
        """When the namespace cannot be resolved, a row that ASKED for scope user is not copied into the project."""
        result = _run_learn(trw_dir, scope="user", _adapter_store=_seam_store(marks_user=False))

        assert result["status"] == "recorded"
        assert _entry_yaml_files(trw_dir) == []

    def test_project_tier_row_still_writes_yaml(self, trw_dir: Path) -> None:
        """Control: a project row keeps its sidecar and index row."""
        result = _run_learn(trw_dir, scope="project", _adapter_store=_seam_store(marks_user=False))

        assert result["status"] == "recorded"
        assert len(_entry_yaml_files(trw_dir)) == 1
        assert _index_ids(trw_dir) == [str(result["learning_id"])]

    def test_auto_scope_the_store_did_not_mark_user_keeps_the_sidecar(self, trw_dir: Path) -> None:
        """Control: only an explicit ``scope="user"`` fails closed; an unmarked ``auto`` row is a project row."""
        _run_learn(trw_dir, scope="auto", _adapter_store=_seam_store(marks_user=False))

        assert len(_entry_yaml_files(trw_dir)) == 1


class TestRealStoreLearningMarksTheTier:
    """The seam answers above are only as good as ``store_learning``'s own user-tier mark; drive the real one."""

    @pytest.fixture
    def fake_store(self, monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
        store = FakeMemoryStore()
        monkeypatch.setattr(
            "trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (store, PROJECT_NAMESPACE)
        )
        return store

    def test_store_learning_marks_a_user_row_and_leaves_a_project_row_unmarked(
        self, trw_dir: Path, fake_store: FakeMemoryStore
    ) -> None:
        from trw_mcp.state.memory_adapter import store_learning

        user = store_learning(trw_dir, "L-user1", "prefer small commits", "operator preference", scope="user")
        project = store_learning(trw_dir, "L-proj1", "project row", "detail", scope="project")

        assert user["tier"] == "user"
        assert "tier" not in project
        assert ("put", ("prefer small commits", USER_NAMESPACE)) in fake_store.calls
        assert ("put", ("project row", PROJECT_NAMESPACE)) in fake_store.calls

    def test_execute_learn_end_to_end_user_row_has_no_project_copy(
        self, trw_dir: Path, fake_store: FakeMemoryStore
    ) -> None:
        user = _run_learn(trw_dir, summary="prefer small commits", detail="operator preference", scope="user")
        assert user["status"] == "recorded"
        assert _entry_yaml_files(trw_dir) == []
        assert _index_ids(trw_dir) == []
        assert (USER_NAMESPACE, str(user["learning_id"])) in fake_store.rows

        project = _run_learn(trw_dir, summary="project row", detail="detail", scope="project")
        assert project["status"] == "recorded"
        assert len(_entry_yaml_files(trw_dir)) == 1
        assert _index_ids(trw_dir) == [str(project["learning_id"])]
