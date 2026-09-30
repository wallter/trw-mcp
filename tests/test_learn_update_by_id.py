"""PRD-CORE-294 FR03 on the trw-mcp surface: ``trw_learn(learning_id=...)`` corrects or retires by id.

Drives the REAL registered ``trw_learn`` / ``trw_recall`` tool functions over a real
project store. trw-memory's ``memory_update`` half of the same contract lives in
``trw-memory/tests/test_lifecycle_contract.py``.

PRD-CORE-280 slice e1: the "real project store" is now the daemon-backed
checkout (``daemon_checkout``), never an in-process ``memory.db``. The
registered ``trw_learn``/``trw_recall`` tool CLOSURES are still exercised
end-to-end. They resolve ``trw_dir`` via ``resolve_trw_dir()``, which this
suite's process-wide path-isolation stand-in (``tests/_path_isolation.py``)
answers from its own ``current_root()`` rather than ``TRW_PROJECT_ROOT`` --
so the ``trw_project`` fixture repoints ``current_root()`` at the daemon
checkout's own root with the module's own public ``set_current_root``
(idempotent, and restored implicitly next test by the suite's own
``_isolate_trw_dir`` autouse fixture) so the tool closures land on the
daemon-migrated checkout rather than a bare, unmigrated one.

That ``memory_update`` and ``trw_learn`` share one correction, and that trw-memory's
recall mirror never serves a retired row, are trw-memory's
(``test_lifecycle_contract``, ``test_tier_recall_selection``); the end-to-end
retire below covers this surface.
"""

from __future__ import annotations

import asyncio

import pytest

from tests import _path_isolation
from tests._memory_fixtures import DaemonCheckout


@pytest.fixture()
def trw_project(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> DaemonCheckout:
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    _path_isolation.set_current_root(daemon_checkout.trw_dir.parent)
    (daemon_checkout.trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (daemon_checkout.trw_dir / "memory").mkdir(parents=True, exist_ok=True)

    async def _do() -> None:
        await daemon_checkout.client.store(
            "pin the sqlite driver before the migration",
            daemon_checkout.namespace,
            entry_id="L-fix",
            detail="original detail",
            tags=["sqlite", "migration"],
            importance=0.4,
        )

    asyncio.run(_do())
    return daemon_checkout


def _tool(name: str) -> object:
    from tests.conftest import extract_tool_fn, make_test_server

    return extract_tool_fn(make_test_server("learning"), name)


def _stored(trw_project: DaemonCheckout, entry_id: str = "L-fix") -> dict[str, object]:
    async def _do() -> dict[str, object]:
        result = await trw_project.client.get(entry_id, trw_project.namespace)
        assert result.get("status") == "ok", result
        return dict(result["entry"])

    return asyncio.run(_do())


def test_only_named_fields_change_and_the_id_is_kept(trw_project: DaemonCheckout) -> None:
    result = _tool("trw_learn")(learning_id="L-fix", impact=0.8)

    assert result["learning_id"] == "L-fix"
    assert result["status"] == "updated"
    entry = _stored(trw_project)
    assert entry["importance"] == 0.8
    assert (entry["content"], entry["detail"], entry["tags"]) == (
        "pin the sqlite driver before the migration",
        "original detail",
        ["sqlite", "migration"],
    )


def test_tags_replace_unless_tags_add_appends(trw_project: DaemonCheckout) -> None:
    learn = _tool("trw_learn")

    learn(learning_id="L-fix", metadata={"tags_add": ["migration", "driver"]})
    assert _stored(trw_project)["tags"] == ["sqlite", "migration", "driver"]

    learn(learning_id="L-fix", tags=["only"])
    assert _stored(trw_project)["tags"] == ["only"]


def test_unknown_id_fails_loudly(trw_project: DaemonCheckout) -> None:
    result = _tool("trw_learn")(learning_id="L-nope", impact=0.5)

    assert result["status"] == "rejected"
    assert result["reason"] == "learning_not_found"


def test_retired_learning_leaves_default_recall_and_stays_reachable_by_status(trw_project: DaemonCheckout) -> None:
    recall = _tool("trw_recall")

    def ids(**kwargs: object) -> set[str]:
        return {str(row["id"]) for row in recall(query="sqlite driver migration", **kwargs)["learnings"]}

    assert "L-fix" in ids()

    assert _tool("trw_learn")(learning_id="L-fix", status="obsolete")["status"] == "updated"

    assert "L-fix" not in ids()
    assert "L-fix" in ids(status="obsolete")


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_detail_leaves_the_detail_unchanged(trw_project: DaemonCheckout, blank: str) -> None:
    result = _tool("trw_learn")(learning_id="L-fix", impact=0.4, detail=blank)

    assert result["status"] in {"updated", "no_changes"}, result
    entry = _stored(trw_project)
    assert entry["detail"] == "original detail"
    assert entry["content"] == "pin the sqlite driver before the migration"


_METADATA_TEXT = {
    "nudge_line": "keep me",
    "phase_origin": "IMPLEMENT",
    "task_type": "coding",
    "protection_tier": "protected",
}


@pytest.mark.parametrize("blank", ["", "  "])
@pytest.mark.parametrize("key", [*_METADATA_TEXT, "domain_free_text_keys"])
def test_a_blank_metadata_string_leaves_the_stored_value_unchanged(
    trw_project: DaemonCheckout, key: str, blank: str
) -> None:
    learn = _tool("trw_learn")
    seeded = learn(learning_id="L-fix", metadata=dict(_METADATA_TEXT))
    assert seeded["status"] == "updated", seeded
    before = _stored(trw_project)

    metadata = dict.fromkeys(_METADATA_TEXT, blank) if key == "domain_free_text_keys" else {key: blank}
    metadata.update({"team_origin": blank, "expires": blank, "supersedes": blank})
    result = learn(learning_id="L-fix", impact=0.4, metadata=metadata)

    assert result["status"] in {"updated", "no_changes"}, result
    after = _stored(trw_project)
    for k in ("content", "detail", "tags", "status", "type", "confidence", "evidence_level", *_METADATA_TEXT):
        assert after.get(k) == before.get(k), k


def test_tags_add_reaches_the_yaml_backup(trw_project: DaemonCheckout) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.state.analytics import find_entry_by_id

    learn = _tool("trw_learn")
    created = learn(
        summary="vacuum before copying the db", detail="copying a WAL db mid-write corrupts it", tags=["sqlite"]
    )
    learning_id = str(created["learning_id"])

    assert learn(learning_id=learning_id, metadata={"tags_add": ["backup"]})["status"] == "updated"

    config = get_config()
    found = find_entry_by_id(trw_project.trw_dir / config.learnings_dir / config.entries_dir, learning_id)
    assert found is not None
    assert found[1]["tags"][-1] == "backup"
    assert "sqlite" in found[1]["tags"]


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_summary_is_rejected_and_never_stored(trw_project: DaemonCheckout, blank: str) -> None:
    result = _tool("trw_learn")(learning_id="L-fix", summary=blank)

    assert result["status"] == "rejected"
    assert "summary" in result["message"]
    assert _stored(trw_project)["content"] == "pin the sqlite driver before the migration"


def test_a_status_update_with_an_unset_summary_keeps_the_summary(trw_project: DaemonCheckout) -> None:
    result = _tool("trw_learn")(learning_id="L-fix", status="obsolete", tags=None)

    assert result["status"] == "updated"
    assert _stored(trw_project)["content"] == "pin the sqlite driver before the migration"


@pytest.mark.parametrize("blank", ["", "  "])
def test_an_empty_tags_string_clears_the_tags(trw_project: DaemonCheckout, blank: str) -> None:
    assert _stored(trw_project)["tags"]

    result = _tool("trw_learn")(learning_id="L-fix", tags=blank)

    assert result["status"] == "updated"
    assert _stored(trw_project)["tags"] == []


@pytest.mark.parametrize(
    "field",
    ["status", "type", "confidence", "evidence_level", "scope", "metadata", "detail"],
)
def test_an_empty_string_for_an_unset_optional_field_changes_nothing(trw_project: DaemonCheckout, field: str) -> None:
    """Some MCP clients send "" for every unset optional string: it means UNSET, never a stored ""."""
    before = _stored(trw_project)

    result = _tool("trw_learn")(learning_id="L-fix", impact=0.4, **{field: ""})

    assert result["status"] in {"updated", "no_changes"}, result
    after = _stored(trw_project)
    for key in ("content", "detail", "tags", "importance", "status", "type", "confidence", "evidence_level"):
        assert after.get(key) == before.get(key), key


def test_blank_update_only_metadata_strings_leave_seeded_values_unchanged(trw_project: DaemonCheckout) -> None:
    """codex r1 known issue: team_origin and expires are pinned with seeded values, not only sent blank."""
    learn = _tool("trw_learn")
    seeded = learn(learning_id="L-fix", metadata={"team_origin": "platform-team", "expires": "2027-01-01"})
    assert seeded["status"] == "updated", seeded
    before = _stored(trw_project)
    assert before.get("team_origin") == "platform-team" and before.get("expires") == "2027-01-01"

    result = learn(learning_id="L-fix", impact=0.3, metadata={"team_origin": "  ", "expires": ""})

    assert result["status"] in {"updated", "no_changes"}, result
    after = _stored(trw_project)
    assert (after.get("team_origin"), after.get("expires")) == ("platform-team", "2027-01-01")


@pytest.mark.parametrize("blank", ["", "   "])
@pytest.mark.parametrize("field", ["domain", "phase_affinity"])
def test_a_blank_list_field_string_clears_the_list(trw_project: DaemonCheckout, field: str, blank: str) -> None:
    learn = _tool("trw_learn")
    assert learn(learning_id="L-fix", metadata={field: ["x"]})["status"] == "updated"

    result = learn(learning_id="L-fix", metadata={field: blank})

    assert result["status"] == "updated", result
    assert _stored(trw_project).get(field) in ([], None)
