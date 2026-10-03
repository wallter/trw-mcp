"""PRD-SEC-023 FR04 (chokepoint C2): what a session read raises its mark, and what it writes carries the mark.

A session starts at ``team``. A row any recall surface returns raises the mark; nothing lowers it. ``trw_learn`` stamps the new row with the mark
when the mark is above the row's own label, in the project namespace and in ``user:local`` alike, so the label follows the data into every later
session of every project. A session that never rose touches no metadata. No daemon and no embedder: ``FakeMemoryStore`` and a temporary
``TRW_USER_DIR``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from trw_memory.labels import Level, Surface
from trw_memory.models.memory import MemoryEntry

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state._recall_admission import label_scope
from trw_mcp.state._session_mark import reset_session_mark, session_mark


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "user-base"
    base.mkdir()
    monkeypatch.setenv("TRW_USER_DIR", str(base))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    reset_session_mark()
    return base


def _labels(base: Path, text: str) -> None:
    path = base / "labels.yaml"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _row(entry_id: str, namespace: str = "default", **metadata: str) -> MemoryEntry:
    return MemoryEntry(id=entry_id, content=f"learning {entry_id}", namespace=namespace, metadata=dict(metadata))


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeMemoryStore:
    fake = FakeMemoryStore()
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (fake, "default"))
    monkeypatch.setattr("trw_mcp.state._memory_recall.passive_learnings_allowed", lambda: True)
    monkeypatch.setattr("trw_mcp.tools.knowledge.resolve_trw_dir", lambda: tmp_path / ".trw")
    (tmp_path / ".trw" / "memory").mkdir(parents=True)
    return fake


def _recall(tmp_path: Path, surface: Surface = Surface.AGENT) -> list[dict[str, object]]:
    from trw_mcp.state._memory_recall import recall_learnings

    with label_scope(surface):
        return recall_learnings(tmp_path / ".trw", "*", status=None, max_results=25)


def _learn(tmp_path: Path, learning_id: str, **kwargs: Any) -> dict[str, object]:
    from trw_mcp.state.memory_adapter import store_learning

    return store_learning(tmp_path / ".trw", learning_id, f"summary {learning_id}", "detail", **kwargs)


# ── the mark rises with what a recall returns ────────────────────────────────


def test_a_fresh_session_is_at_team_and_a_team_recall_leaves_it_there(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    store.rows[("default", "L-a")] = _row("L-a")

    assert [r["id"] for r in _recall(tmp_path)] == ["L-a"]

    assert session_mark().level is Level.TEAM and session_mark().reported() is None


def test_recalling_a_personal_row_raises_the_mark_and_a_later_team_recall_does_not_lower_it(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    store.rows[("default", "L-p")] = _row("L-p", trw_label="personal")
    store.rows[("default", "L-t")] = _row("L-t")

    _recall(tmp_path)
    assert session_mark().level is Level.PERSONAL

    store.rows.pop(("default", "L-p"))
    _recall(tmp_path)
    assert session_mark().level is Level.PERSONAL, "nothing lowers the mark inside a process"


def test_a_row_the_labels_withheld_does_not_raise_the_mark(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    """The mark follows what the session READ. A sensitive row no surface returned was never read."""
    store.rows[("default", "L-s")] = _row("L-s", trw_label="sensitive")
    store.rows[("default", "L-t")] = _row("L-t")

    _recall(tmp_path)

    assert session_mark().level is Level.TEAM


def test_the_auto_surface_raises_the_mark_only_when_the_user_opted_personal_rows_into_it(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    store.rows[("default", "L-p")] = _row("L-p", trw_label="personal")

    _recall(tmp_path, Surface.AUTO)
    assert session_mark().level is Level.TEAM, "personal rows are not shown unasked by default"

    _labels(user_dir, "version: 1\nauto_surface_max: personal\nagent_max: personal\n")
    _recall(tmp_path, Surface.AUTO)
    assert session_mark().level is Level.PERSONAL


def test_the_ids_and_graph_paths_raise_the_mark_too(user_dir: Path, store: FakeMemoryStore, tmp_path: Path) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import recall_by_ids
    from trw_mcp.tools.knowledge import graph_related

    store.rows[("default", "L-p")] = _row("L-p", trw_label="personal")
    recall_by_ids(tmp_path / ".trw", get_config(), ["L-p"], status=None)
    assert session_mark().level is Level.PERSONAL

    reset_session_mark()
    store.rows[("default", "L-root")] = _row("L-root")
    store.graph_edges[("default", "L-root")] = [("L-p", "related_to", 0.5)]
    graph_related("L-root")
    assert session_mark().level is Level.PERSONAL


def test_a_new_session_starts_at_team_again(user_dir: Path) -> None:
    session_mark().raise_to(Level.SENSITIVE)
    reset_session_mark()
    assert session_mark().level is Level.TEAM


# ── a write carries the mark ─────────────────────────────────────────────────


def test_a_learning_written_after_reading_a_personal_row_is_stamped_in_the_project_namespace(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    session_mark().raise_to(Level.PERSONAL)

    _learn(tmp_path, "L-new", scope="project")

    assert store.rows[("default", "L-new")].metadata["trw_label"] == "personal"


def test_the_same_stamp_follows_a_write_to_the_user_tier(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    session_mark().raise_to(Level.SENSITIVE)

    _learn(tmp_path, "L-user", scope="user")

    (row,) = [r for (ns, _), r in store.rows.items() if ns == "user:local"]
    assert row.metadata["trw_label"] == "sensitive"


def test_a_session_that_never_rose_touches_no_metadata_at_all(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    """NFR01: existing tests assert ``entry.metadata`` exactly."""
    _learn(tmp_path, "L-plain", scope="project")
    _learn(tmp_path, "L-meta", scope="project", metadata={"source": "agent"})

    assert "trw_label" not in store.rows[("default", "L-plain")].metadata
    assert store.rows[("default", "L-meta")].metadata == {"source": "agent"}


def test_a_row_already_at_or_above_the_mark_gets_no_second_stamp(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    _labels(user_dir, "version: 1\nrules:\n  - tags_any: [health]\n    level: sensitive\n")
    session_mark().raise_to(Level.PERSONAL)

    _learn(tmp_path, "L-h", scope="project", tags=["health"])

    assert "trw_label" not in store.rows[("default", "L-h")].metadata, "its own rule label already exceeds the mark"


def test_the_stamp_survives_a_dedup_style_correction_attempt_to_lower_it(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    """The lifecycle rules live in trw-memory (test_labels_derived_rows); here the written row simply keeps what it was given."""
    session_mark().raise_to(Level.PERSONAL)
    _learn(tmp_path, "L-new", scope="project", metadata={"trw_label": "team"})

    assert store.rows[("default", "L-new")].metadata["trw_label"] == "personal", (
        "a caller cannot write a lower stamp than the mark"
    )


# ── the responses report the session label ───────────────────────────────────


def test_trw_recall_and_trw_learn_report_session_label_only_once_the_mark_rose(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    store.rows[("default", "L-p")] = _row("L-p", trw_label="personal")
    trw_dir = tmp_path / ".trw"

    first = execute_recall("*", trw_dir, get_config(), track=False)
    assert first["session_label"] == "personal", "this very recall returned a personal row"

    reset_session_mark()
    store.rows.pop(("default", "L-p"))
    store.rows[("default", "L-t")] = _row("L-t")
    clean = execute_recall("*", trw_dir, get_config(), track=False)
    assert "session_label" not in clean, "a team-level session adds nothing to the response"


def test_trw_learn_reports_session_label_only_once_the_mark_rose(
    user_dir: Path, store: FakeMemoryStore, tmp_path: Path
) -> None:
    before = _learn(tmp_path, "L-before", scope="project")
    assert "session_label" not in before, "a team-level session adds nothing to the response"

    session_mark().raise_to(Level.PERSONAL)
    after = _learn(tmp_path, "L-after", scope="project")

    assert after["session_label"] == "personal"
