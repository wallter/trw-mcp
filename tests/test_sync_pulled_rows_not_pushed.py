"""A row team or company sync PULLED is never pushed back, and a dirty one is cleaned (SYNC amplifier).

Recalling a pulled team row used to make it dirty (the counters bumped its ``sync_seq``); the push then uploaded it under
its local id (``team-sync-L-x``), the platform stored a NEW learning, the next host pulled and pushed THAT
(``team-sync-team-sync-L-x``), and so on. Counters no longer dirty a row (trw-memory), and the dirty page leaves every
pulled row out: it is not this host's to upload, and an edit made here to a teammate's learning stays here. Pulled rows
that an earlier release left dirty are acknowledged clean, not pushed.
"""

from __future__ import annotations

from unittest.mock import patch

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from tests._test_sync_client_support import _make_config


def _client(tmp_path):  # type: ignore[no-untyped-def]
    from trw_mcp.sync.client import BackendSyncClient

    with patch("trw_mcp.sync.client.resolve_sync_client_id", return_value="sync-client-1"):
        return BackendSyncClient(_make_config(), tmp_path)


def _put(store: FakeMemoryStore, entry_id: str, source: str | None = None) -> None:
    store.put(f"learning {entry_id}", FAKE_NAMESPACE, {"entry_id": entry_id})
    if source is not None:
        row = store.rows[(FAKE_NAMESPACE, entry_id)]
        store.rows[(FAKE_NAMESPACE, entry_id)] = row.model_copy(update={"source": source})


def test_a_pulled_row_is_left_out_of_the_dirty_page(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    _put(fake_memory_store, "L-mine")
    _put(fake_memory_store, "team-sync-L-theirs", "team_sync")
    _put(fake_memory_store, "company-L-theirs", "company_sync")

    page = _client(tmp_path)._get_dirty_entries()

    assert [entry.id for entry in page] == ["L-mine"]


def test_a_pulled_row_an_earlier_release_left_dirty_is_cleaned_not_pushed(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    """Upgrade: 113 pulled rows on one host were already dirty from recall bookkeeping."""
    for n in range(3):
        _put(fake_memory_store, f"team-sync-L-{n}", "team_sync")
    client = _client(tmp_path)

    assert client._get_dirty_entries() == []
    assert fake_memory_store.page_dirty(FAKE_NAMESPACE, 100) == []  # acknowledged: not dirty any more


def test_a_page_full_of_pulled_rows_does_not_hide_the_rows_behind_it(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    for n in range(5):
        _put(fake_memory_store, f"team-sync-L-{n}", "team_sync")
    _put(fake_memory_store, "L-mine")

    page = _client(tmp_path)._get_dirty_entries(page_size=2)

    assert [entry.id for entry in page] == ["L-mine"]


# ── luna red-team: which rows count as pulled ───────────────────────────────────────────────────────────────────


def test_an_own_row_whose_id_looks_pulled_is_still_pushed(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    """Explicit provenance wins: a learning THIS host wrote under a ``team-sync-`` id is its own and is uploaded."""
    _put(fake_memory_store, "team-sync-notes", "human")
    _put(fake_memory_store, "team-sync-tool", "tool")

    page = _client(tmp_path)._get_dirty_entries()

    assert sorted(entry.id for entry in page) == ["team-sync-notes", "team-sync-tool"]
    assert {key for key, seq in fake_memory_store.synced.items() if seq} == set()  # neither was acknowledged clean


def test_the_id_prefix_decides_only_when_the_row_records_no_provenance() -> None:
    from types import SimpleNamespace

    from trw_mcp.sync._pulled import is_pulled

    assert is_pulled(SimpleNamespace(id="team-sync-L-x", source="", source_identity="", client_profile="")) is True
    assert (
        is_pulled(SimpleNamespace(id="team-sync-L-x", source="agent", source_identity="", client_profile="")) is False
    )
    assert is_pulled(SimpleNamespace(id="L-x", source="team_sync", source_identity="", client_profile="")) is True
    assert is_pulled(SimpleNamespace(id="L-x", source="", source_identity="company_sync", client_profile="")) is True
    assert is_pulled(SimpleNamespace(id="L-x", source="agent", source_identity="team_sync", client_profile="")) is False


def test_a_pulled_row_that_is_saved_and_restored_stays_pulled(fake_memory_store: FakeMemoryStore) -> None:
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.sync._pulled import is_pulled

    _put(fake_memory_store, "team-sync-L-x", "team_sync")
    row = fake_memory_store.rows[(FAKE_NAMESPACE, "team-sync-L-x")]

    restored = MemoryEntry.model_validate(row.model_dump(mode="json"))

    assert is_pulled(restored) is True


def test_a_long_run_of_pulled_rows_is_cleaned_before_the_page_gives_up(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    """The old 20-page cap hid every own row behind 40+ pulled ones; the loop now runs until the page has one."""
    for n in range(60):
        _put(fake_memory_store, f"team-sync-L-{n}", "team_sync")
    _put(fake_memory_store, "L-mine")

    page = _client(tmp_path)._get_dirty_entries(page_size=2)

    assert [entry.id for entry in page] == ["L-mine"]


def test_hitting_the_safety_cap_is_logged_and_never_loops_forever(
    fake_memory_store: FakeMemoryStore, tmp_path, monkeypatch
) -> None:
    from structlog.testing import capture_logs

    from trw_mcp.sync import _client_runtime

    monkeypatch.setattr(_client_runtime, "_PULLED_CLEAN_PASSES", 3)
    for n in range(20):
        _put(fake_memory_store, f"team-sync-L-{n}", "team_sync")

    with capture_logs() as logs:
        page = _client(tmp_path)._get_dirty_entries(page_size=2)

    assert page == []
    assert any(event["event"] == "sync_dirty_page_pass_cap_reached" and event["passes"] == 3 for event in logs)


# ── an edited pulled row stays dirty (its edit is protected); only unchanged ones are acknowledged ──────────────


def _stamp(store: FakeMemoryStore, entry_id: str, *, baseline_of: str | None = None) -> None:
    """Record, as a pull does, the fingerprint of the content the row had when it was pulled."""
    from trw_mcp.sync._pulled import BASELINE_KEY, content_fingerprint

    row = store.rows[(FAKE_NAMESPACE, entry_id)]
    reference = row if baseline_of is None else row.model_copy(update={"content": baseline_of})
    store.rows[(FAKE_NAMESPACE, entry_id)] = row.model_copy(
        update={"metadata": {**row.metadata, BASELINE_KEY: content_fingerprint(reference)}}
    )


def test_a_pulled_row_whose_content_still_matches_its_pull_is_acknowledged(
    fake_memory_store: FakeMemoryStore, tmp_path
) -> None:
    _put(fake_memory_store, "team-sync-L-same", "team_sync")
    _stamp(fake_memory_store, "team-sync-L-same")

    assert _client(tmp_path)._get_dirty_entries() == []
    assert fake_memory_store.page_dirty(FAKE_NAMESPACE, 100) == []


def test_a_pulled_row_edited_here_stays_dirty_and_is_not_pushed(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    _put(fake_memory_store, "team-sync-L-edited", "team_sync")
    _stamp(fake_memory_store, "team-sync-L-edited", baseline_of="what the teammate wrote")  # content differs now

    assert _client(tmp_path)._get_dirty_entries() == []
    assert [e.id for e in fake_memory_store.page_dirty(FAKE_NAMESPACE, 100)] == ["team-sync-L-edited"]


def test_edited_pulled_rows_do_not_hide_the_rows_behind_them(fake_memory_store: FakeMemoryStore, tmp_path) -> None:
    for n in range(5):
        _put(fake_memory_store, f"team-sync-L-{n}", "team_sync")
        _stamp(fake_memory_store, f"team-sync-L-{n}", baseline_of="the teammate's version")
    _put(fake_memory_store, "L-mine")

    page = _client(tmp_path)._get_dirty_entries(page_size=1)

    assert [entry.id for entry in page] == ["L-mine"]
    assert len(fake_memory_store.page_dirty(FAKE_NAMESPACE, 100)) == 6  # the five edits are still protected


def test_the_fingerprint_follows_content_and_ignores_counters_and_metadata() -> None:
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.sync._pulled import content_fingerprint

    base = MemoryEntry(id="x", content="a", detail="d", tags=["t"])

    assert content_fingerprint(base) == content_fingerprint(
        base.model_copy(update={"recall_count": 9, "metadata": {"k": "v"}})
    )
    assert content_fingerprint(base) != content_fingerprint(base.model_copy(update={"detail": "edited"}))
    assert content_fingerprint(base) != content_fingerprint(base.model_copy(update={"tags": ["t", "u"]}))
