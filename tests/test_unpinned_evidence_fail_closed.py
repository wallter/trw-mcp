"""E2E-COV-UNPINNED-EVIDENCE: the unpinned-deliver evidence readers fail CLOSED (PRD-FIX-140-FR04, T29).

A delivery with no pinned run is gated on what ``.trw/context`` says about THIS session: a recorded,
still-current passing build, and a count of the files it changed. Every branch pinned here is one no
other test executed (coverage audit, trunk 88d3761217): each is a place where an unreadable, malformed,
undated or foreign record must count against the delivery, never for it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.tools._delivery_event_checks import (
    PROCESS_STARTED_AT,
    change_evidence_unknown,
    unpinned_build_failure_recorded,
    unpinned_build_passed,
    unpinned_session_changed_files,
)


@pytest.fixture(autouse=True)
def _client_with_a_change_evidence_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests read hook-written change records, which only a client with a registered writer produces.

    Set explicitly: the ambient environment must not decide it (E2E-INC-115 b).
    """
    monkeypatch.setenv("TRW_CLIENT_PROFILE", "claude-code")


_SID = "sid"


def _trw(tmp_path: Path, state: object | None = None, *, raw_state: str | None = None) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    if raw_state is not None:
        (trw_dir / "context" / "ceremony-state.json").write_text(raw_state, encoding="utf-8")
    elif state is not None:
        (trw_dir / "context" / "ceremony-state.json").write_text(json.dumps(state), encoding="utf-8")
    return trw_dir


def _stream(trw_dir: Path) -> Path:
    return trw_dir / "context" / "session-events.jsonl"


def _write_events(trw_dir: Path, *records: dict[str, object]) -> None:
    _stream(trw_dir).write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _passed_state(passed_at: str) -> dict[str, object]:
    return {
        "session_started": True,
        "session_build_results": {_SID: "passed"},
        "session_build_results_at": {_SID: passed_at},
    }


_PASS_AT = datetime.now(timezone.utc) - timedelta(minutes=10)
_AFTER_PASS = _PASS_AT + timedelta(minutes=5)


class TestCeremonyStateShape:
    @pytest.mark.parametrize(
        "state",
        [["session_started"], {"session_started": False, "session_build_results": {_SID: False}}],
        ids=["non-object-root", "never-started"],
    )
    def test_a_non_object_or_unstarted_state_records_nothing(self, tmp_path: Path, state: object) -> None:
        trw_dir = _trw(tmp_path, state)

        assert unpinned_build_failure_recorded(trw_dir, _SID) is False
        assert unpinned_build_passed(trw_dir, _SID) is False

    def test_an_unparseable_state_is_uncomputable_not_clean(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, raw_state="{torn")

        assert unpinned_build_failure_recorded(trw_dir, _SID) is None
        assert unpinned_session_changed_files(trw_dir, _SID) is None
        assert unpinned_build_passed(trw_dir, _SID) is False


class TestRecordedBuildFailure:
    @pytest.mark.parametrize("recorded", [False, " FAIL ", "false"], ids=["bool-false", "padded-fail", "str-false"])
    def test_a_recorded_failure_in_any_writer_spelling_is_a_failure(self, tmp_path: Path, recorded: object) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True, "session_build_results": {_SID: recorded}})

        assert unpinned_build_failure_recorded(trw_dir, _SID) is True

    def test_another_session_s_failure_is_not_this_session_s(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True, "session_build_results": {"other": False}})

        assert unpinned_build_failure_recorded(trw_dir, _SID) is False


class TestPassIsCurrent:
    def test_positive_control_a_stamped_pass_with_no_later_edit_stands(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, _passed_state(_PASS_AT.isoformat()))

        assert unpinned_build_passed(trw_dir, _SID) is True

    @pytest.mark.parametrize("stamp", ["", "not-a-time"], ids=["missing", "unparseable"])
    def test_a_pass_whose_stamp_cannot_be_read_is_not_current(self, tmp_path: Path, stamp: str) -> None:
        trw_dir = _trw(tmp_path, _passed_state(stamp))

        assert unpinned_build_passed(trw_dir, _SID) is False

    def test_a_pass_with_no_stamp_recorded_at_all_is_not_current(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True, "session_build_results": {_SID: True}})

        assert unpinned_build_passed(trw_dir, _SID) is False

    def test_a_naive_pass_stamp_is_read_as_utc(self, tmp_path: Path) -> None:
        """A naive stamp before a later edit is stale; one after it stands (no naive/aware crash either way)."""
        naive_pass = _PASS_AT.replace(tzinfo=None)
        trw_dir = _trw(tmp_path, _passed_state(naive_pass.isoformat()))
        _write_events(trw_dir, {"event": "file_modified", "session_id": _SID, "file": "a.py", "ts": _ts(_AFTER_PASS)})
        assert unpinned_build_passed(trw_dir, _SID) is False

        later = (_AFTER_PASS + timedelta(minutes=1)).replace(tzinfo=None)
        (trw_dir / "context" / "ceremony-state.json").write_text(
            json.dumps(_passed_state(later.isoformat())), encoding="utf-8"
        )
        assert unpinned_build_passed(trw_dir, _SID) is True

    @pytest.mark.parametrize("ts", [None, "unknown", ""], ids=["no-ts", "writer-unknown", "empty"])
    def test_an_edit_with_no_readable_time_makes_the_pass_stale(self, tmp_path: Path, ts: object) -> None:
        trw_dir = _trw(tmp_path, _passed_state(_PASS_AT.isoformat()))
        record: dict[str, object] = {"event": "file_modified", "session_id": _SID, "file": "a.py"}
        if ts is not None:
            record["ts"] = ts
        _write_events(trw_dir, record)

        assert unpinned_build_passed(trw_dir, _SID) is False

    def test_another_session_s_edit_is_ignored_only_while_scoped(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, _passed_state(_PASS_AT.isoformat()))
        _write_events(
            trw_dir, {"event": "file_modified", "session_id": "other", "file": "a.py", "ts": _ts(_AFTER_PASS)}
        )

        assert unpinned_build_passed(trw_dir, _SID) is True
        assert unpinned_build_passed(trw_dir, _SID, unscoped_since=_PASS_AT - timedelta(hours=1)) is False

    def test_an_unreadable_edit_from_any_session_makes_the_pass_stale(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, _passed_state(_PASS_AT.isoformat()))
        _write_events(trw_dir, {"event": "change_evidence_unknown", "session_id": "other", "ts": _ts(_AFTER_PASS)})

        assert unpinned_build_passed(trw_dir, _SID) is False

    def test_an_untrusted_stream_makes_the_pass_stale(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, _passed_state(_PASS_AT.isoformat()))
        _stream(trw_dir).write_text('{bad middle line\n{"event": "noise"}\n', encoding="utf-8")

        assert unpinned_build_passed(trw_dir, _SID) is False

    def test_an_unreadable_stream_makes_the_pass_stale(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, _passed_state(_PASS_AT.isoformat()))
        _stream(trw_dir).write_bytes(b"\xff\xfe not utf-8\n")

        assert unpinned_build_passed(trw_dir, _SID) is False


class TestChangeEvidenceUnknown:
    def test_no_root_or_no_stream_vouches_for_nothing_being_unknown(self, tmp_path: Path) -> None:
        assert change_evidence_unknown(None) is False
        assert change_evidence_unknown(tmp_path) is False

    @pytest.mark.parametrize("body", [b"\xff\xfe\n", b'{bad middle\n{"event": "x"}\n'], ids=["not-utf8", "untrusted"])
    def test_a_stream_that_cannot_vouch_is_unknown(self, tmp_path: Path, body: bytes) -> None:
        trw_dir = _trw(tmp_path)
        _stream(trw_dir).write_bytes(body)

        assert change_evidence_unknown(tmp_path) is True

    def test_an_unreadable_edit_counts_only_since_this_server_started(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path)
        before_boot = _ts(PROCESS_STARTED_AT - timedelta(hours=1))
        _write_events(trw_dir, {"event": "change_evidence_unknown", "ts": before_boot})
        assert change_evidence_unknown(tmp_path) is False

        _write_events(
            trw_dir,
            {"event": "change_evidence_unknown", "ts": before_boot},
            {"event": "change_evidence_unknown", "ts": _ts(PROCESS_STARTED_AT + timedelta(seconds=1))},
        )
        assert change_evidence_unknown(tmp_path) is True


class TestChangedFileCount:
    def test_the_checkpoint_count_survives_an_absent_stream(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True, "files_modified_since_checkpoint": 3})

        assert unpinned_session_changed_files(trw_dir, _SID) == 3

    def test_a_stream_that_is_not_a_regular_file_is_uncomputable(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True})
        _stream(trw_dir).mkdir()

        assert unpinned_session_changed_files(trw_dir, _SID) is None

    def test_an_unreadable_edit_since_boot_is_uncomputable(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True})
        _write_events(trw_dir, {"event": "change_evidence_unknown", "ts": _ts(datetime.now(timezone.utc))})

        assert unpinned_session_changed_files(trw_dir, _SID) is None

    def test_a_stream_that_cannot_be_decoded_is_uncomputable(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True})
        _stream(trw_dir).write_bytes(b"\xff\xfe not utf-8\n")

        assert unpinned_session_changed_files(trw_dir, _SID) is None

    def test_unscoped_counting_takes_only_records_inside_the_window(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True})
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
        _write_events(
            trw_dir,
            {"event": "file_modified", "session_id": "a", "file": "old.py", "ts": _ts(since - timedelta(hours=1))},
            {"event": "file_modified", "session_id": "b", "file": "new.py", "ts": _ts(since + timedelta(minutes=1))},
            {"event": "file_modified", "session_id": "c", "file": "undated.py", "ts": "unknown"},
        )

        # old.py is outside the window; an undated edit cannot be shown to predate it, so it counts.
        assert unpinned_session_changed_files(trw_dir, _SID, unscoped_since=since) == 2

    def test_the_larger_of_the_checkpoint_count_and_the_stream_wins(self, tmp_path: Path) -> None:
        trw_dir = _trw(tmp_path, {"session_started": True, "files_modified_since_checkpoint": 5})
        _write_events(trw_dir, {"event": "file_modified", "session_id": _SID, "file": "a.py", "ts": _ts(_AFTER_PASS)})

        assert unpinned_session_changed_files(trw_dir, _SID) == 5
