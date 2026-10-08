"""AHR 1.0-rc.2 application: session start lists handoff records a later session could take up.

The compaction self-handoff only works when the next session learns the record exists. These tests
drive ``read_handoff_records`` over real sealed records in a temporary ``.trw`` tree, including the
adversarial cases an independent review raised (symlinks, malformed files, unchecked supersession,
duplicate ids, read-back drafts, timestamp order), and the session-start step table through its adapter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.handoff._vectors import STANDARD, VECTORS
from trw_mcp.handoff import digest, load, seal, validate

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(
    directory: Path, handoff_id: str, *, age: timedelta, name: str | None = None, **changes: Any
) -> dict[str, Any]:
    doc = load(STANDARD)
    doc.pop("integrity", None)
    created = NOW - age
    doc.update(handoff_id=handoff_id, created_at=_stamp(created), expires_at=_stamp(created + timedelta(days=7)))
    doc["as_of"]["at"] = _stamp(created - timedelta(minutes=1))
    doc.update(changes)
    sealed = seal(doc)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name or handoff_id}.json").write_text(json.dumps(sealed, indent=2), encoding="utf-8")
    return sealed


def _readback(directory: Path, handoff: dict[str, Any], *, disposition: str = "ready", sealed: bool = True) -> None:
    """A read-back of *handoff* by its addressee that passes L1 against it (vector 02, re-pointed)."""
    rb = load(VECTORS / "valid" / "02-readback-for-01.json")
    rb.pop("integrity", None)
    at = datetime.strptime(handoff["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) + timedelta(minutes=10)
    rb["handoff"] = {"handoff_id": handoff["handoff_id"], "digest": digest(handoff)}
    rb["at"] = _stamp(at)
    rb["reverified"][0]["evidence"]["at"] = _stamp(at - timedelta(minutes=1))
    rb["disposition"] = disposition
    if disposition == "questions":
        rb["questions"] = ["Which worktree is the swap target?"]
    if sealed:
        rb = seal(rb)
        assert validate(rb, handoff) == [], validate(rb, handoff)
    (directory / f"{handoff['handoff_id']}.readback.rb1.json").write_text(json.dumps(rb), encoding="utf-8")


@pytest.fixture
def trw(tmp_path: Path) -> Path:
    return tmp_path / ".trw"


def _read(trw: Path) -> Any:
    from trw_mcp.tools._handoff_records_readback import read_handoff_records

    return read_handoff_records(trw, trw.parent, NOW)


def _ids(trw: Path) -> list[str]:
    found = _read(trw)
    return [] if found is None else [item["handoff_id"] for item in found["items"]]


def test_nothing_waiting_omits_the_block(trw: Path) -> None:
    (trw / "handoffs").mkdir(parents=True)
    assert _read(trw) is None


def test_waiting_records_are_listed_newest_first_with_their_digest(trw: Path) -> None:
    older = _record(trw / "handoffs", "ho-old", age=timedelta(hours=5))
    newer = _record(trw / "runs" / "task" / "run-1" / "handoffs", "ho-new", age=timedelta(hours=1))
    found = _read(trw)
    assert found["total"] == 2
    assert [item["handoff_id"] for item in found["items"]] == ["ho-new", "ho-old"]
    assert found["items"][0]["path"] == ".trw/runs/task/run-1/handoffs/ho-new.json"
    assert found["items"][0]["digest"] == digest(newer) and found["items"][1]["digest"] == digest(older)
    assert set(found["items"][0]) == {"path", "handoff_id", "subject", "tier", "to", "created_at", "digest"}


def test_a_sealed_ready_read_back_takes_the_record(trw: Path) -> None:
    handoff = _record(trw / "handoffs", "ho-taken", age=timedelta(hours=1))
    _readback(trw / "handoffs", handoff)
    assert _read(trw) is None


@pytest.mark.parametrize(("disposition", "sealed"), [("ready", False), ("questions", True)])
def test_a_draft_or_questions_read_back_keeps_the_record_listed(trw: Path, disposition: str, sealed: bool) -> None:
    handoff = _record(trw / "handoffs", "ho-pending", age=timedelta(hours=1))
    _readback(trw / "handoffs", handoff, disposition=disposition, sealed=sealed)
    (item,) = _read(trw)["items"]
    assert (item["handoff_id"], item["readback"]) == ("ho-pending", "in_progress")


def test_an_empty_file_named_like_a_read_back_claims_nothing(trw: Path) -> None:
    _record(trw / "handoffs", "ho-open", age=timedelta(hours=1))
    (trw / "handoffs" / "ho-open.readback.rb1.json").write_text("{}", encoding="utf-8")
    assert _ids(trw) == ["ho-open"]


def test_a_handoff_whose_name_contains_readback_is_still_a_handoff(trw: Path) -> None:
    _record(trw / "handoffs", "ho.readback.followup", age=timedelta(hours=1))
    assert _ids(trw) == ["ho.readback.followup"]


def test_expired_superseded_and_draft_records_are_not_listed(trw: Path) -> None:
    _record(trw / "handoffs", "ho-expired", age=timedelta(days=8))
    first = _record(trw / "handoffs", "ho-first", age=timedelta(hours=3))
    _record(
        trw / "handoffs",
        "ho-second",
        age=timedelta(hours=2),
        supersedes=[{"handoff_id": "ho-first", "digest": digest(first)}],
    )
    (trw / "handoffs" / "ho-draft.json").write_text(
        json.dumps({**load(STANDARD), "handoff_id": "ho-draft", "tier_reason": "TODO(handoff): why"}),
        encoding="utf-8",
    )
    assert _ids(trw) == ["ho-second"]


@pytest.mark.parametrize(
    "listing",
    [
        {"subject": "another-subject"},
        {"bad_digest": True},
        {"tier": "minimal"},
    ],
)
def test_an_ineligible_listing_does_not_hide_its_predecessor(trw: Path, listing: dict[str, Any]) -> None:
    first = _record(trw / "handoffs", "ho-first", age=timedelta(hours=3))
    pointer = "sha256:" + "0" * 64 if listing.pop("bad_digest", False) else digest(first)
    changes: dict[str, Any] = {"supersedes": [{"handoff_id": "ho-first", "digest": pointer}], **listing}
    if changes.get("tier") == "minimal":
        changes["readback"] = {"required": False}
    _record(trw / "handoffs", "ho-second", age=timedelta(hours=2), **changes)
    assert "ho-first" in _ids(trw)


def test_an_expired_successor_does_not_hide_its_predecessor(trw: Path) -> None:
    first = _record(trw / "handoffs", "ho-first", age=timedelta(hours=1))
    _record(
        trw / "handoffs",
        "ho-second",
        age=timedelta(days=8),
        supersedes=[{"handoff_id": "ho-first", "digest": digest(first)}],
    )
    assert _ids(trw) == ["ho-first"]


def test_a_taken_successor_still_hides_its_predecessor(trw: Path) -> None:
    first = _record(trw / "handoffs", "ho-first", age=timedelta(hours=3))
    second = _record(
        trw / "handoffs",
        "ho-second",
        age=timedelta(hours=2),
        supersedes=[{"handoff_id": "ho-first", "digest": digest(first)}],
    )
    _readback(trw / "handoffs", second)
    assert _read(trw) is None


def test_one_id_with_two_contents_is_withheld_and_named(trw: Path) -> None:
    _record(trw / "handoffs", "ho-dup", age=timedelta(hours=2))
    _record(trw / "runs" / "t" / "r" / "handoffs", "ho-dup", age=timedelta(hours=1))
    _record(trw / "handoffs", "ho-fine", age=timedelta(hours=3))
    found = _read(trw)
    assert [item["handoff_id"] for item in found["items"]] == ["ho-fine"]
    assert found["duplicate_ids"] == ["ho-dup"]


def test_a_symlink_out_of_trw_is_not_read(trw: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    _record(outside, "ho-outside", age=timedelta(hours=1))
    (trw / "handoffs").mkdir(parents=True)
    (trw / "handoffs" / "ho-outside.json").symlink_to(outside / "ho-outside.json")
    assert _read(trw) is None


def test_a_malformed_file_does_not_hide_the_others(trw: Path) -> None:
    _record(trw / "handoffs", "ho-healthy", age=timedelta(hours=1))
    deep = load(STANDARD)
    deep["extensions"] = {"https://example.org/deep": "__DEEP__"}
    # Build the 3,000-deep object as text: json.dumps of it exceeds the recursion limit on Python 3.11
    # (the release check's Linux leg), while the reader under test must still survive it.
    nested = '{"x": ' * 3000 + "{}" + "}" * 3000
    text = json.dumps(deep).replace('"__DEEP__"', nested)
    (trw / "handoffs" / "ho-deep.json").write_text(text, encoding="utf-8")
    assert _ids(trw) == ["ho-healthy"]


def test_newest_first_compares_instants_not_strings(trw: Path) -> None:
    _record(trw / "handoffs", "ho-whole", age=timedelta(hours=1))
    _record(trw / "handoffs", "ho-later", age=timedelta(hours=1), created_at="2026-10-08T11:00:00.5Z")
    assert _ids(trw) == ["ho-later", "ho-whole"]


def test_the_list_is_capped_but_the_total_is_not(trw: Path) -> None:
    from trw_mcp.tools._handoff_records_readback import HANDOFF_RECORDS_MAX_ITEMS

    for n in range(HANDOFF_RECORDS_MAX_ITEMS + 2):
        _record(trw / "handoffs", f"ho-{n}", age=timedelta(minutes=10 + n))
    found = _read(trw)
    assert found["total"] == HANDOFF_RECORDS_MAX_ITEMS + 2 and len(found["items"]) == HANDOFF_RECORDS_MAX_ITEMS


def test_scanning_is_bounded_and_says_so(trw: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import _handoff_records_readback as discovery

    monkeypatch.setattr(discovery, "MAX_SCANNED", 2)
    for n in range(4):
        _record(trw / "handoffs", f"ho-{n}", age=timedelta(minutes=10 + n))
    found = _read(trw)
    assert found["truncated"] is True and found["total"] <= 2


def test_no_free_text_from_the_record_reaches_the_result(trw: Path) -> None:
    """R-SEC-1: the goal, claims and constraints are data the session start never echoes."""
    _record(trw / "handoffs", "ho-inject", age=timedelta(hours=1))
    text = json.dumps(_read(trw))
    record = load(trw / "handoffs" / "ho-inject.json")
    assert record["objective"]["goal"] not in text and record["claims"][0]["text"] not in text


def test_the_session_start_step_sets_the_key_only_when_something_waits(
    trw: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _ceremony_step_table as table

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw.parent))
    (trw / "handoffs").mkdir(parents=True)

    class Ctx:
        results: dict[str, Any] = {}

    table._ss_handoff_records(Ctx())  # type: ignore[arg-type]
    assert "handoff_records" not in Ctx.results
    live = _stamp(datetime.now(UTC) + timedelta(days=1))
    _record(trw / "handoffs", "ho-live", age=timedelta(minutes=5), expires_at=live)
    table._ss_handoff_records(Ctx())  # type: ignore[arg-type]
    assert [item["handoff_id"] for item in Ctx.results["handoff_records"]["items"]] == ["ho-live"]
