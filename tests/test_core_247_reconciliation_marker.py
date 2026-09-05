"""PRD-CORE-247-FR04/FR05 + NFR04/NFR05: offline-write markers and reconciliation.

Every assertion here reads the STORE, not a return value. The defect this PRD
fixes was invisible from the caller's side: ``write_local_learning`` passed
``source_type="local_cli"`` and got a success result back while
``_validate_source_type`` coerced the marker to ``"agent"`` before storage. A
test that asserted the argument, or the result, would have passed throughout.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest

from trw_mcp.state._constants import LOCAL_CLI_SOURCE_IDENTITY, RECONCILE_PENDING_TAG


def _read_row(trw_dir: Path, learning_id: str) -> object:
    from trw_mcp.state.memory_adapter import get_backend

    return get_backend(trw_dir).get(learning_id, namespace="default")


def _write_offline(trw_dir: Path, summary: str, detail: str, tags: list[str] | None = None) -> str:
    from trw_mcp.services.orchestration_service import write_local_learning

    result = write_local_learning(summary, detail, trw_dir=trw_dir, tags=tags)
    learning_id = str(result.get("learning_id") or result.get("id") or "")
    assert learning_id, f"offline write returned no id: {result}"
    return learning_id


def _write_online(trw_dir: Path, summary: str, detail: str) -> str:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._learn_impl import execute_learn

    result = execute_learn(summary=summary, detail=detail, trw_dir=trw_dir, config=get_config())
    learning_id = str(result.get("learning_id") or result.get("id") or "")
    assert learning_id, f"MCP-path write returned no id: {result}"
    return learning_id


# ---------------------------------------------------------------------------
# FR04 — the two markers, with different lifetimes
# ---------------------------------------------------------------------------


def test_local_write_is_distinguishable_from_mcp_write(tmp_path: Path) -> None:
    """FR04 acceptance: the two write paths produce rows that differ.

    Fails before the change: ``source_identity`` read back as ``""`` and the
    reserved tag was never applied, so the offline row was byte-indistinguishable
    from the MCP-path row.
    """
    trw_dir = tmp_path / ".trw"
    offline_id = _write_offline(trw_dir, "Offline write marker probe", "Written through the local CLI path.")
    online_id = _write_online(trw_dir, "MCP write marker probe", "Written through the trw_learn tool path.")

    offline = _read_row(trw_dir, offline_id)
    online = _read_row(trw_dir, online_id)
    assert offline is not None and online is not None

    assert offline.source_identity == LOCAL_CLI_SOURCE_IDENTITY
    assert RECONCILE_PENDING_TAG in offline.tags

    assert online.source_identity != LOCAL_CLI_SOURCE_IDENTITY
    assert RECONCILE_PENDING_TAG not in online.tags


def test_operator_supplied_tags_survive_beside_the_reserved_tag(tmp_path: Path) -> None:
    """FR04: the marker is added, never substituted for the caller's tags."""
    trw_dir = tmp_path / ".trw"
    learning_id = _write_offline(trw_dir, "Tagged offline write", "Detail body.", tags=["shell", "hooks"])
    row = _read_row(trw_dir, learning_id)
    assert row is not None
    assert {"shell", "hooks", RECONCILE_PENDING_TAG} <= set(row.tags)


def test_source_type_local_cli_argument_is_gone() -> None:
    """FR04: the silently-erased marker is deleted, not preserved beside the working one.

    An AST read of the ``execute_learn`` call inside ``write_local_learning``,
    not a substring grep — the module docstring legitimately names the removed
    argument to explain why it went, and a grep would fire on that prose.
    """
    source = (
        Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "services" / "orchestration_service.py"
    ).read_text(encoding="utf-8")
    fn = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "write_local_learning"
    )
    calls = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "execute_learn"
    ]
    assert len(calls) == 1, "write_local_learning must reach the shared learn implementation exactly once"
    keywords = {kw.arg for kw in calls[0].keywords}
    assert "source_type" not in keywords, (
        "source_type is coerced to 'agent' by _validate_source_type before storage; passing it "
        "here is a silently erased marker sitting beside a working one"
    )
    assert "source_identity" in keywords, "the durable provenance marker must be passed"


# ---------------------------------------------------------------------------
# FR05 — report, then clear
# ---------------------------------------------------------------------------


def test_session_start_reports_then_clears_the_pending_queue(tmp_path: Path) -> None:
    """FR05 acceptance: first call reports N and clears; second reports 0."""
    from trw_mcp.tools._ceremony_reconcile_step import step_reconcile_local_writes

    trw_dir = tmp_path / ".trw"
    ids = [
        _write_offline(trw_dir, summary, f"Body for {summary}.")
        for summary in (
            "Sqlite checkpoint serialisation on ancient drivers",
            "Argparse subparser dispatch omits the usage listing",
            "Ruamel anchor prescan rejects duplicated aliases",
        )
    ]

    first = step_reconcile_local_writes(trw_dir)
    assert first["pending"] == 3
    assert sorted(first["learning_ids"]) == sorted(ids)
    assert first["cleared"] == 3

    for learning_id in ids:
        row = _read_row(trw_dir, learning_id)
        assert row is not None
        assert RECONCILE_PENDING_TAG not in row.tags
        # Durable provenance is NOT cleared — only the transient queue entry is.
        assert row.source_identity == LOCAL_CLI_SOURCE_IDENTITY

    second = step_reconcile_local_writes(trw_dir)
    assert second["pending"] == 0
    assert second["learning_ids"] == []


def test_reconcile_failure_records_a_degradation_and_leaves_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR05 acceptance: an induced failure yields a recorded degradation, not an error.

    Driven through the REAL step-table driver, because that is where the
    ``success: true`` guarantee lives — asserting on the step alone would not
    prove ``trw_session_start`` survives it.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.models.typed_dicts import SessionStartResultDict
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_step_table import SessionStartContext as Ctx
    from trw_mcp.tools._ceremony_step_table import Step, run_steps

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("induced reconciliation failure")

    monkeypatch.setattr("trw_mcp.tools._ceremony_step_table.step_reconcile_local_writes", _boom)
    monkeypatch.setattr(_ceremony, "resolve_trw_dir", lambda: tmp_path / ".trw")

    results: SessionStartResultDict = {}  # type: ignore[typeddict-item]
    errors: list[str] = []
    sctx = Ctx(query="", config=get_config(), ctx=None, is_focused=False, results=results, errors=errors)
    run_steps((Step("reconcile_local_writes", "_ss_reconcile_local_writes"),), sctx, _ceremony)

    assert errors == [], "a diagnostic step must never populate errors (which is what flips success)"
    degradations = results.get("degradations") or []
    assert any("reconcile_local_writes" in str(entry) for entry in degradations), degradations


# ---------------------------------------------------------------------------
# NFR04 — concurrency
# ---------------------------------------------------------------------------


def test_concurrent_offline_and_mcp_writes(tmp_path: Path) -> None:
    """NFR04 acceptance: both rows persist, and the clear only clears what it reported.

    The interleaving under test is the dangerous one: a row written BETWEEN the
    query and the clear must keep its tag rather than being cleared unreported.
    """
    from trw_mcp.tools import _ceremony_reconcile_step as step_mod

    trw_dir = tmp_path / ".trw"
    first_id = _write_offline(trw_dir, "Offline write before the query", "Detail A.")
    online_id = _write_online(trw_dir, "MCP write alongside", "Detail B.")

    late_ids: list[str] = []
    real_pending = step_mod._pending_entries

    def _pending_then_race(dir_: Path) -> list[tuple[str, list[str]]]:
        rows = real_pending(dir_)
        late_ids.append(_write_offline(dir_, "Offline write during the clear", "Detail C."))
        return rows

    monkeypatched = step_mod._pending_entries
    step_mod._pending_entries = _pending_then_race  # type: ignore[assignment]
    try:
        report = step_mod.step_reconcile_local_writes(trw_dir)
    finally:
        step_mod._pending_entries = monkeypatched  # type: ignore[assignment]

    assert report["learning_ids"] == [first_id]
    assert _read_row(trw_dir, online_id) is not None, "the MCP-path row must be untouched and present"

    late_row = _read_row(trw_dir, late_ids[0])
    assert late_row is not None
    assert RECONCILE_PENDING_TAG in late_row.tags, "a row written after the query must survive to the next session"

    follow_up = step_mod.step_reconcile_local_writes(trw_dir)
    assert follow_up["learning_ids"] == late_ids


def test_a_concurrent_tag_write_survives_the_clear(tmp_path: Path) -> None:
    """NFR04 review follow-up: the clear must not be a lost update.

    ``update_learning`` REPLACES the whole tag list, so the set it is handed has
    to be computed from the row as it is now. Computing it from the query-time
    snapshot instead means a tag another process added between the query and the
    clear is silently erased — by the step whose only job is to remove one
    specific tag.

    Fails before the change: ``urgent`` is written after ``_pending_entries``
    returns, and the snapshot-derived replacement drops it.
    """
    from trw_mcp.state.memory_adapter import update_learning
    from trw_mcp.tools import _ceremony_reconcile_step as step_mod

    trw_dir = tmp_path / ".trw"
    learning_id = _write_offline(trw_dir, "Row that gains a tag mid-reconcile", "Body.", tags=["shell"])

    real_pending = step_mod._pending_entries

    def _pending_then_concurrent_tag(dir_: Path) -> list[tuple[str, list[str]]]:
        rows = real_pending(dir_)
        # A different process tags the row AFTER the query, BEFORE the clear.
        live = _read_row(dir_, learning_id)
        assert live is not None
        update_learning(dir_, learning_id, tags=[*live.tags, "urgent"])
        return rows

    step_mod._pending_entries = _pending_then_concurrent_tag  # type: ignore[assignment]
    try:
        report = step_mod.step_reconcile_local_writes(trw_dir)
    finally:
        step_mod._pending_entries = real_pending  # type: ignore[assignment]

    assert report["learning_ids"] == [learning_id]
    row = _read_row(trw_dir, learning_id)
    assert row is not None
    assert RECONCILE_PENDING_TAG not in row.tags, "the reserved tag must still be cleared"
    assert "urgent" in row.tags, "a tag written between the query and the clear must survive"
    assert "shell" in row.tags, "the caller's original tags must survive"


def test_a_row_already_cleared_by_another_process_counts_as_cleared(tmp_path: Path) -> None:
    """NFR04: the step converges on the STATE, not on having done the write itself.

    Two sessions starting at once both query, both report; whichever clears
    second finds the tag already gone. That is success — the tag is absent — and
    counting it as a failure would make ``cleared < pending`` the normal case and
    destroy the signal that a rising gap is a broken clear step.
    """
    from trw_mcp.state.memory_adapter import update_learning
    from trw_mcp.tools import _ceremony_reconcile_step as step_mod

    trw_dir = tmp_path / ".trw"
    learning_id = _write_offline(trw_dir, "Row cleared by a racing session", "Body.")
    real_pending = step_mod._pending_entries

    def _pending_then_other_session_clears(dir_: Path) -> list[tuple[str, list[str]]]:
        rows = real_pending(dir_)
        live = _read_row(dir_, learning_id)
        assert live is not None
        update_learning(dir_, learning_id, tags=[t for t in live.tags if t != RECONCILE_PENDING_TAG])
        return rows

    step_mod._pending_entries = _pending_then_other_session_clears  # type: ignore[assignment]
    try:
        report = step_mod.step_reconcile_local_writes(trw_dir)
    finally:
        step_mod._pending_entries = real_pending  # type: ignore[assignment]

    assert report["pending"] == 1
    assert report["cleared"] == 1


# ---------------------------------------------------------------------------
# NFR05 — forward-only and idempotent
# ---------------------------------------------------------------------------


def test_forward_only_and_idempotent_against_a_copied_store(tmp_path: Path) -> None:
    """NFR05 acceptance: pre-existing rows are neither reported nor modified."""
    from trw_mcp.tools._ceremony_reconcile_step import step_reconcile_local_writes

    legacy_dir = tmp_path / "legacy" / ".trw"
    legacy_ids = [
        _write_online(legacy_dir, summary, f"Body for {summary}.")
        for summary in (
            "Tarball extraction ignores symlink members",
            "Websocket ping interval starves the reader loop",
            "Codegen emits duplicate protobuf oneof branches",
        )
    ]
    before = {lid: (_read_row(legacy_dir, lid).tags, _read_row(legacy_dir, lid).source_identity) for lid in legacy_ids}

    copied = tmp_path / "copied" / ".trw"
    copied.parent.mkdir(parents=True, exist_ok=True)
    from trw_mcp.state.memory_adapter import reset_backend

    # Close the source connection so the copy sees a checkpointed database.
    reset_backend()
    shutil.copytree(legacy_dir, copied)

    first = step_reconcile_local_writes(copied)
    assert first["pending"] == 0
    second = step_reconcile_local_writes(copied)
    assert second == first, "repeated runs must converge to the same state"

    for lid in legacy_ids:
        row = _read_row(copied, lid)
        assert row is not None
        assert (row.tags, row.source_identity) == before[lid], "no pre-existing row may be modified"
