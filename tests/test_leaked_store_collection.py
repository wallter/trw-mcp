"""The conftest collects a store a test left open, so the next test can set up on the same path.

An unclosed ``SQLiteBackend`` keeps its store lock until the cyclic collector runs. A later open that finds another
file under the lock's name is refused by design, which made a test fail in setup because of the test before it, and
only when no collection happened in between. The collector is off here, so nothing depends on its timing.
"""

from __future__ import annotations

import gc
import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture()
def collector_off() -> Iterator[None]:
    """Only an explicit collection can release a leaked store while this is active."""
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


def _leak_a_store_and_replace_its_directory(root: Path) -> Path:
    """Open a store under *root*, drop it unclosed, and put a new directory under its name; returns the db path.

    The old directory is moved aside, not deleted: its lock file stays alive, so the recreated one is another file on
    every platform (after a delete, Linux usually hands the same inode back and nothing is refused).
    """
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    db = root / "store" / "memory.db"
    backend = SQLiteBackend(db)
    del backend
    (root / "store").rename(root / "moved-aside")
    return db


@pytest.mark.usefixtures("collector_off")
def test_leaked_store_replaced_path_reopens_only_after_conftest_collection(tmp_path: Path) -> None:
    from trw_memory.exceptions import UnsupportedStorageError
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from tests.conftest import _collect_leaked_stores

    # Reached through a symlink, as macOS reaches its tmp: the registry names resolved paths, the caller may not.
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    db = _leak_a_store_and_replace_its_directory(tmp_path / "alias")

    with pytest.raises(UnsupportedStorageError, match="was replaced while this process held it"):
        SQLiteBackend(db)
    assert _collect_leaked_stores(tmp_path / "another-test") is False
    with pytest.raises(UnsupportedStorageError, match="was replaced while this process held it"):
        SQLiteBackend(db)

    assert _collect_leaked_stores(tmp_path / "alias") is True
    reopened = SQLiteBackend(db)
    try:
        assert db.exists()
    finally:
        reopened.close()


def test_conftest_collection_keeps_referenced_store_open_and_skips_intact_or_closed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_memory import _store_lock
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    from tests.conftest import _collect_leaked_stores

    collections: list[int] = []
    real_collect = gc.collect
    monkeypatch.setattr(gc, "collect", lambda *args: collections.append(1) or real_collect(*args))
    db = tmp_path / "store" / "memory.db"
    lock_name = os.path.realpath(db) + ".oplock"
    backend = SQLiteBackend(db)
    try:
        # Held and its lock file intact: the next open of this name is not refused, so nothing is collected.
        assert _collect_leaked_stores(tmp_path) is False
        # Held and its lock file gone: a collection runs, and the store, still referenced, keeps its hold.
        (tmp_path / "store").rename(tmp_path / "moved-aside")
        assert _collect_leaked_stores(tmp_path) is True
        assert _store_lock._FILES[lock_name].users == 1
    finally:
        backend.close()
    # Closed by the test: no hold is counted, whatever happened to the file (the common case by far).
    assert _collect_leaked_stores(tmp_path) is False
    assert _collect_leaked_stores(None) is False
    assert len(collections) == 1


@pytest.fixture(scope="module")
def collector_off_for_the_module() -> Iterator[None]:
    """The collector stays off between the two cases below, so only the conftest hook can save the second."""
    was_enabled = gc.isenabled()
    gc.disable()
    yield
    if was_enabled:
        gc.enable()


@pytest.fixture()
def unclosed_store(tmp_path: Path, collector_off_for_the_module: None) -> Path:
    """A store opened in setup and never closed: the fixture shape this file's hook exists for."""
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    db = tmp_path / "store" / "memory.db"
    SQLiteBackend(db)
    return db


# End to end: the name is longer than the 30 characters pytest keeps for a tmp_path, so both cases get the same
# directory name, the first one's having been removed when it passed (tmp_path_retention_policy = "failed").
@pytest.mark.parametrize("case", ["leaves_its_store_open", "sets_up_on_the_same_path"])
def test_parametrized_cases_sharing_a_tmp_path_name_each_set_up_their_store(unclosed_store: Path, case: str) -> None:
    assert unclosed_store.exists(), case
