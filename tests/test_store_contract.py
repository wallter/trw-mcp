"""One contract, every ``MemoryStore`` implementation (PRD-CORE-280 FR01).

The daemon implementation (PRD-CORE-298 FR01) runs against a real daemon
process; an implementation that passes here is interchangeable behind
``selected_store``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from trw_memory.daemon import DaemonClient, DaemonPaths, mint_grant
from trw_memory.lifecycle.correction import LearningPatch
from trw_memory.models.memory import MemoryEntry

from tests._memory_daemon import running_daemon
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state._daemon_store import DaemonMemoryStore
from trw_mcp.state._recall_admission import RecallAdmission
from trw_mcp.state._store_selection import MemoryStore, RecallSpec
from trw_mcp.state._tier_routing import USER_NAMESPACE

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def daemon_paths(tmp_path_factory: pytest.TempPathFactory) -> Iterator[DaemonPaths]:
    with running_daemon(tmp_path_factory.mktemp("daemon")) as paths:
        yield paths


@pytest.fixture(params=["fake", "daemon"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> MemoryStore:
    if request.param == "fake":
        return FakeMemoryStore()
    # One daemon serves the module; every test writes its own ids.
    paths = request.getfixturevalue("daemon_paths")
    client = DaemonClient(mint_grant(paths, ["default", USER_NAMESPACE], root=tmp_path / "checkout"), paths=paths)
    return DaemonMemoryStore(client, "default")


def test_a_put_persists_assertions(store: MemoryStore) -> None:
    """``StoreRequest.assertions`` survives a ``put``, readable back via ``get``.

    The row goes to ``user:local``: the module shares one daemon, and an assertion row
    in ``default`` would be counted by the verify sweep below.
    """
    from trw_memory.models.memory import Assertion, AssertionType

    store.put(
        "Contract claim",
        USER_NAMESPACE,
        {
            "entry_id": "L-c1a",
            "assertions": [Assertion(type=AssertionType.GREP_PRESENT, target="x.py", pattern="needle")],
        },
    )

    entry = store.get("L-c1a")
    assert entry is not None
    assert len(entry.assertions) == 1
    assert entry.assertions[0].target == "x.py"


def test_a_put_row_is_found_by_id(store: MemoryStore) -> None:
    result = store.put("Contract summary", "default", {"entry_id": "L-c1", "importance": 0.8, "tags": ["t"]})

    assert (result["status"], result["memory_id"], result["namespace"]) == ("stored", "L-c1", "default")
    entry = store.get("L-c1")
    assert isinstance(entry, MemoryEntry)
    assert (entry.content, entry.namespace, entry.importance) == ("Contract summary", "default", 0.8)


def test_a_put_rejection_status_never_persists_a_row(store: MemoryStore) -> None:
    """A refused write answers with a status, never a raise, and leaves no row (PRD-CORE-280 e3).

    The daemon runs trw-memory's real write gate; the fake has none, so it is told to refuse.
    """
    if isinstance(store, FakeMemoryStore):
        store.next_put_status = "invalid"

    result = store.put("rejected content", "default", {"entry_id": "L-contract-rej", "confidence": "verified"})

    assert result["status"] == "invalid"
    assert store.get("L-contract-rej") is None


def test_a_correction_changes_only_the_named_fields(store: MemoryStore) -> None:
    store.put("Before", "default", {"entry_id": "L-c2", "importance": 0.4, "detail": "kept"})

    result = store.correct("L-c2", LearningPatch(impact=0.9))

    assert (result["status"], result["learning_id"]) == ("updated", "L-c2")
    entry = store.get("L-c2")
    assert entry is not None
    assert (entry.importance, entry.detail, entry.content) == (0.9, "kept", "Before")


def test_a_correction_carries_the_fields_maintenance_writes(store: MemoryStore) -> None:
    """Anchor re-verification, dedup merges and promotion marks write through ``correct`` too."""
    store.put("Maintained", "default", {"entry_id": "L-c4", "metadata": {"kept": "yes"}})

    result = store.correct(
        "L-c4",
        LearningPatch(
            anchor_validity=0.5,
            evidence=["e1"],
            recurrence=3,
            merged_from=["L-old"],
            metadata_add={"promoted_to_claude_md": "true"},
        ),
    )

    entry = store.get("L-c4")
    assert result["status"] == "updated"
    assert entry is not None
    assert (entry.anchor_validity, entry.evidence, entry.recurrence, entry.merged_from) == (0.5, ["e1"], 3, ["L-old"])
    assert entry.metadata["promoted_to_claude_md"] == "true"
    assert entry.metadata.get("kept") == "yes"


def test_a_correction_naming_supersedes_closes_the_prior_entry(store: MemoryStore) -> None:
    """A patch's ``supersedes`` closes THAT entry's validity window, not the corrected one's."""
    store.put("Old claim", "default", {"entry_id": "L-c5-old"})
    store.put("New claim", "default", {"entry_id": "L-c5-new"})

    result = store.correct("L-c5-new", LearningPatch(supersedes="L-c5-old"))

    assert result["status"] == "updated"
    prior = store.get("L-c5-old")
    successor = store.get("L-c5-new")
    assert prior is not None and successor is not None
    assert prior.invalid_from is not None
    assert prior.invalidated_by == "L-c5-new"
    assert successor.invalid_from is None


def test_a_correction_of_an_unknown_id_is_not_found(store: MemoryStore) -> None:
    assert store.correct("L-missing-c", LearningPatch(impact=0.9))["status"] == "not_found"


def test_an_unknown_id_is_none(store: MemoryStore) -> None:
    assert store.get("L-missing") is None


def test_count_reports_the_namespaces_row_total(store: MemoryStore) -> None:
    # A warm-up put first: the SqliteMemoryStore path runs the first write of a
    # fresh backend through trw_memory.security._runtime_canary.initialize_canaries,
    # which seeds canary_injection_rate (default 5) pinned decoy rows into this
    # SAME namespace (CANARY_NAMESPACE == DEFAULT_NAMESPACE == "default") on the
    # very first put to that store and quarantine-db pairing, idempotently after
    # that. Measuring "before" only after that one-time seeding keeps the delta
    # below exact for every implementation (sqlite/fake/daemon).
    store.put("Warm-up", "default", {"entry_id": "L-c4b"})
    before = store.count("default")

    store.put("Counted one", "default", {"entry_id": "L-c5"})
    store.put("Counted two", "default", {"entry_id": "L-c6"})

    assert store.count("default") == before + 2


def test_health_counts_the_namespaces_entries_and_derives_a_shared_tag_relation(store: MemoryStore) -> None:
    # Deltas: the module shares one daemon, so other tests' rows are in "default" too.
    store.put("Warm-up", "default", {"entry_id": "L-c4b"})
    before = store.health("default")

    store.put("Healthy one", "default", {"entry_id": "L-h1", "tags": ["health-contract", "shared"]})
    store.put("Healthy two", "default", {"entry_id": "L-h2", "tags": ["health-contract", "shared"]})
    after = store.health("default")

    assert set(after) == {"entries", "synced", "edges", "has_relations", "embedded", "max_recall_count"}
    assert after["entries"] == before["entries"] + 2
    assert after["synced"] == before["synced"]
    assert after["has_relations"] is True


def test_a_user_namespace_row_is_found_by_id_and_corrected_in_place(store: MemoryStore) -> None:
    store.put("Portable", USER_NAMESPACE, {"entry_id": "L-c3", "importance": 0.5})

    assert store.correct("L-c3", LearningPatch(impact=0.7))["status"] == "updated"

    entry = store.get("L-c3")
    assert entry is not None
    assert (entry.namespace, entry.importance) == (USER_NAMESPACE, 0.7)


def test_the_project_row_wins_when_both_namespaces_hold_an_id(store: MemoryStore) -> None:
    store.put("Portable", USER_NAMESPACE, {"entry_id": "L-c4"})
    store.put("Project", "default", {"entry_id": "L-c4"})

    entry = store.get("L-c4")
    assert entry is not None
    assert (entry.namespace, entry.content) == ("default", "Project")


# -- sync (PRD-CORE-298 FR01): push pages dirty rows, pull finds and applies ------------------

_SYNC_NS = "default"


def _drain(store: MemoryStore) -> None:
    """Mark every row already dirty, so the page holds only this test's rows.

    The first write seeds the store's canary rows, so write once before draining.
    """
    store.put("Warm", _SYNC_NS, {"entry_id": "L-warm"})
    while page := store.page_dirty(_SYNC_NS, 500):
        store.mark_synced(_SYNC_NS, page)


def test_put_rows_are_paged_oldest_first_until_marked_synced(store: MemoryStore) -> None:
    _drain(store)
    for index in range(3):
        store.put(f"Dirty {index}", _SYNC_NS, {"entry_id": f"L-s{index}"})

    page = store.page_dirty(_SYNC_NS, 2)
    assert [e.id for e in page] == ["L-s0", "L-s1"]
    assert store.mark_synced(_SYNC_NS, page) == 2
    assert [e.id for e in store.page_dirty(_SYNC_NS, 10)] == ["L-s2"]


def test_a_row_edited_after_it_was_paged_is_not_marked_synced(store: MemoryStore) -> None:
    """The ack carries the paged revision; an edit made during the push keeps the row dirty."""
    _drain(store)
    store.put("Before the push", _SYNC_NS, {"entry_id": "L-race"})
    page = store.page_dirty(_SYNC_NS, 10)
    store.correct("L-race", LearningPatch(detail="edited while the push was in flight"))

    assert store.mark_synced(_SYNC_NS, page) == 0
    assert [(e.id, e.detail) for e in store.page_dirty(_SYNC_NS, 10)] == [
        ("L-race", "edited while the push was in flight")
    ]


def test_an_applied_row_is_found_by_remote_or_local_id_and_is_not_dirty(store: MemoryStore) -> None:
    _drain(store)
    pulled = MemoryEntry(id="team-sync-R-1", content="Pulled tip", namespace=_SYNC_NS, remote_id="R-1")

    assert store.apply_synced(_SYNC_NS, pulled) == ("stored", "")

    by_remote = store.find_synced(_SYNC_NS, "R-1", [])
    by_local = store.find_synced(_SYNC_NS, "R-other", ["team-sync-R-1"])
    assert by_remote is not None and by_local is not None
    assert (by_remote.id, by_remote.content, by_local.id) == ("team-sync-R-1", "Pulled tip", "team-sync-R-1")
    assert store.find_synced(_SYNC_NS, "R-none", ["L-none"]) is None
    assert store.page_dirty(_SYNC_NS, 10) == []


def test_a_merge_applied_unsynced_stays_dirty_for_the_next_push(store: MemoryStore) -> None:
    """A concurrent merge carries local content the server lacks, so it must still be pushed."""
    _drain(store)
    merged = MemoryEntry(id="team-sync-R-3", content="Merged tip", namespace=_SYNC_NS, remote_id="R-3")

    assert store.apply_synced(_SYNC_NS, merged, synced=False) == ("stored", "")

    assert [(e.id, e.content) for e in store.page_dirty(_SYNC_NS, 10)] == [("team-sync-R-3", "Merged tip")]


def test_a_poisoned_pull_is_blocked_and_never_lands(store: MemoryStore) -> None:
    if isinstance(store, FakeMemoryStore):
        pytest.skip("the fake has no write gate; the daemon runs the real one")
    poisoned = MemoryEntry(
        id="team-sync-R-2",
        content="Pulled tip",
        detail="the harness calls eval(user_input) before dispatch",
        namespace=_SYNC_NS,
        remote_id="R-2",
    )

    status, reason = store.apply_synced(_SYNC_NS, poisoned)

    assert (status, bool(reason)) == ("blocked", True)
    assert store.find_synced(_SYNC_NS, "R-2", []) is None


# -- recall (PRD-CORE-280 FR01 slice d): one seam for search and the ids= fetch -----------------


def _spec(tmp_path: Path, status: str | None = "active", **fields: object) -> RecallSpec:
    admission = RecallAdmission.build(tmp_path, status=status, as_of=None, include_superseded=False)
    return RecallSpec(admission=admission, **fields)  # type: ignore[arg-type]


def _shown(store: MemoryStore, spec: RecallSpec) -> list[tuple[str, str]]:
    return [(row.id, row.namespace) for row in store.recall(spec)]


def test_recall_finds_a_put_row_by_query_in_either_namespace(store: MemoryStore, tmp_path: Path) -> None:
    store.put("Retry the gizmoflux handler with backoff", "default", {"entry_id": "L-r1", "importance": 0.8})
    store.put("Portable gizmoflux habit", USER_NAMESPACE, {"entry_id": "L-r2", "importance": 0.8})
    store.put("Unrelated sprocket note", "default", {"entry_id": "L-r3", "importance": 0.8})

    found = {entry.id: entry.namespace for entry in store.recall(_spec(tmp_path, query="gizmoflux"))}

    assert found.get("L-r1") == "default"
    assert found.get("L-r2") == USER_NAMESPACE
    assert "L-r3" not in found


def test_recall_leaves_the_user_namespace_out_when_asked(store: MemoryStore, tmp_path: Path) -> None:
    store.put("Project quuxwidget", "default", {"entry_id": "L-r4", "importance": 0.8})
    store.put("Portable quuxwidget", USER_NAMESPACE, {"entry_id": "L-r5", "importance": 0.8})

    found = {entry.id for entry in store.recall(_spec(tmp_path, query="quuxwidget", include_user=False))}

    assert "L-r4" in found and "L-r5" not in found


def test_recall_by_ids_returns_admitted_rows_in_request_order(store: MemoryStore, tmp_path: Path) -> None:
    store.put("First zorblat", "default", {"entry_id": "L-r6", "importance": 0.8})
    store.put("Second zorblat", USER_NAMESPACE, {"entry_id": "L-r7", "importance": 0.8})
    store.put("Retired zorblat", "default", {"entry_id": "L-r8", "importance": 0.8})
    store.correct("L-r8", LearningPatch(status="obsolete"))

    rows = store.recall(_spec(tmp_path, ids=("L-r7", "L-missing", "L-r8", "L-r6")))

    # A missing id and a row admission refuses are indistinguishable: both are absent.
    assert [(row.id, row.namespace) for row in rows] == [("L-r7", USER_NAMESPACE), ("L-r6", "default")]


def test_an_id_in_both_namespaces_is_shown_once_as_the_row_ids_hydrates(store: MemoryStore, tmp_path: Path) -> None:
    store.put("Portable blorfquix", USER_NAMESPACE, {"entry_id": "L-t1", "importance": 0.8})
    store.put("Project blorfquix", "default", {"entry_id": "L-t1", "importance": 0.8})

    searched = [row for row in _shown(store, _spec(tmp_path, query="blorfquix")) if row[0] == "L-t1"]

    assert searched == [("L-t1", "default")]
    assert _shown(store, _spec(tmp_path, ids=("L-t1",))) == searched


def test_a_refused_project_twin_leaves_the_user_row_on_both_paths(store: MemoryStore, tmp_path: Path) -> None:
    store.put("Portable glimmerwort", USER_NAMESPACE, {"entry_id": "L-t2", "importance": 0.8})
    store.put("Project glimmerwort", "default", {"entry_id": "L-t2", "importance": 0.8})
    store.correct("L-t2", LearningPatch(status="obsolete"))  # the project row: get() resolves it first

    searched = [row for row in _shown(store, _spec(tmp_path, query="glimmerwort")) if row[0] == "L-t2"]

    assert searched == [("L-t2", USER_NAMESPACE)]
    assert _shown(store, _spec(tmp_path, ids=("L-t2",))) == searched


def test_a_user_hit_whose_id_hydrates_to_the_project_row_is_not_shown(store: MemoryStore, tmp_path: Path) -> None:
    """``ids=`` would hydrate the admitted project twin, which the query did not match: no stub."""
    store.put("Portable snazzleberry", USER_NAMESPACE, {"entry_id": "L-t3", "importance": 0.8})
    store.put("Project note on something else", "default", {"entry_id": "L-t3", "importance": 0.8})

    assert "L-t3" not in {row[0] for row in _shown(store, _spec(tmp_path, query="snazzleberry"))}
    assert _shown(store, _spec(tmp_path, ids=("L-t3",))) == [("L-t3", "default")]


def test_user_rows_are_capped_on_search_and_on_the_ids_fetch(
    store: MemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state import _recall_take

    monkeypatch.setattr(_recall_take, "user_recall_cap", lambda: 2)
    for index in range(3):
        store.put(f"Portable capzorian {index}", USER_NAMESPACE, {"entry_id": f"L-u{index}", "importance": 0.8})

    searched = [row for row in _shown(store, _spec(tmp_path, query="capzorian")) if row[1] == USER_NAMESPACE]
    fetched = _shown(store, _spec(tmp_path, ids=("L-u2", "L-u0", "L-u1")))

    assert len(searched) == 2
    assert fetched == [("L-u2", USER_NAMESPACE), ("L-u0", USER_NAMESPACE)]


def test_a_refused_row_on_the_first_page_does_not_hide_an_admitted_one(store: MemoryStore, tmp_path: Path) -> None:
    store.put("Older kept", "default", {"entry_id": "L-p1", "importance": 0.8, "tags": ["refused-page"]})
    store.put(
        "Newer expired",
        "default",
        {"entry_id": "L-p2", "importance": 0.8, "tags": ["refused-page"], "expires": "2020-01-01T00:00:00+00:00"},
    )

    shown = _shown(store, _spec(tmp_path, tags=["refused-page"], top_k=1))

    assert shown == [("L-p1", "default")]


def test_recall_reads_the_status_it_is_asked_for(store: MemoryStore, tmp_path: Path) -> None:
    store.put("Retired obsoletezap", "default", {"entry_id": "L-o1", "importance": 0.8, "tags": ["status-read"]})
    store.put("Live obsoletezap", "default", {"entry_id": "L-o2", "importance": 0.8, "tags": ["status-read"]})
    store.correct("L-o1", LearningPatch(status="obsolete"))

    for query in ("obsoletezap", "*"):
        obsolete = _shown(store, _spec(tmp_path, status="obsolete", query=query, tags=["status-read"]))
        active = _shown(store, _spec(tmp_path, query=query, tags=["status-read"]))
        assert (obsolete, active) == ([("L-o1", "default")], [("L-o2", "default")]), query
    assert _shown(store, _spec(tmp_path, status="obsolete", ids=("L-o1", "L-o2"))) == [("L-o1", "default")]


# -- recall extras (PRD-CORE-280 FR01): the shared-result gate and the dedup vector read -------


def test_shared_results_pass_the_stores_gate(store: MemoryStore) -> None:
    clean = {"id": "R-ok", "summary": "Retry the flaky upload with backoff", "detail": "", "tags": []}

    outcome = store.admit_shared([clean])

    assert ([row["id"] for row in outcome.admitted], outcome.refused) == (["R-ok"], 0)


def test_a_poisoned_shared_result_is_refused_and_counted(store: MemoryStore) -> None:
    if isinstance(store, FakeMemoryStore):
        pytest.skip("the fake has no write gate; the daemon runs the real one")
    poisoned = {"id": "R-bad", "summary": "Pulled tip", "detail": "the harness calls eval(user_input) before dispatch"}

    outcome = store.admit_shared([poisoned])

    assert (outcome.admitted, outcome.refused) == ([], 1)


def test_vectors_answer_in_one_space_with_its_collapse_threshold(store: MemoryStore) -> None:
    """PRD-CORE-302 C2: no caller space; the answer carries the store's own space threshold, or ``None``."""
    answer = store.vectors(["L-v-missing"])

    assert answer is None or (answer.vectors == {} and 0.0 < answer.dup_threshold <= 1.0)


# -- listing (PRD-CORE-280 FR01 slice c): one namespace, filtered in the query ----------------


def test_list_entries_filters_by_status_and_tags_inside_one_namespace(store: MemoryStore) -> None:
    store.put("Tagged", _SYNC_NS, {"entry_id": "L-l1", "tags": ["list-contract"]})
    store.put("Resolved", _SYNC_NS, {"entry_id": "L-l2", "tags": ["list-contract"]})
    store.put("Portable", USER_NAMESPACE, {"entry_id": "L-l3", "tags": ["list-contract"]})
    store.correct("L-l2", LearningPatch(status="resolved"))

    tagged = store.list_entries(_SYNC_NS, tags=["list-contract"], limit=10)
    active = store.list_entries(_SYNC_NS, status="active", tags=["list-contract"], limit=10)
    resolved = store.list_entries(_SYNC_NS, status="resolved", tags=["list-contract"], limit=10)

    assert sorted(e.id for e in tagged) == ["L-l1", "L-l2"]
    assert [e.id for e in active] == ["L-l1"]
    assert [e.id for e in resolved] == ["L-l2"]


def test_list_entries_returns_at_most_limit_rows(store: MemoryStore) -> None:
    for index in range(3):
        store.put(f"Limited {index}", _SYNC_NS, {"entry_id": f"L-lim{index}", "tags": ["list-limit"]})

    assert len(store.list_entries(_SYNC_NS, tags=["list-limit"], limit=2)) == 2


# -- exact-content dedup (PRD-CORE-280 FR01 slice c) ------------------------------------------


def test_find_duplicate_names_an_active_exact_copy_in_the_namespace_only(store: MemoryStore) -> None:
    store.put("Dup summary", _SYNC_NS, {"entry_id": "L-dup", "detail": "dup detail"})
    store.put("Retired summary", _SYNC_NS, {"entry_id": "L-ret", "detail": "d"})
    store.correct("L-ret", LearningPatch(status="obsolete"))

    assert store.find_duplicate(_SYNC_NS, "Dup summary", "dup detail") == "L-dup"
    assert store.find_duplicate(_SYNC_NS, "Dup summary", "other detail") is None
    assert store.find_duplicate(USER_NAMESPACE, "Dup summary", "dup detail") is None
    assert store.find_duplicate(_SYNC_NS, "Retired summary", "d") is None


# -- dedup verdict (PRD-CORE-302 FR01) ---------------------------------------------------------


def test_similar_on_empty_text_gives_no_verdict(store: MemoryStore) -> None:
    """PRD-CORE-302 C1/C4: empty text is no semantic verdict (the caller stores), not a refusal."""
    assert store.similar(_SYNC_NS, "  \n", 0.95, 0.85, 10) is None


# -- maintain-verify (PRD-CORE-280 FR01 slice c) -----------------------------------------------


def test_verify_sweeps_the_namespaces_assertion_rows_against_a_project_root(store: MemoryStore, tmp_path: Path) -> None:
    from trw_memory.lifecycle.verification_pass import VerifySettings

    root = tmp_path / "checkout"
    root.mkdir()
    (root / "present.py").write_text("x = 1\n", encoding="utf-8")
    assertion = {"type": "glob_exists", "pattern": "", "target": "present.py"}
    store.put("Verified by a file", _SYNC_NS, {"entry_id": "L-ver", "assertions": [assertion]})

    summary = store.verify(_SYNC_NS, root, VerifySettings())

    assert summary.entries_processed == 1
    assert summary.entry_failures == 0


def test_assertion_health_counts_the_namespaces_cached_verdicts(store: MemoryStore) -> None:
    """A row's never-checked claim counts as stale; a row without claims is not counted.

    Deltas in ``user:local``: the module shares one daemon, and ``default`` holds the verify sweep's row.
    """
    empty = {"passing": 0, "failing": 0, "stale": 0, "unverifiable": 0, "total": 0}
    before = store.assertion_health(USER_NAMESPACE, 30) or empty

    never_checked = {"type": "glob_exists", "pattern": "", "target": "present.py"}
    store.put("Carries a claim", USER_NAMESPACE, {"entry_id": "L-ah", "assertions": [never_checked]})
    store.put("Carries none", USER_NAMESPACE, {"entry_id": "L-ah2"})

    after = store.assertion_health(USER_NAMESPACE, 30)
    assert after is not None
    assert {key: after[key] - before[key] for key in empty} == {**empty, "stale": 1, "total": 1}


def _counts(store: MemoryStore, namespace: str, entry_id: str) -> tuple[int, int, int]:
    entry = next(e for e in store.list_entries(namespace, limit=1000) if e.id == entry_id)
    return entry.access_count, entry.recall_count, entry.session_count


def test_record_surfaced_counts_each_shown_id_once_where_it_is_owned(store: MemoryStore) -> None:
    """A repeated id counts once; the project row of a twin is counted, never its user copy; an unknown id is skipped."""
    store.put("Project", "default", {"entry_id": "L-rs1"})
    store.put("Portable", USER_NAMESPACE, {"entry_id": "L-rs1"})
    store.put("User only", USER_NAMESPACE, {"entry_id": "L-rs2"})

    store.record_surfaced(["L-rs1", "L-rs1", "L-rs2", "L-rs-missing"])

    assert _counts(store, "default", "L-rs1") == (1, 1, 0)
    assert _counts(store, USER_NAMESPACE, "L-rs1") == (0, 0, 0)
    assert _counts(store, USER_NAMESPACE, "L-rs2") == (1, 1, 0)


def test_record_surfaced_at_session_start_also_counts_the_session(store: MemoryStore) -> None:
    store.put("Shown at start", "default", {"entry_id": "L-rs3"})

    store.record_surfaced(["L-rs3"], session_start=True)

    assert _counts(store, "default", "L-rs3") == (1, 1, 1)


def test_graph_related_of_a_row_without_edges_is_an_empty_complete_window(store: MemoryStore) -> None:
    store.put("Nothing points here", USER_NAMESPACE, {"entry_id": "L-lone"})

    assert store.graph_related(USER_NAMESPACE, "L-lone", 1, None, 10) == ([], False)


def test_graph_backfill_pages_a_namespace_and_says_where_it_stopped(store: MemoryStore) -> None:
    store.put("Swept by the backfill", USER_NAMESPACE, {"entry_id": "L-gbf"})

    first = store.graph_backfill(USER_NAMESPACE, None, 1, None)
    rest = store.graph_backfill(USER_NAMESPACE, first["next"], 10_000, None)

    assert first["processed"] + first["skipped"] + first["failed"] == 1
    assert first["next"] is not None
    assert first["complete"] is False
    assert rest["complete"] is True


# -- maintenance (PRD-CORE-280 FR01 slice e2) ------------------------------------------------------


_POLICY: dict[str, object] = {"enabled": True, "similarity_threshold": 0.75, "min_cluster": 3, "max_per_cycle": 50}


def test_maintain_runs_the_stores_decay_pass_for_the_namespace(store: MemoryStore) -> None:
    store.put("Maintained", _SYNC_NS, {"entry_id": "L-mnt"})

    decay = store.maintain(_SYNC_NS, _POLICY)["passes"]["decay"]

    assert decay["status"] == "ok", decay
    assert decay["processed"] == 0, "a fresh row has not gone unused long enough to decay"


# -- embedder status (PRD-CORE-302 FR05) ---------------------------------------------------------


def test_embedder_status_answers_without_loading_a_model(store: MemoryStore) -> None:
    """The store says whether it can encode; the fake can, the keyword-only test daemon cannot."""
    embedder = store.embedder_status(_SYNC_NS)

    assert isinstance(embedder["available"], bool)
    assert embedder["loaded"] is False, "a status read must never load the model"
    if not embedder["available"]:
        assert embedder["reason"], embedder


@pytest.mark.parametrize("stuck", [False, True], ids=["advances", "stuck"])
def test_a_daemon_verify_calls_until_the_sweep_is_done_and_refuses_one_that_stops_advancing(stuck: bool) -> None:
    """rc9: one memory_verify call verifies a bounded part of the namespace and answers ``next`` (the
    daemon keeps the position); the store calls again until the sweep is complete, and a daemon whose
    position does not move forward is refused rather than looped on."""
    from trw_memory.lifecycle.verification_pass import MaintainVerifySummary, VerifySettings

    counts = MaintainVerifySummary().as_dict()
    replies = [
        {"status": "ok", "summary": {**counts, "entries_processed": 2}, "next": ["ns", "L-2"]},
        {"status": "ok", "summary": {**counts, "entries_processed": 2, "entry_failures": 1}, "next": ["ns", "L-5"]},
        {"status": "ok", "summary": {**counts, "entries_processed": 1}},
    ]
    if stuck:
        replies[1]["next"] = ["ns", "L-2"]
    calls: list[str] = []

    class _Client:
        async def verify(self, namespace, project_root, settings):  # type: ignore[no-untyped-def]
            calls.append(namespace)
            return replies[len(calls) - 1]

    store = DaemonMemoryStore(_Client(), "ns")  # type: ignore[arg-type]
    if stuck:
        with pytest.raises(ValueError, match="did not advance"):
            store.verify("ns", None, VerifySettings())
        return
    summary = store.verify("ns", None, VerifySettings())

    assert len(calls) == 3
    assert (summary.entries_processed, summary.entry_failures) == (5, 1)
