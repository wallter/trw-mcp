"""PRD-CORE-280 FR06 -- a split store's one recovery: ``memory migrate --to user --apply`` on a pinned checkout.

A pinned checkout whose project ``memory.db`` still holds rows (an older stdio
server wrote them after the cutover) is told to run exactly that command by
init-project and update-project. It merges the strays into the pinned
namespace through the checkout's existing grant, the destination winning, and
leaves the pin and the token as they are.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from trw_memory.daemon._grants import CHECKOUT_TOKEN_RELPATH
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests._memory_fixtures import MemoryDaemon
from tests.test_store_migration import _IDS, _client, _namespace, _served, checkout, daemon  # noqa: F401
from trw_mcp.state._store_migration import MigrationRefusedError, apply_migration, holds_rows

pytestmark = pytest.mark.integration


def _write_strays(root: Path, *entries: MemoryEntry) -> None:
    store = SQLiteBackend(root / ".trw" / "memory" / "memory.db")
    try:
        for entry in entries:
            store.store(entry)
    finally:
        store.close()


def _run_the_printed_command(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run, from the checkout, exactly the command the split-store warning prints -- parsed out of it, not retyped."""
    import re
    import shlex

    from trw_mcp.bootstrap._namespace_pin import pin_empty_checkout
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    warned: dict[str, list[str]] = {}
    pin_empty_checkout(root, warned)
    (line,) = warned["warnings"]
    program, *argv = shlex.split(re.findall(r"`([^`]+)`", line)[0])
    assert program == "trw-mcp"
    monkeypatch.chdir(root)
    args = _build_arg_parser().parse_args(argv)
    SUBCOMMAND_HANDLERS[args.command](args)


@pytest.fixture
def split(checkout: Path, daemon: MemoryDaemon) -> Path:
    """A migrated checkout, token in place, whose project store then gained a stray and a colliding row.

    The colliding row is an exact copy of the migrated one, read back before the move: a
    collision that differs in any field refuses the cutover (FR03), below.
    """
    store = SQLiteBackend(checkout / ".trw" / "memory" / "memory.db")
    try:
        migrated = store.get("L-a", namespace="default")
        vector = store.get_vector_records(["L-a"], namespace="default").get("L-a")
    finally:
        store.close()
    assert migrated is not None
    apply_migration(checkout / ".trw")
    assert not holds_rows(checkout / ".trw" / "memory" / "memory.db"), "a completed apply empties the project store"
    _write_strays(
        checkout,
        MemoryEntry(id="L-stray", content="written by an old stdio server", namespace="default"),
        migrated,
        MemoryEntry(id="C-1", content="decoy", namespace="default", metadata={"system_canary": "true"}),
    )
    if vector is not None:
        store = SQLiteBackend(checkout / ".trw" / "memory" / "memory.db")
        try:
            store.upsert_vector("L-a", list(vector.embedding), namespace="default", provenance=vector.provenance)
        finally:
            store.close()
    return checkout


def test_the_printed_command_merges_a_pinned_checkouts_strays_and_keeps_its_pin_and_token(
    split: Path,
    daemon: MemoryDaemon,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.bootstrap._namespace_pin import pin_empty_checkout

    namespace = _namespace(split)
    config, token = (split / ".trw" / "config.yaml").read_bytes(), (split / CHECKOUT_TOKEN_RELPATH).read_bytes()

    _run_the_printed_command(split, monkeypatch)

    page = asyncio.run(_client(split).list_page(namespace, 100, None))
    held = {row["id"]: row["content"] for row in page["entries"]}
    assert sorted(held) == sorted([*_IDS, "L-stray"]), "the stray moved; the decoy stayed behind"
    assert held["L-a"] == "learning L-a", "the destination wins a collision"
    assert _served(split, namespace)[0] == 4
    assert (split / ".trw" / "config.yaml").read_bytes() == config
    assert (split / CHECKOUT_TOKEN_RELPATH).read_bytes() == token
    after: dict[str, list[str]] = {}
    pin_empty_checkout(split, after)
    assert not after.get("warnings"), "the split-store warning is gone"
    with pytest.raises(MigrationRefusedError, match="nothing to do"):
        apply_migration(split / ".trw")


def test_a_stray_that_differs_from_the_row_the_namespace_holds_refuses_and_moves_nothing(
    checkout: Path, daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Destination-wins would drop the stray's content, and the migration cannot know which copy is newer (FR03).

    The collisions are compared before anything is copied, so the refusal is atomic:
    the stray with no twin is not copied either, and the checkout's store stays whole.
    """
    apply_migration(checkout / ".trw")
    _write_strays(
        checkout,
        MemoryEntry(id="L-stray", content="written by an old stdio server", namespace="default"),
        MemoryEntry(id="L-a", content="an edited copy of a migrated row", namespace="default"),
    )
    namespace = _namespace(checkout)
    before = (checkout / ".trw" / "memory" / "memory.db").read_bytes()
    rows_before = _served(checkout, namespace)[0]

    with pytest.raises(SystemExit):
        _run_the_printed_command(checkout, monkeypatch)

    page = asyncio.run(_client(checkout).list_page(namespace, 100, None))
    held = {row["id"]: row["content"] for row in page["entries"]}
    assert sorted(held) == sorted(_IDS), "nothing moved, not even the stray with no twin"
    assert _served(checkout, namespace)[0] == rows_before
    assert held["L-a"] == "learning L-a", "the namespace's row is not overwritten"
    assert (checkout / ".trw" / "memory" / "memory.db").read_bytes() == before, "the checkout's store is not emptied"


def test_the_strays_pass_manifest_rolls_back_everything_after_the_store_was_emptied(
    checkout: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._memory_daemon import running_daemon
    from tests.test_store_migration import _project
    from trw_mcp.models.config import reload_config
    from trw_mcp.state._store_migration import rollback_migration

    user_dir = tmp_path / "userhome"
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", lambda _paths: None)
    reload_config()
    memory = checkout / ".trw" / "memory"
    with running_daemon(user_dir):
        apply_migration(checkout / ".trw")
        _write_strays(
            checkout, MemoryEntry(id="L-stray", content="written by an old stdio server", namespace="default")
        )
        before = set(memory.glob("migration-*.json"))
        _run_the_printed_command(checkout, monkeypatch)
        (manifest_path,) = set(memory.glob("migration-*.json")) - before
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["namespace"] == _namespace(checkout)
    assert [row["id"] for row in manifest["rows"]] == ["L-stray"]
    assert not holds_rows(memory / "memory.db"), "the strays pass emptied the project store"

    restored = rollback_migration(checkout / ".trw", manifest_path)  # the daemon is stopped

    ids, vectors, edges = _project(checkout)
    assert (restored, ids) == (4, [*_IDS, "L-stray"])
    assert sorted(vectors) == _IDS and [vectors[i].index(1.0) for i in _IDS] == [0, 1, 2]
    assert edges == {("L-a", "L-b")}
    assert "project_namespace" not in (checkout / ".trw" / "config.yaml").read_text(encoding="utf-8")
    reload_config()


def test_a_pin_its_grant_does_not_cover_is_refused_and_nothing_changes(split: Path) -> None:
    from trw_mcp.state._store_migration import _set_pin

    _set_pin(split / ".trw", "project:elsewhere-22222222")
    config = (split / ".trw" / "config.yaml").read_bytes()

    with pytest.raises(MigrationRefusedError, match="does not cover project:elsewhere-22222222"):
        apply_migration(split / ".trw")

    assert (split / ".trw" / "config.yaml").read_bytes() == config
    assert holds_rows(split / ".trw" / "memory" / "memory.db"), "the strays are still where they were"
