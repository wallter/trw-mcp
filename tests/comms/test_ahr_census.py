"""PRD-CORE-349 FR08/NFR01: one append-only writer of ``ahr_events``, and no partial event on a crash."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms._ahr_support import events, handoff_doc, inbox, offer, read_back, readback_doc
from tests.comms.test_policy import SendScene, scene  # noqa: F401

_SRC = Path(__file__).resolve().parents[2] / "src" / "trw_mcp"
_WRITER = "comms/_ahr_events.py"
_IDENT = r'(?:"[^"]+"|\[[^\]]+\]|`[^`]+`|\w+)'
_TABLE = r'(?:"ahr_events"|\[ahr_events\]|`ahr_events`|ahr_events(?![\w-]))'
_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+"
    rf"(?:{_IDENT}\s*\.\s*)?{_TABLE}",
    re.IGNORECASE,
)
#: The single admitted write, verbatim (learning L-oOvZ: pin statements, never exempt a pattern).
_ADMITTED = (
    "INSERT INTO ahr_events(handoff_id,seq,message_id,event_id,event,actor,at,subject,event_digest,event_doc,record) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
)


def _literals(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def ahr_writes(root: Path) -> dict[str, list[str]]:
    """Every literal SQL write to ``ahr_events`` under *root*, by file."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Adjacent string literals are one Constant after parsing, so a split statement is seen whole.
        hits = [text for text in _literals(tree) if _WRITE.search(text)]
        if hits:
            found[path.relative_to(root).as_posix()] = hits
    return found


@pytest.mark.unit
def test_ahr_events_has_one_writer_and_one_insert() -> None:
    assert ahr_writes(_SRC) == {_WRITER: [_ADMITTED]}


@pytest.mark.unit
def test_the_writer_never_updates_or_deletes_the_log() -> None:
    texts = _literals(ast.parse((_SRC / _WRITER).read_text(encoding="utf-8")))
    assert not [t for t in texts if re.search(r"\b(UPDATE|DELETE|REPLACE)\b", t) and "ahr_events" in t]


@pytest.mark.unit
@pytest.mark.parametrize(
    "rogue_sql",
    [
        "UPDATE ahr_events SET event='accepted' WHERE handoff_id=?",
        'DELETE FROM "ahr_events" WHERE handoff_id=?',
        "INSERT OR REPLACE INTO main.ahr_events VALUES (?)",
        "REPLACE INTO [ahr_events] VALUES (?)",
    ],
)
def test_census_catches_a_planted_writer(tmp_path: Path, rogue_sql: str) -> None:
    (tmp_path / "comms").mkdir()
    (tmp_path / _WRITER).write_text((_SRC / _WRITER).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "rogue.py").write_text(f"def rogue(conn):\n    conn.execute({rogue_sql!r})\n", encoding="utf-8")
    assert set(ahr_writes(tmp_path)) == {_WRITER, "rogue.py"}


def test_a_crash_between_append_and_commit_leaves_no_partial_event(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR01: the event is in the operation's one transaction; an unexpected failure after the INSERT rolls it back."""
    from trw_mcp.comms import _ahr_events

    handoff = handoff_doc()
    message_id = offer(scene, handoff)["receipt"]["message_id"]
    assert read_back(scene, message_id, readback_doc(handoff))["status"] == "ok"
    real = _ahr_events._append

    def crash(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)
        raise RuntimeError("injected fault after the INSERT, before COMMIT")

    monkeypatch.setattr(_ahr_events, "_append", crash)
    with pytest.raises(Exception, match="injected fault"):
        inbox(scene, "impl-2", "accept", message_id)
    monkeypatch.setattr(_ahr_events, "_append", real)
    assert [event for _seq, event, _actor in events(scene, handoff["handoff_id"])] == ["offered", "read_back"]
    assert scene.rows("SELECT fact FROM milestones WHERE message_id=? AND fact='accepted'", (message_id,)) == []
    assert inbox(scene, "impl-2", "accept", message_id)["status"] == "ok"
    assert [seq for seq, _event, _actor in events(scene, handoff["handoff_id"])] == [1, 2, 3]
