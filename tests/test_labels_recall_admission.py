"""PRD-SEC-023 FR03 (chokepoint C1): recall admission is surface-aware, and it runs before any per-namespace cap.

``auto`` (session start, nudges, hook hints, every caller that did not ask) admits rows up to ``auto_surface_max``; ``agent`` (only
``trw_recall``) up to ``agent_max``. ``sensitive`` exceeds both. No daemon and no embedder: the store is ``FakeMemoryStore``, which runs the
real shared admission and caps, and ``TRW_USER_DIR`` points at a temporary directory so the operator's real labels.yaml is never read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from trw_memory.labels import Surface
from trw_memory.models.memory import MemoryEntry

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state._recall_admission import RecallAdmission, label_scope
from trw_mcp.state._recall_take import take_hits

pytestmark = pytest.mark.repo_scan

_TEAM_ROW = "L-team"


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "user-base"
    base.mkdir()
    monkeypatch.setenv("TRW_USER_DIR", str(base))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return base


def _labels(base: Path, text: str) -> None:
    path = base / "labels.yaml"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _row(entry_id: str, namespace: str = "default", **metadata: str) -> MemoryEntry:
    return MemoryEntry(id=entry_id, content=f"learning {entry_id}", namespace=namespace, metadata=dict(metadata))


def _admission(tmp_path: Path, surface: Surface | None = None) -> RecallAdmission:
    kwargs: dict[str, Any] = {} if surface is None else {"surface": surface}
    return RecallAdmission.build(tmp_path / ".trw", status=None, as_of=None, include_superseded=False, **kwargs)


def test_with_no_labels_and_no_stamps_admission_is_unchanged(user_dir: Path, tmp_path: Path) -> None:
    rows = [_row("a"), _row("b", "user:local"), _row("c")]
    assert _admission(tmp_path).admit(rows) == rows
    assert _admission(tmp_path, Surface.AGENT).admit(rows) == rows


def test_auto_withholds_personal_rows_but_the_agent_surface_admits_them(user_dir: Path, tmp_path: Path) -> None:
    team, personal, sensitive = _row("t"), _row("p", trw_label="personal"), _row("s", trw_label="sensitive")
    rows = [team, personal, sensitive]

    assert _admission(tmp_path).admit(rows) == [team], "the default surface is auto"
    assert _admission(tmp_path, Surface.AGENT).admit(rows) == [team, personal]


def test_a_sensitive_row_reaches_no_surface_under_any_policy(user_dir: Path, tmp_path: Path) -> None:
    _labels(
        user_dir,
        "version: 1\nauto_surface_max: personal\nagent_max: personal\nrules:\n  - tags_any: [health]\n    level: sensitive\n",
    )
    secret = MemoryEntry(id="h", content="x", namespace="default", tags=["health"])

    assert _admission(tmp_path).admit([secret]) == []
    assert _admission(tmp_path, Surface.AGENT).admit([secret]) == []


def test_a_user_who_opts_in_sees_personal_rows_on_the_auto_surface(user_dir: Path, tmp_path: Path) -> None:
    _labels(user_dir, "version: 1\nauto_surface_max: personal\nagent_max: personal\n")
    personal = _row("p", trw_label="personal")

    assert _admission(tmp_path).admit([personal]) == [personal]


def test_the_scope_sets_the_surface_and_counts_each_withheld_row_once(user_dir: Path, tmp_path: Path) -> None:
    rows = [_row("s1", trw_label="sensitive"), _row("s2", trw_label="sensitive"), _row("t")]

    with label_scope(Surface.AGENT) as tally:
        admission = _admission(tmp_path)
        assert admission.surface is Surface.AGENT
        admission.admit(rows)
        admission.admit(rows)  # the same rows examined again (a twin lookup) must not inflate the count
        admission.representative([rows[0]])

    assert tally.count == 2
    assert _admission(tmp_path).surface is Surface.AUTO, "the scope ends with the with-block"


def test_withheld_rows_never_crowd_out_admitted_ones_under_a_per_namespace_cap(user_dir: Path, tmp_path: Path) -> None:
    """The labels apply BEFORE the cap: three sensitive rows ranked first must not use up a cap of two."""
    from trw_mcp.state._store_selection import RecallSpec

    rows = [_row(f"s{i}", trw_label="sensitive") for i in range(3)] + [_row("t1"), _row("t2"), _row("t3")]
    spec = RecallSpec(admission=_admission(tmp_path, Surface.AGENT), query="*", top_k=2)

    taken = take_hits(lambda k: rows[:k], spec, cap=2, seen=set())

    assert [row.id for row in taken] == ["t1", "t2"]


def test_a_twin_does_not_claim_an_id_when_its_row_is_withheld(user_dir: Path, tmp_path: Path) -> None:
    """``representative`` is the by-id path too: a withheld row is indistinguishable from an absent one."""
    admission = _admission(tmp_path)
    assert admission.representative([_row("x", trw_label="personal")]) is None
    assert admission.representative([_row("x", trw_label="personal"), _row("x", "user:local")]) is not None


def _fake_store(monkeypatch: pytest.MonkeyPatch, *rows: MemoryEntry) -> FakeMemoryStore:
    store = FakeMemoryStore()
    for row in rows:
        store.rows[(row.namespace, row.id)] = row
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (store, "default"))
    return store


def test_recall_learnings_uses_the_auto_surface_and_trw_recall_the_agent_one(
    user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.tools._recall_impl import execute_recall

    team, personal = _row(_TEAM_ROW), _row("L-personal", trw_label="personal")
    _fake_store(monkeypatch, team, personal)
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.state._memory_recall.passive_learnings_allowed", lambda: True)

    auto_ids = {str(r["id"]) for r in recall_learnings(trw_dir, "*", status=None, max_results=25)}
    assert auto_ids == {_TEAM_ROW}, "session start, nudges and hook hints never show personal rows unasked"

    from trw_mcp.models.config import get_config

    result = execute_recall("*", trw_dir, get_config(), track=False)
    shown = {str(r["id"]) for r in result["learnings"]}
    assert shown == {_TEAM_ROW, "L-personal"}, "trw_recall asked for them"
    assert "withheld_by_label" not in result, "nothing was withheld, so the key is absent (byte-identical output)"


def test_trw_recall_reports_how_many_rows_the_labels_withheld_as_a_count_only(
    user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    _fake_store(monkeypatch, _row(_TEAM_ROW), _row("L-s1", trw_label="sensitive"), _row("L-s2", trw_label="sensitive"))
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.state._memory_recall.passive_learnings_allowed", lambda: True)

    result = execute_recall("*", trw_dir, get_config(), track=False)

    assert {str(r["id"]) for r in result["learnings"]} == {_TEAM_ROW}
    assert result["withheld_by_label"] == 2
    assert "L-s1" not in repr(result) and "sensitive" not in repr(result), (
        "a count, never a name or a level of a particular row"
    )


def test_the_ids_path_withholds_by_label_too(user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import recall_by_ids

    _fake_store(monkeypatch, _row(_TEAM_ROW), _row("L-s", trw_label="sensitive"), _row("L-p", trw_label="personal"))
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)

    result = recall_by_ids(trw_dir, get_config(), [_TEAM_ROW, "L-s", "L-p"], status=None)

    assert {str(r["id"]) for r in result["learnings"]} == {_TEAM_ROW, "L-p"}
    assert result["missing_ids"] == ["L-s"], "a withheld row is indistinguishable from an absent one"
    assert result["withheld_by_label"] == 1


# ── trw_session_start reports a strict policy (FR01) ─────────────────────────


def test_session_start_reports_a_strict_policy_and_says_nothing_otherwise(
    user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools import _ceremony_session_start_steps as steps

    monkeypatch.setattr("trw_mcp.tools._ceremony_helpers.perform_session_recalls", lambda *a, **k: ([], {}))
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: tmp_path / ".trw")

    clean: dict[str, Any] = {}
    steps.step_recall_learnings("*", get_config(), clean, [])  # type: ignore[arg-type]
    assert "labels_policy" not in clean, "no file: the payload is byte-identical to today's"

    _labels(user_dir, "version: 1\nrules:\n  - tags_any: [finance]\n    level: team\n")
    strict: dict[str, Any] = {}
    steps.step_recall_learnings("*", get_config(), strict, [])  # type: ignore[arg-type]

    assert strict["labels_policy"].startswith("strict"), strict
    assert "finance" not in repr(strict), "the notice never quotes the file"


# ── NFR04: nothing in trw-mcp reaches around the module ──────────────────────


def test_trw_mcp_uses_only_the_public_labels_interface_and_never_reads_the_stamp() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if "trw_label" in (text := path.read_text(encoding="utf-8")) or "trw_memory.labels._" in text
    ]
    assert offenders == []


# ── trw_recall(graph_id=...) is an agent surface too (codex r1) ──────────────


def _graph_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[MemoryEntry], edges: list[str]) -> None:
    store = _fake_store(monkeypatch, *rows)
    store.graph_edges[("default", "L-root")] = [(target, "related_to", 0.5) for target in edges]
    monkeypatch.setattr("trw_mcp.tools.knowledge.resolve_trw_dir", lambda: tmp_path / ".trw")


def test_graph_neighbours_are_withheld_by_label_and_counted(
    user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools.knowledge import graph_related

    _graph_store(
        monkeypatch,
        tmp_path,
        [
            _row("L-root"),
            _row("L-team"),
            _row("L-personal", trw_label="personal"),
            _row("L-secret", trw_label="sensitive"),
        ],
        ["L-team", "L-personal", "L-secret"],
    )

    result = graph_related("L-root")

    assert [item["id"] for item in result["related"]] == ["L-team", "L-personal"], (
        "agent surface: personal yes, sensitive never"
    )
    assert result["count"] == 2 and result["withheld_by_label"] == 1
    assert "L-secret" not in repr(result)


def test_a_withheld_graph_root_looks_like_an_absent_one(
    user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools.knowledge import graph_related

    _graph_store(monkeypatch, tmp_path, [_row("L-root", trw_label="sensitive"), _row("L-team")], ["L-team"])

    result = graph_related("L-root")

    assert result["found"] is False and result["related"] == [] and result["count"] == 0
    assert "L-team" not in repr(result), "nothing about a withheld root's neighbourhood may leak"


def test_a_graph_with_nothing_withheld_is_unchanged(
    user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools.knowledge import graph_related

    _graph_store(monkeypatch, tmp_path, [_row("L-root"), _row("L-a"), _row("L-b")], ["L-a", "L-b"])

    result = graph_related("L-root")

    assert [item["id"] for item in result["related"]] == ["L-a", "L-b"]
    assert "withheld_by_label" not in result


def test_a_neighbour_is_labelled_by_the_row_the_graph_returned_not_by_a_twin_with_the_same_id(
    user_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r2: ``store.get(id)`` resolves a bare id, but the graph row lives in the root's namespace. A permitted row in another namespace
    must not vouch for a sensitive twin; a neighbour whose label cannot be read from the namespace the graph used is withheld."""
    from trw_mcp.tools.knowledge import graph_related

    store = _fake_store(
        monkeypatch,
        _row("L-root"),
        _row("L-twin", "default", trw_label="sensitive"),  # the real neighbour, in the root's namespace
        _row("L-twin", "user:local"),  # a permitted twin elsewhere that a bare-id lookup could return instead
    )
    store.graph_edges[("default", "L-root")] = [("L-twin", "related_to", 0.5)]
    monkeypatch.setattr("trw_mcp.tools.knowledge.resolve_trw_dir", lambda: tmp_path / ".trw")
    # a store whose bare-id lookup prefers the user-tier twin
    real_get = store.get
    monkeypatch.setattr(store, "get", lambda entry_id: store.rows.get(("user:local", entry_id)) or real_get(entry_id))

    result = graph_related("L-root")

    assert result["related"] == [] and result["withheld_by_label"] == 1, result
