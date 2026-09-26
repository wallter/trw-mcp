"""PRD-CORE-274-FR10: populated formation and project isolation.

Every scope here carries REAL endpoints and REAL admitted messages on both
sides. An empty receiver proves nothing: "no foreign records came back" is
satisfied trivially when there were no foreign records to come back, and that is
the shape an isolation test most easily degrades into.

Two scope separations are exercised, because they fail differently:

* two formations under ONE project — same member names, same request keys, so
  only the group identity distinguishes them;
* the same textual formation/member/request-key IDs under SEPARATE projects,
  which is the collision a shared team vocabulary actually produces.

Distinct formations get distinct database files, so a passing cross-scope test
could simply mean the two calls never met. The load-bearing control at the end
therefore puts two internally valid, populated groups in ONE database — built by
the product and accepted by the production integrity validator — and shows the
same assertion fails when only the group predicate is removed.

Synthetic in-process FastMCP clients. Not native harness, wake, crash or
power-loss proof.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import open_slot
from trw_mcp import formation
from trw_mcp.comms import _inbox_page, derive_group_id
from trw_mcp.comms._schema import verify
from trw_mcp.comms._store import database_path
from trw_mcp.models import config as config_module
from trw_mcp.models.config import TRWConfig
from trw_mcp.state import _paths
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths import pin_active_run
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools

MEMBERS = ("alpha", "beta")
#: Small enough that one scope can be driven to its lifetime ceiling inside a
#: test, which is how "exhausting A leaves B usable" gets a real exhaustion
#: rather than a simulated one.
ADMISSION_LIMIT = 2


@dataclass
class Scope:
    """One formation inside one project — the unit isolation is claimed over."""

    project: Path
    formation_id: str
    manifest: Path
    runs: dict[str, Path] = field(default_factory=dict)

    @property
    def group_id(self) -> str:
        return derive_group_id(self.project, self.manifest)

    @property
    def database(self) -> Path:
        return database_path(self.manifest)

    def pin(self, member: str) -> str:
        return f"{self.formation_id}-{self.project.name}-{member}"


@dataclass
class Harness:
    server: FastMCP
    monkeypatch: pytest.MonkeyPatch
    config: TRWConfig

    def activate(self, scope: Scope, member: str) -> None:
        """Become *member* of *scope*, project root included.

        The project root is switched as well as the session id, because FR10
        derives the group from BOTH and a test that only switched identity would
        never leave the first project.
        """
        from tests import _path_isolation

        _path_isolation.set_current_root(scope.project)
        self.monkeypatch.setenv("TRW_PROJECT_ROOT", str(scope.project))
        self.monkeypatch.setenv("TRW_SESSION_ID", scope.pin(member))

    def call(self, scope: Scope, member: str, tool: str, **arguments: Any) -> dict[str, Any]:
        self.activate(scope, member)
        result = asyncio.run(self.server.call_tool(tool, arguments))
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    def rows(self, scope: Scope, sql: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        conn = sqlite3.connect(scope.database)
        try:
            return conn.execute(sql, parameters).fetchall()
        finally:
            conn.close()

    def ledger(self, scope: Scope) -> dict[str, Any]:
        """The numeric state FR10 says must not move when another scope is used."""
        return {
            **self.record_ledger(scope),
            "refusals": sorted(self.rows(scope, "SELECT reason,count FROM refusal_counts")),
        }

    def record_ledger(self, scope: Scope) -> dict[str, Any]:
        """Ledger WITHOUT refusal counters.

        A refused call legitimately increments the caller scope's refusal
        bookkeeping, so asserting the full ledger is unchanged across a refusal
        would fail for a correct implementation. These three are the numbers a
        refusal must never move.
        """
        return {
            "charge": self.rows(scope, "SELECT charge FROM groups")[0][0],
            "admissions": self.rows(scope, "SELECT count(*) FROM admissions")[0][0],
            "milestones": self.rows(scope, "SELECT count(*) FROM milestones")[0][0],
        }

    def state(self, scope: Scope, message_id: str) -> str | None:
        found = self.rows(scope, "SELECT state FROM admissions WHERE message_id=?", (message_id,))
        return str(found[0][0]) if found else None

    def facts(self, scope: Scope, message_id: str) -> set[str]:
        return {
            str(row[0]) for row in self.rows(scope, "SELECT fact FROM milestones WHERE message_id=?", (message_id,))
        }


def _make_project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".trw").mkdir(parents=True)
    return root


def _make_scope(root: Path, formation_id: str, monkeypatch: pytest.MonkeyPatch) -> Scope:
    """Create and join a real formation through the public facade."""
    from tests import _path_isolation

    _path_isolation.set_current_root(root)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    trw_dir = root / ".trw"
    runs = {member: trw_dir / "runs" / f"{formation_id}-{member}" for member in MEMBERS}
    for run in runs.values():
        (run / "meta").mkdir(parents=True)
        (run / "meta" / "run.yaml").write_text(f"run_id: {run.name}\nstatus: active\n", encoding="utf-8")
    scope = Scope(root, formation_id, runs[MEMBERS[0]] / "formation.yaml", runs)
    formation.create(
        runs[MEMBERS[0]],
        {
            "formation_id": formation_id,
            "members": [open_slot(m) for m in MEMBERS],
        },
        trw_dir=trw_dir,
        prds_dir=root / "prds",
    )
    for member in MEMBERS:
        monkeypatch.setenv("TRW_SESSION_ID", scope.pin(member))
        context = build_call_context(None)
        formation.join(formation_id, member, runs[member], pin_key=context.session_id, trw_dir=trw_dir)
        pin_active_run(runs[member], context=context)
    return scope


def _populate(harness: Harness, scope: Scope, *, key: str = "shared-key", body: str = "payload") -> str:
    """Enroll the recipient and admit one real message. Returns its message_id."""
    assert harness.call(scope, MEMBERS[1], "trw_inbox", action="enroll")["status"] == "ok"
    receipt = harness.call(scope, MEMBERS[0], "trw_send", recipient_member_id=MEMBERS[1], request_key=key, body=body)
    assert receipt["status"] == "ok", receipt
    message_id = receipt["receipt"]["message_id"]
    assert isinstance(message_id, str)
    return message_id


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Harness:
    config = TRWConfig(
        comms_enabled=True,
        cleanup_on_boot=False,
        comms_group_row_limit=ADMISSION_LIMIT,
        # One item per page so a real next_cursor exists to try against
        # another scope; the default page would swallow both messages.
        comms_fetch_max_items=1,
    )
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    monkeypatch.setattr(_paths, "get_config", lambda: config)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    server = FastMCP("isolation")
    register_swarm_comms_tools(server)
    return Harness(server, monkeypatch, config)


def _ids(payload: dict[str, Any]) -> set[str]:
    return {str(item["message_id"]) for item in payload["items"]}


def _assert_scopes_isolated(harness: Harness, a: Scope, b: Scope, mine: str, foreign: str) -> None:
    """The whole FR10 claim, asserted identically for both scope separations.

    Written once and driven from both tests on purpose: two near-copies drift,
    and an isolation assertion that is subtly weaker in one of them is exactly
    the defect this file exists to exclude.
    """
    assert a.group_id != b.group_id
    assert a.database != b.database

    # POSITIVE CONTROL, both directions. Without this the refusals below could
    # all be a feature that simply does not work.
    assert _ids(harness.call(a, MEMBERS[1], "trw_inbox", action="fetch")) == {mine}
    assert _ids(harness.call(b, MEMBERS[1], "trw_inbox", action="fetch")) == {foreign}

    # Body-free status must not surface the other scope either.
    status = harness.call(a, MEMBERS[0], "trw_inbox", action="status")
    assert _ids(status) == {mine}
    assert all("body" not in item for item in status["items"])

    # ACK of a foreign id while bound to A must refuse and move NOTHING in
    # either scope. Checking the returned list alone would pass against an
    # implementation that acknowledged it and said nothing; checking only B
    # would miss the worse case, where the refusal silently ACKs the CALLER's
    # own pending message instead.
    before_a, before_b = harness.record_ledger(a), harness.record_ledger(b)
    refused = harness.call(a, MEMBERS[1], "trw_inbox", action="ack", message_ids=[foreign])
    assert refused["status"] == "refused", refused
    assert foreign not in set(refused.get("acknowledged_ids") or [])
    assert harness.state(b, foreign) == "pending"
    assert harness.state(a, mine) == "pending"
    assert harness.record_ledger(a) == before_a
    assert harness.record_ledger(b) == before_b

    # POSITIVE ACK CONTROL on BOTH scopes. Without it every assertion above is
    # satisfied by an ACK path that refuses everything.
    for scope, owned in ((a, mine), (b, foreign)):
        charge_before = harness.record_ledger(scope)["charge"]
        accepted = harness.call(scope, MEMBERS[1], "trw_inbox", action="ack", message_ids=[owned])
        assert accepted["status"] == "ok", accepted
        assert owned in set(accepted["acknowledged_ids"])
        assert harness.state(scope, owned) == "acked"
        assert "acked" in harness.facts(scope, owned)
        # ACK is a receipt, not a refund: the lifetime charge stands.
        assert harness.record_ledger(scope)["charge"] == charge_before


def test_two_formations_in_one_project_are_isolated(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same project, same member names, same request key — only the group differs."""
    project = _make_project(tmp_path, "one-project")
    a = _make_scope(project, "f-one", monkeypatch)
    b = _make_scope(project, "f-two", monkeypatch)
    mine = _populate(harness, a, body="a-body")
    foreign = _populate(harness, b, body="b-body")

    _assert_scopes_isolated(harness, a, b, mine, foreign)


def test_same_ids_in_separate_projects_are_isolated(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical formation, member and request-key text in two projects.

    This is the collision a shared team vocabulary actually produces: nothing
    about the names distinguishes the two scopes, only the project root that
    FR10 folds into the derived group id.
    """
    a = _make_scope(_make_project(tmp_path, "project-a"), "shared-name", monkeypatch)
    b = _make_scope(_make_project(tmp_path, "project-b"), "shared-name", monkeypatch)
    assert a.formation_id == b.formation_id
    mine = _populate(harness, a, body="a-body")
    foreign = _populate(harness, b, body="b-body")
    assert mine != foreign  # same request key, different messages

    _assert_scopes_isolated(harness, a, b, mine, foreign)


def test_a_cursor_cannot_be_replayed_into_another_scope(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A continuation token is scoped, not a bare row offset."""
    project = _make_project(tmp_path, "cursor-project")
    a = _make_scope(project, "f-one", monkeypatch)
    b = _make_scope(project, "f-two", monkeypatch)
    _populate(harness, a, key="k1")
    harness.call(a, MEMBERS[0], "trw_send", recipient_member_id=MEMBERS[1], request_key="k2", body="second")
    _populate(harness, b, key="k1")

    page = harness.call(a, MEMBERS[1], "trw_inbox", action="fetch")
    cursor = page["next_cursor"]
    assert isinstance(cursor, str) and cursor  # positive control: a real cursor exists

    carried = harness.call(b, MEMBERS[1], "trw_inbox", action="fetch", cursor=cursor)

    assert carried["status"] == "refused", carried
    # B's own paging still works, so the refusal is about the foreign cursor.
    assert harness.call(b, MEMBERS[1], "trw_inbox", action="fetch")["status"] == "ok"


def test_exhausting_one_scope_leaves_the_other_usable(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lifetime ceiling is per group, and reaching it is not contagious."""
    project = _make_project(tmp_path, "exhaust-project")
    a = _make_scope(project, "f-one", monkeypatch)
    b = _make_scope(project, "f-two", monkeypatch)
    _populate(harness, a, key="k1")
    _populate(harness, b, key="k1")
    before = harness.ledger(b)

    assert (
        harness.call(a, MEMBERS[0], "trw_send", recipient_member_id=MEMBERS[1], request_key="k2", body="x")["status"]
        == "ok"
    )
    exhausted = harness.call(a, MEMBERS[0], "trw_send", recipient_member_id=MEMBERS[1], request_key="k3", body="x")

    assert exhausted["status"] == "refused"
    assert harness.ledger(a)["charge"] == ADMISSION_LIMIT
    # B's numeric ledgers are untouched, including its refusal counters.
    assert harness.ledger(b) == before
    # ...and B is still usable, which a shared-quota bug would break.
    assert (
        harness.call(b, MEMBERS[0], "trw_send", recipient_member_id=MEMBERS[1], request_key="k2", body="y")["status"]
        == "ok"
    )


def _merge_into(source: Scope, target: Scope) -> None:
    """Co-locate *source*'s groups inside *target*'s database.

    The rows are PRODUCT-GENERATED, copied verbatim from a database the product
    built and accepted. Hand-writing a two-group fixture would test my reading
    of the schema invariants rather than the real thing, and any mistake would
    show up as a fixture that the integrity validator rejects.
    """
    conn = sqlite3.connect(target.database)
    try:
        conn.execute("ATTACH DATABASE ? AS other", (str(source.database),))
        for table in ("groups", "endpoints", "admissions", "milestones", "refusal_counts"):
            conn.execute(f"INSERT INTO {table} SELECT * FROM other.{table}")
        conn.commit()
        conn.execute("DETACH DATABASE other")
    finally:
        conn.close()


def _assert_status_shows_no_foreign(harness: Harness, scope: Scope, mine: str) -> None:
    """THE assertion under control. Identical text in both runs, by construction."""
    assert _ids(harness.call(scope, MEMBERS[0], "trw_inbox", action="status")) == {mine}


def _status_without_group_predicate(original: Any) -> Any:
    """Drop ONLY ``group_id=?`` from the status read; keep everything else.

    Caller predicate, ordering and limit are preserved, so a failure can only be
    attributed to the group filter. The fetch branch is untouched and delegates
    to the real implementation.
    """

    def substitute(
        conn: sqlite3.Connection,
        binding: Any,
        action: str,
        incarnation: str | None,
        after: int,
        limit: int,
    ) -> list[sqlite3.Row]:
        if action != "status":
            return list(original(conn, binding, action, incarnation, after, limit))
        return list(
            conn.execute(
                "SELECT rowid AS append_id,* FROM admissions WHERE "
                "(sender_member_id=? OR recipient_member_id=?) AND rowid>? ORDER BY rowid LIMIT ?",
                (binding.member_id, binding.member_id, after, limit + 1),
            )
        )

    return substitute


def test_status_group_predicate_is_load_bearing(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEGATIVE CONTROL for every isolation assertion above.

    Separate formations get separate database files, so the passing tests above
    are also consistent with an implementation that has NO group filter at all
    and simply never met the other scope's rows. This control removes that
    alternative explanation: two internally valid, populated groups share ONE
    database, the production integrity validator accepts the fixture first, and
    then the single group predicate is removed. The same assertion must fail.
    """
    project = _make_project(tmp_path, "colocated")
    a = _make_scope(project, "f-one", monkeypatch)
    b = _make_scope(project, "f-two", monkeypatch)
    mine = _populate(harness, a, body="a-body")
    foreign = _populate(harness, b, body="b-body")
    _merge_into(b, a)
    # The page must be able to HOLD both rows. At a one-item page the foreign
    # row is excluded by paging rather than by the group filter, and the control
    # silently passes while proving nothing — observed before this line existed.
    harness.config.comms_fetch_max_items = 16

    # The fixture must be valid BEFORE it is used as evidence, by production
    # rules rather than by my assertion about them.
    conn = sqlite3.connect(a.database)
    conn.row_factory = sqlite3.Row
    try:
        verify(conn)
        colocated = {row[0] for row in conn.execute("SELECT DISTINCT group_id FROM admissions")}
    finally:
        conn.close()
    assert colocated == {a.group_id, b.group_id}
    assert {row[0] for row in harness.rows(a, "SELECT message_id FROM admissions")} == {mine, foreign}

    # Guarded: the foreign row is right there in the same table and stays out.
    _assert_status_shows_no_foreign(harness, a, mine)

    monkeypatch.setattr(_inbox_page, "_read_rows", _status_without_group_predicate(_inbox_page._read_rows))

    with pytest.raises(AssertionError):
        _assert_status_shows_no_foreign(harness, a, mine)

    # ...and the failure is specifically the foreign record surfacing, not some
    # unrelated breakage that also happens to raise AssertionError.
    leaked = _ids(harness.call(a, MEMBERS[0], "trw_inbox", action="status"))
    assert leaked == {mine, foreign}
