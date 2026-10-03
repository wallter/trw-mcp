"""CORE274 factual preparation/ACK records, byte packing and transaction rollback; PRD-CORE-322 NFR04 and the FR08 writer census."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.conftest import core
from tests.comms.test_fetch_ack import invoke, transport_scene  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms._envelope import canonical_bytes


@pytest.mark.parametrize(
    "scene",
    [
        {"comms_body_max_bytes": 1024, "comms_response_max_bytes": 10240},
        {"comms_body_max_bytes": 43008, "comms_response_max_bytes": 262144},
    ],
    indirect=True,
)
async def test_escaped_byte_pages_prepare_only_included_and_lost_response_replays(transport_scene: SendScene) -> None:
    s = transport_scene
    expected = []
    async with Client(s.server) as client:
        for index in range(3):
            sent = await invoke(
                client,
                "trw_send",
                recipient_member_id="impl-2",
                request_key=str(index),
                body="\x00" * s.config.comms_body_max_bytes,
            )
            assert sent["status"] == "ok"
            expected.append({**sent["receipt"], "body": "\x00" * s.config.comms_body_max_bytes})
        s.actor("impl-2")
        first = await invoke(client, "trw_inbox")  # simulate consumer losing this result
        assert first["items"] == expected[:1]
        assert first["next_cursor"] is not None
        assert len(canonical_bytes(first)) <= s.config.comms_response_max_bytes
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(1,)]
        facts = s.rows("SELECT * FROM milestones WHERE fact='fetch_prepared'")
        assert await invoke(client, "trw_inbox") == first
        assert s.rows("SELECT * FROM milestones WHERE fact='fetch_prepared'") == facts
        assert s.rows("SELECT COUNT(*) FROM admissions WHERE state='pending'") == [(3,)]
        second = await invoke(client, "trw_inbox", cursor=first["next_cursor"])
        third = await invoke(client, "trw_inbox", cursor=second["next_cursor"])
        assert second["items"] == expected[1:2]
        assert third["items"] == expected[2:3]
        assert "next_cursor" not in third
        assert (
            len(canonical_bytes(second)) <= s.config.comms_response_max_bytes
            and len(canonical_bytes(third)) <= s.config.comms_response_max_bytes
        )
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(3,)]


async def test_fetch_policy_compatibility_checked_even_when_empty(transport_scene: SendScene) -> None:
    s = transport_scene
    stored = s.config.comms_body_max_bytes
    s.config.comms_body_max_bytes = 1
    s.config.comms_response_max_bytes = 6 * stored + 4095
    s.actor("impl-2")
    async with Client(s.server) as client:
        assert (await invoke(client, "trw_inbox"))["reason"] == "response_body_policy_incompatible"
        assert (await invoke(client, "trw_inbox", action="status"))["items"] == []
        s.config.comms_response_max_bytes += 1
        assert core(await invoke(client, "trw_inbox")) == {
            "status": "ok",
            "items": [],
        }


@pytest.mark.parametrize(
    "failure_prefix", ["INSERT OR IGNORE INTO milestones", "INSERT INTO milestones", "UPDATE admissions"]
)
async def test_exception_after_each_fetch_or_ack_write_rolls_back(
    transport_scene: SendScene, failure_prefix: str
) -> None:
    from trw_mcp.comms import _store

    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="a", body="payload")
        s.actor("impl-2")
        before = {
            table: s.rows(f"SELECT * FROM {table}")
            for table in ("groups", "admissions", "milestones", "refusal_counts")
        }
        original = _store.sqlite3.connect

        class Broken(_store.sqlite3.Connection):
            def execute(self, sql: str, parameters: Any = ()) -> Any:
                result = super().execute(sql, parameters)
                if sql.startswith(failure_prefix):
                    raise _store.sqlite3.OperationalError("synthetic after-write failure")
                return result

        with s.monkeypatch.context() as local:
            local.setattr(_store.sqlite3, "connect", lambda *a, **k: original(*a, **{**k, "factory": Broken}))
            args = (
                {}
                if failure_prefix.startswith("INSERT OR IGNORE")
                else {"action": "ack", "message_ids": [sent["receipt"]["message_id"]]}
            )
            assert (await invoke(client, "trw_inbox", **args))["reason"] == "storage_corrupt"
        assert {table: s.rows(f"SELECT * FROM {table}") for table in before} == before


@pytest.mark.parametrize("action", ["status", "ack"])
async def test_actual_small_response_bound_refuses_before_ack_mutation(transport_scene: SendScene, action: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="a", body="payload")
        s.actor("impl-2")
        s.config.comms_response_max_bytes = 20  # process-local invalid-config control of the runtime bound
        args = {"message_ids": [sent["receipt"]["message_id"]]} if action == "ack" else {}
        assert (await invoke(client, "trw_inbox", action=action, **args))["reason"] == "response_too_small"
        assert s.rows("SELECT state FROM admissions") == [("pending",)]


@pytest.mark.parametrize("scene", [{"comms_body_max_bytes": 1024, "comms_response_max_bytes": 10240}], indirect=True)
async def test_process_local_byte_guard_control_demonstrates_oversized_page(transport_scene: SendScene) -> None:
    from trw_mcp.comms import _paging

    s = transport_scene
    async with Client(s.server) as client:
        for index in range(3):
            await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=str(index), body="\x00" * 1024)
        s.actor("impl-2")
        s.monkeypatch.setattr(_paging, "fits", lambda payload, max_bytes: True)
        result = await invoke(client, "trw_inbox")
        assert len(result["items"]) == 3
        assert len(canonical_bytes(result)) > s.config.comms_response_max_bytes


# --- PRD-CORE-322 NFR04: every handoff write is one transaction --------------------


@pytest.mark.parametrize(
    ("stage", "action", "prefix", "needle"),
    [
        ("pending", "accept", "INSERT INTO milestones", "'acked'"),
        ("pending", "accept", "UPDATE admissions SET state=? WHERE message_id", ""),
        ("pending", "accept", "INSERT INTO milestones", "'accepted'"),
        ("accepted", "report", "INSERT INTO milestones", "'reported'"),
        ("accepted", "report", "INSERT INTO handoff_reports", ""),
        ("reported", "complete", "INSERT INTO milestones", "'completed'"),
    ],
)
async def test_exception_after_each_handoff_write_rolls_back(
    transport_scene: SendScene, stage: str, action: str, prefix: str, needle: str
) -> None:
    from trw_mcp.comms import _store

    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="h", body="task")
        message_id = sent["receipt"]["message_id"]
        s.actor("impl-2")
        if stage != "pending":
            await invoke(client, "trw_inbox", action="accept", message_ids=[message_id])
        if stage == "reported":
            await invoke(client, "trw_inbox", action="report", message_ids=[message_id], next_read="wt@sha")
            s.actor("impl-1")
        tables = ("groups", "admissions", "milestones", "handoff_reports", "refusal_counts")
        before = {table: s.rows(f"SELECT * FROM {table}") for table in tables}
        original = _store.sqlite3.connect
        fired: list[str] = []

        class Broken(_store.sqlite3.Connection):
            def execute(self, sql: str, parameters: Any = ()) -> Any:
                result = super().execute(sql, parameters)
                if sql.startswith(prefix) and needle in sql + repr(parameters):
                    fired.append(sql)
                    raise _store.sqlite3.OperationalError("synthetic after-write failure")
                return result

        args: dict[str, Any] = {"next_read": "wt@sha"} if action == "report" else {}
        with s.monkeypatch.context() as local:
            local.setattr(_store.sqlite3, "connect", lambda *a, **k: original(*a, **{**k, "factory": Broken}))
            result = await invoke(client, "trw_inbox", action=action, message_ids=[message_id], **args)
        assert fired, "the injected failure never ran, so this arm proved nothing"
        assert result["reason"] == "storage_corrupt"
        assert {table: s.rows(f"SELECT * FROM {table}") for table in before} == before


# --- PRD-CORE-322 FR08: the writer census -------------------------------------------

_SRC = Path(__file__).resolve().parents[2] / "src" / "trw_mcp"
_WRITER = "comms/_messages.py"
#: The identifier may be bare, double-quoted, backtick-quoted, bracketed, and/or
#: schema-qualified (``main.milestones``, ``"milestones"``, ``` `milestones` ```,
#: ``[milestones]``) — SQLite and other engines all accept these forms, and a
#: rogue writer using any of them must still be caught (PRD-CORE-322 review).
#: One SQL identifier: double-quoted, bracketed, backtick-quoted or bare.
_IDENT = r'(?:"[^"]+"|\[[^\]]+\]|`[^`]+`|\w+)'
#: The ledger table as a COMPLETE identifier, so "milestones-history" or milestones_v2 never match.
_TABLE = r'(?:"(milestones|handoff_reports)"|\[(milestones|handoff_reports)\]|`(milestones|handoff_reports)`|(milestones|handoff_reports)(?![\w-]))'
_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+"
    rf"(?:{_IDENT}\s*\.\s*)?{_TABLE}",
    re.IGNORECASE,
)


def _table(match: re.Match[str]) -> str:
    """The ledger table a :data:`_WRITE` match names, whichever quoting form matched."""
    return next(group for group in match.groups() if group).lower()


_HANDOFF_FACT = re.compile(r"'(accepted|reported|completed)'")


def _literals(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def ledger_writers(root: Path) -> set[str]:
    """Every source file holding a literal SQL write to the fact ledger or the pointer table."""
    return {
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*.py"))
        if any(_WRITE.search(text) for text in _literals(ast.parse(path.read_text(encoding="utf-8"))))
    }


def handoff_writers(source: str) -> set[str]:
    """Functions in *source* that write a handoff fact or a pointer row."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            for text in _literals(node):
                match = _WRITE.search(text)
                if match and (_table(match) == "handoff_reports" or _HANDOFF_FACT.search(text)):
                    found.add(node.name)
    return found


@pytest.mark.unit
def test_fact_ledger_and_pointer_table_have_one_writer_module() -> None:
    assert ledger_writers(_SRC) == {_WRITER}


@pytest.mark.unit
def test_only_the_three_handoff_actions_write_handoff_facts() -> None:
    assert handoff_writers((_SRC / _WRITER).read_text(encoding="utf-8")) == {"accept", "report", "complete"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "rogue_sql",
    [
        'INSERT OR IGNORE INTO "milestones"(message_id,fact,at) VALUES (?,?,?)',
        "INSERT OR IGNORE INTO `milestones`(message_id,fact,at) VALUES (?,?,?)",
        "INSERT OR IGNORE INTO [milestones](message_id,fact,at) VALUES (?,?,?)",
        "INSERT OR IGNORE INTO main.milestones(message_id,fact,at) VALUES (?,?,?)",
        'INSERT OR IGNORE INTO main."milestones"(message_id,fact,at) VALUES (?,?,?)',
        'UPDATE "handoff_reports" SET next_read=? WHERE message_id=?',
        "UPDATE main.handoff_reports SET next_read=? WHERE message_id=?",
        "DELETE FROM `milestones` WHERE message_id=?",
        "REPLACE INTO [handoff_reports](message_id,next_read,reported_at) VALUES (?,?,?)",
        # core322-s2 r2: quoted schema qualifiers and whitespace around the dot.
        'UPDATE "main"."handoff_reports" SET next_read=? WHERE message_id=?',
        "DELETE FROM [main].[milestones] WHERE message_id=?",
        "UPDATE main . milestones SET at=? WHERE message_id=?",
    ],
    ids=[
        "double_quoted",
        "backtick_quoted",
        "bracketed",
        "schema_qualified",
        "schema_and_double_quoted",
        "update_double_quoted",
        "update_schema_qualified",
        "delete_backtick_quoted",
        "replace_bracketed",
        "update_quoted_schema_and_table",
        "delete_bracketed_schema_and_table",
        "update_spaced_dot",
    ],
)
def test_census_catches_quoted_and_qualified_rogue_writers(tmp_path: Path, rogue_sql: str) -> None:
    """PRD-CORE-322 review P2: a rogue writer using a quoted/qualified identifier
    must still be caught. Before the fix, ``_WRITE`` only matched a bare
    identifier, so ``ledger_writers`` silently omitted every rogue file below.
    """
    (tmp_path / "comms").mkdir()
    (tmp_path / "comms" / "_messages.py").write_text((_SRC / _WRITER).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "rogue.py").write_text(f"def rogue(conn):\n    conn.execute({rogue_sql!r})\n", encoding="utf-8")
    assert ledger_writers(tmp_path) == {_WRITER, "rogue.py"}


@pytest.mark.unit
def test_census_fails_on_a_planted_writer(tmp_path: Path) -> None:
    (tmp_path / "comms").mkdir()
    (tmp_path / "comms" / "_messages.py").write_text((_SRC / _WRITER).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "rogue.py").write_text(
        'def close(conn):\n    conn.execute("INSERT OR IGNORE INTO milestones(message_id,fact,at) VALUES (?,?,?)")\n',
        encoding="utf-8",
    )
    (tmp_path / "rogue_pointer.py").write_text('SQL = "update handoff_reports set next_read=?"\n', encoding="utf-8")
    assert ledger_writers(tmp_path) == {_WRITER, "rogue.py", "rogue_pointer.py"}
    planted = "\n\ndef forge(conn):\n    conn.execute(\"INSERT INTO milestones(message_id,fact,at) VALUES (?,'completed',?)\")\n"
    source = (_SRC / _WRITER).read_text(encoding="utf-8") + planted
    assert handoff_writers(source) == {"accept", "report", "complete", "forge"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "other_sql",
    [
        'INSERT INTO "milestones-history"(x) VALUES (?)',
        "INSERT INTO milestones_v2(x) VALUES (?)",
        "UPDATE [handoff_reports_archive] SET x=?",
        "DELETE FROM main.milestones_old WHERE x=?",
    ],
    ids=["hyphenated_quoted", "suffixed_bare", "suffixed_bracketed", "suffixed_qualified"],
)
def test_census_ignores_tables_that_only_share_a_prefix(tmp_path: Path, other_sql: str) -> None:
    """core322-s2 r2: the table must match as a complete identifier, never a prefix."""
    (tmp_path / "comms").mkdir()
    (tmp_path / "comms" / "_messages.py").write_text((_SRC / _WRITER).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "other.py").write_text(f"def other(conn):\n    conn.execute({other_sql!r})\n", encoding="utf-8")
    assert ledger_writers(tmp_path) == {_WRITER}


# --- PRD-CORE-322 FR08: the display census (slice S3) -------------------------------

#: Modules allowed to READ handoff state: the derivation, the writer (retry/role checks), the verifier.
_HANDOFF_READERS = {"comms/_handoff.py", "comms/_messages.py", "comms/_schema.py"}


def _normalized(sql: str) -> str:
    return " ".join(sql.split())


#: The only ledger reads outside the handoff modules, pinned by their exact text: each restricts
#: ``fact`` to ``fetch_prepared`` and so reads no handoff state. Every other ledger read counts as a
#: handoff reader until a reviewer pins it here (core322-s3 r3-r11: each pattern-based exemption --
#: a projected, negated, wrapped or inverted comparison -- was answered by a new bypass).
_NON_HANDOFF_READS = frozenset(
    _normalized(sql)
    for sql in (
        "SELECT 1 FROM milestones WHERE message_id=? AND fact='fetch_prepared'",
        "SELECT a.recipient_member_id,COUNT(*),MIN(a.admitted_at) FROM admissions a "
        "LEFT JOIN milestones m ON m.message_id=a.message_id AND m.fact='fetch_prepared' "
        "WHERE a.group_id=? AND a.state='pending' AND a.expires_at>? AND m.message_id IS NULL "
        "GROUP BY a.recipient_member_id",
    )
)


def handoff_readers(root: Path) -> set[str]:
    """Every source file whose SQL reads the pointer table, or reads the ledger without excluding handoff facts."""
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        for text in _literals(ast.parse(path.read_text(encoding="utf-8"))):
            # A literal reads a ledger table when it mentions one and carries a SELECT keyword (any
            # case, core322-s3 r9) -- a write included, since ``INSERT ... SELECT`` reads its source
            # (core322-s3 r12). Prose and dict keys that merely say "milestones" are not reads.
            if not re.search(r"\bSELECT\b", text, re.IGNORECASE):
                continue
            mentions = {
                name for name in ("milestones", "handoff_reports") if re.search(rf"\b{name}\b", text, re.IGNORECASE)
            }
            if "handoff_reports" in mentions or (
                "milestones" in mentions and _normalized(text) not in _NON_HANDOFF_READS
            ):
                found.add(path.relative_to(root).as_posix())
    return found


def callers_of(root: Path, name: str) -> set[tuple[str, str]]:
    """``(file, function)`` for every function in *root* that calls *name*."""
    found: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for call in ast.walk(node):
                    if isinstance(call, ast.Call):
                        func = call.func
                        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                        if called == name:
                            found.add((path.relative_to(root).as_posix(), node.name))
    return found


@pytest.mark.unit
def test_both_handoff_displays_derive_state_through_the_one_function() -> None:
    """FR06 and FR07 call derive_handoff; nothing else derives or reads handoff state for display."""
    assert callers_of(_SRC, "derive_handoff") == {
        ("comms/_inbox_page.py", "_project"),
        ("comms/_handoff.py", "open_handoffs"),
    }
    assert callers_of(_SRC, "open_handoffs") == {("formation/_stall.py", "stall_scan")}
    assert handoff_readers(_SRC) == _HANDOFF_READERS


@pytest.mark.unit
@pytest.mark.parametrize(
    "rogue_sql",
    [
        "SELECT next_read FROM handoff_reports WHERE message_id=?",
        "SELECT at FROM \"milestones\" WHERE message_id=? AND fact='accepted'",
        "SELECT a.* FROM admissions a JOIN main.milestones m ON m.message_id=a.message_id WHERE m.fact='completed'",
        # core322-s3 r3: a read that names no fact still returns every handoff fact.
        "SELECT fact,at FROM milestones WHERE message_id=?",
        "SELECT at FROM milestones WHERE message_id=? AND fact=?",
        "SELECT * FROM [milestones] WHERE fact IN (?,?)",
        "SELECT at FROM milestones WHERE fact='fetch_prepared' OR 1",
        # core322-s3 r4: negation or inequality inverts a restriction into every other fact.
        "SELECT fact FROM milestones WHERE NOT fact='acked'",
        "SELECT fact FROM milestones WHERE NOT (fact IN ('acked','fetch_prepared'))",
        "SELECT fact FROM milestones WHERE fact != 'acked'",
        "SELECT fact FROM milestones WHERE fact <> 'acked'",
        # core322-s3 r5: a nested predicate constrains another scan, not this one.
        "SELECT fact FROM milestones WHERE EXISTS (SELECT 1 FROM milestones m WHERE m.fact='acked')",
        "SELECT fact FROM milestones WHERE fact='acked' UNION SELECT fact FROM milestones",
        "SELECT m.fact FROM admissions a JOIN milestones m ON m.message_id=a.message_id WHERE a.state='pending'",
        "SELECT 1 FROM admissions a LEFT JOIN milestones m ON m.message_id=a.message_id AND m.fact='accepted'",
        # core322-s3 r6: a comparison in the select list restricts no rows.
        "SELECT fact, fact='acked' AS is_receipt FROM milestones",
        # core322-s3 r7: a comment or a comma join hid the table from a FROM/JOIN pattern.
        "SELECT next_read FROM /* comment */ handoff_reports",
        "SELECT h.next_read FROM admissions a, handoff_reports h",
        "SELECT m.fact FROM admissions a, milestones m WHERE m.message_id=a.message_id",
        # core322-s3 r8: a leading empty statement, and '--' inside a quoted string.
        ";SELECT next_read FROM handoff_reports",
        "SELECT '--' AS marker,next_read FROM handoff_reports",
        # core322-s3 r9: lowercase and mixed-case SQL.
        "select next_read from handoff_reports",
        "Select fact From milestones Where message_id=?",
        "SELECT fact, fact='acked' AS is_receipt FROM milestones WHERE at > 10",
        "SELECT fact FROM milestones WHERE at > 10 ORDER BY fact='acked'",
        "SELECT fact FROM milestones WHERE at > 10 AND (fact='acked')=0",
        "INSERT INTO board(message_id,fact) SELECT message_id,fact FROM milestones",
    ],
    ids=[
        "pointer_table",
        "quoted_ledger_fact",
        "joined_qualified_fact",
        "unfiltered",
        "parameterized_fact",
        "parameterized_in_list",
        "restriction_widened_by_or",
        "negated_equality",
        "negated_in_list",
        "not_equal_bang",
        "not_equal_angle",
        "exists_subquery",
        "union_widens",
        "join_without_fact_pin",
        "join_pinned_to_handoff_fact",
        "projected_comparison",
        "comment_before_table",
        "comma_join_pointer",
        "comma_join_ledger",
        "leading_empty_statement",
        "quoted_comment_marker",
        "lowercase_pointer_read",
        "mixed_case_ledger_read",
        "filtered_projected_comparison",
        "order_by_fact_comparison",
        "inverted_comparison",
        "write_that_reads_the_ledger",
    ],
)
def test_display_census_fails_on_a_planted_second_reader(tmp_path: Path, rogue_sql: str) -> None:
    for relative in _HANDOFF_READERS:
        (tmp_path / relative).parent.mkdir(exist_ok=True)
        (tmp_path / relative).write_text((_SRC / relative).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "board.py").write_text(f"def board(conn):\n    return conn.execute({rogue_sql!r})\n", encoding="utf-8")
    assert handoff_readers(tmp_path) == _HANDOFF_READERS | {"board.py"}


@pytest.mark.unit
def test_display_census_exempts_only_the_pinned_non_handoff_reads(tmp_path: Path) -> None:
    """The pinned fetch_prepared reads are exempt, reflowed or not; an unpinned restricted read still counts."""
    pinned = sorted(_NON_HANDOFF_READS)
    (tmp_path / "stall.py").write_text(
        f"SQL = {[sql.replace(' ', chr(10) + ' ') for sql in pinned]!r}\n", encoding="utf-8"
    )
    assert handoff_readers(tmp_path) == set()
    (tmp_path / "probe.py").write_text(
        "SQL = \"SELECT at FROM milestones WHERE fact IN ('admitted','acked')\"\n", encoding="utf-8"
    )
    assert handoff_readers(tmp_path) == {"probe.py"}
