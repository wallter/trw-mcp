"""DELIVER-JOURNAL-TORN-TAIL-ONLY: only a torn LAST line of session-events.jsonl is tolerated.

A malformed line in the middle used to be skipped like a torn tail, so a damaged or hidden ``file_modified`` /
``change_evidence_unknown`` line lowered the counts the deliver gate enforces. Now it makes the stream untrusted and
every reader fails closed; a torn final line (an interrupted append) is still tolerated.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# The deliver paths here assume a client with a change-evidence writer; declared, never inherited from the shell.
pytestmark = pytest.mark.usefixtures("claude_code_client")

_SESSION = "sess-torn-tail"


def _stamp(offset: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trw(tmp_path: Path, *lines: str, torn: str = "") -> Path:
    """The stream as the hook writes it: newline-terminated lines, plus an optional unterminated (torn) final one."""
    trw = tmp_path / ".trw"
    (trw / "context").mkdir(parents=True)
    (trw / "context" / "ceremony-state.json").write_text(json.dumps({"session_started": True}), encoding="utf-8")
    text = "".join(line + "\n" for line in lines) + torn
    (trw / "context" / "session-events.jsonl").write_text(text, encoding="utf-8")
    return trw


def _edit(path: str) -> str:
    return json.dumps({"ts": _stamp(), "event": "file_modified", "session_id": _SESSION, "file": path})


_UNKNOWN = json.dumps({"ts": _stamp(), "event": "change_evidence_unknown", "reason": "jq_unavailable"})
_CORRUPT = '{"ts": "2026-09-30T00:00:00Z", "event": "file_modif'  # a damaged line, not valid JSON


def test_the_parser_tolerates_only_a_torn_last_line() -> None:
    from trw_mcp.tools._session_stream import session_stream_records

    assert session_stream_records('{"a": 1}\n{"b": 2}\n' + _CORRUPT) == [{"a": 1}, {"b": 2}]  # torn: no newline
    assert session_stream_records('{"a": 1}\n' + _CORRUPT + '\n{"b": 2}\n') is None  # malformed middle
    assert session_stream_records('{"a": 1}\n' + _CORRUPT + "\n") is None  # malformed last line WITH a newline
    assert session_stream_records('{"a": 1}\n[]\n') is None  # parses, but not an object: the writer never writes it
    assert session_stream_records('"text"\n{"a": 1}\n') is None
    assert session_stream_records("\n\n") == []


class TestUnpinnedChangedFiles:
    def test_a_malformed_middle_line_makes_the_count_uncomputable(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_event_checks import unpinned_session_changed_files

        trw = _trw(tmp_path, _edit("src/a.py"), _CORRUPT, _edit("src/b.py"))
        assert unpinned_session_changed_files(trw, _SESSION) is None  # was 2: the hidden line lowered nothing visible

    def test_a_torn_last_line_is_tolerated(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_event_checks import unpinned_session_changed_files

        trw = _trw(tmp_path, _edit("src/a.py"), _edit("src/b.py"), torn=_CORRUPT)
        assert unpinned_session_changed_files(trw, _SESSION) == 2

    def test_a_malformed_newline_terminated_last_line_is_not_torn(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_event_checks import unpinned_session_changed_files

        trw = _trw(tmp_path, _edit("src/a.py"), _CORRUPT)  # junk + newline at the end could hide a final record
        assert unpinned_session_changed_files(trw, _SESSION) is None


class TestChangeEvidenceUnknown:
    def test_a_malformed_middle_line_fails_closed(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_event_checks import change_evidence_unknown

        _trw(tmp_path, _edit("src/a.py"), _CORRUPT, _edit("src/b.py"))
        assert change_evidence_unknown(tmp_path) is True

    def test_a_torn_last_line_is_tolerated(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_event_checks import change_evidence_unknown

        _trw(tmp_path, _edit("src/a.py"), torn=_CORRUPT)
        assert change_evidence_unknown(tmp_path) is False
        _trw(tmp_path / "u", _UNKNOWN, torn=_CORRUPT)
        assert change_evidence_unknown(tmp_path / "u") is True


class TestFileModifiedSince:
    @pytest.fixture
    def since(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(days=1)  # every stamped edit predates it

    def test_a_malformed_middle_line_counts_as_modified(self, tmp_path: Path, since: datetime) -> None:
        from trw_mcp.tools._delivery_event_checks import _file_modified_since

        trw = _trw(tmp_path, _edit("src/a.py"), _CORRUPT, _edit("src/b.py"))
        assert _file_modified_since(trw, _SESSION, since, unscoped_since=None) is True

    def test_a_torn_last_line_is_tolerated(self, tmp_path: Path, since: datetime) -> None:
        from trw_mcp.tools._delivery_event_checks import _file_modified_since

        trw = _trw(tmp_path, _edit("src/a.py"), torn=_CORRUPT)
        assert _file_modified_since(trw, _SESSION, since, unscoped_since=None) is False
