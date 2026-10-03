"""``repair_legacy_anchors``: provenance-only classification, revision-guarded writes, full paging (E2E-INC-025)."""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.models.memory import Anchor, MemoryEntry

from tests._memory_fixtures import FAKE_NAMESPACE
from tests._memory_store_fake import FakeMemoryStore
from tests._structlog_capture import captured_structlog  # noqa: F401  (fixture, imported by name)
from trw_mcp.state import _anchor_repair
from trw_mcp.state._anchor_repair import repair_legacy_anchors

pytestmark = pytest.mark.usefixtures("fake_memory_store")


def _seed(store: FakeMemoryStore, entry_id: str, *files: str) -> None:
    entry = MemoryEntry(
        id=entry_id,
        content=f"lesson {entry_id}",
        namespace=FAKE_NAMESPACE,
        anchors=[
            Anchor.model_construct(file=f, symbol_name="sym", symbol_type="function", signature="", line_range=None)
            for f in files
        ],  # construct: a legacy row may hold what Anchor now refuses
    )
    store.rows[(FAKE_NAMESPACE, entry_id)] = entry


def _files(store: FakeMemoryStore, entry_id: str) -> list[str]:
    return [a.file for a in store.rows[(FAKE_NAMESPACE, entry_id)].anchors]


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    (tmp_path / ".trw").mkdir()
    return tmp_path / ".trw"


def test_key_with_this_checkouts_prefix_is_rewritten(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    _seed(fake_memory_store, "L1", str(tmp_path / "src" / "a.py").lstrip("/"))

    assert repair_legacy_anchors(trw_dir, tmp_path).changed == 1
    assert _files(fake_memory_store, "L1") == ["src/a.py"]


@pytest.mark.parametrize("key", ["/abs/path.py", "/Users/bob/proj/x.py"])
def test_only_a_key_whose_shape_proves_an_absolute_origin_is_dropped(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, key: str
) -> None:
    _seed(fake_memory_store, "L1", key, "src/keep.py")

    assert repair_legacy_anchors(trw_dir, tmp_path).changed == 1
    assert _files(fake_memory_store, "L1") == ["src/keep.py"]


@pytest.mark.parametrize(
    ("key", "machine_shaped"),
    [
        ("Users/bob/x.py", True),
        ("home/bob/x.py", True),
        ("var/deleted_source.py", True),
        ("opt/deleted_source.py", True),
        ("tmp/deleted_source.py", True),  # bare tmp/ is a plausible repo dir: kept, never dropped
        ("src/deleted.py", False),
    ],
)
def test_a_stale_relative_key_is_kept_counted_when_machine_shaped_and_never_holds_the_marker(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, key: str, machine_shaped: bool
) -> None:
    _seed(fake_memory_store, "L1", key)

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert (outcome.changed, outcome.ambiguous) == (0, int(machine_shaped))
    assert _files(fake_memory_store, "L1") == [key]  # never deleted, whether or not the file exists
    assert (trw_dir / "context" / "anchors_repo_relative").exists()  # a clean pass, ambiguity or not


def test_a_relative_key_spelled_like_the_home_directory_is_kept(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With home=/srv/alice, ``srv/alice/deleted_source.py`` is a relative key: a spelling is not provenance."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/srv/alice")))
    _seed(fake_memory_store, "L1", "srv/alice/deleted_source.py")

    outcome = repair_legacy_anchors(trw_dir, tmp_path)
    assert (outcome.changed, outcome.ambiguous) == (0, 1)
    assert _files(fake_memory_store, "L1") == ["srv/alice/deleted_source.py"]


def test_the_ambiguous_count_is_logged_and_reaches_the_deliver_result(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, captured_structlog: list[dict[str, object]]
) -> None:
    from trw_mcp.tools._ceremony_deliver_steps import _repair_legacy_anchors

    _seed(fake_memory_store, "L1", "Users/bob/x.py", "home/bob/y.py")
    results: dict[str, dict[str, object]] = {"knowledge_sync": {}}

    _repair_legacy_anchors(trw_dir, results)  # type: ignore[arg-type]

    note = str(results["knowledge_sync"]["anchor_review"])
    assert note.startswith("2 anchors look machine-shaped but aren't under this checkout; kept")
    assert [e["files"] for e in captured_structlog if e.get("event") == "anchor_repair_ambiguous_key_kept"] == [
        ["Users/bob/x.py", "home/bob/y.py"]
    ]
    later: dict[str, dict[str, object]] = {"knowledge_sync": {}}
    _repair_legacy_anchors(trw_dir, later)  # type: ignore[arg-type]
    assert later["knowledge_sync"] == {}  # the marker is written, so nothing more to say


def test_a_repaired_key_is_stable_on_the_second_run(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    """A checkout-prefixed ``opt/...`` key becomes ``opt/deleted_source.py`` and must then stay."""
    root = tmp_path / "proj"
    root.mkdir()
    _seed(fake_memory_store, "L1", str(root / "opt" / "deleted_source.py").lstrip("/"))

    assert repair_legacy_anchors(trw_dir, root).changed == 1
    assert repair_legacy_anchors(trw_dir, root).changed == 0
    assert _files(fake_memory_store, "L1") == ["opt/deleted_source.py"]


def test_a_concurrent_anchor_edit_is_not_overwritten(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = str(tmp_path / "src" / "a.py").lstrip("/")
    _seed(fake_memory_store, "L1", broken)
    real_list = fake_memory_store.list_entries
    bumped: list[bool] = []

    def list_then_race(*args: object, **kwargs: object) -> list[MemoryEntry]:
        rows = real_list(*args, **kwargs)  # type: ignore[arg-type]
        if not bumped:  # a concurrent writer adds an anchor between the scan and the repair's write
            bumped.append(True)
            live = fake_memory_store.rows[(FAKE_NAMESPACE, "L1")]
            fake_memory_store._write(
                (FAKE_NAMESPACE, "L1"),
                live.model_copy(update={"anchors": [*live.anchors, Anchor(file="src/new.py", symbol_name="n")]}),
            )
        return rows

    monkeypatch.setattr(fake_memory_store, "list_entries", list_then_race)

    assert repair_legacy_anchors(trw_dir, tmp_path).changed == 1
    assert sorted(_files(fake_memory_store, "L1")) == ["src/a.py", "src/new.py"]  # the racer's anchor survived


def test_a_row_that_keeps_conflicting_is_skipped_and_withholds_the_marker(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fake_memory_store, "L1", str(tmp_path / "src" / "a.py").lstrip("/"))
    monkeypatch.setattr(
        fake_memory_store, "correct", lambda lid, patch: {"learning_id": lid, "status": "conflict", "error": "moved"}
    )

    assert repair_legacy_anchors(trw_dir, tmp_path).changed == 0
    assert not (trw_dir / "context" / "anchors_repo_relative").exists()


def test_every_row_is_reached_across_pages_and_the_marker_comes_last(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_anchor_repair, "_PAGE", 2)
    for i in range(5):
        _seed(fake_memory_store, f"L{i}", str(tmp_path / "src" / f"f{i}.py").lstrip("/"))
    marker = trw_dir / "context" / "anchors_repo_relative"

    done = []
    for _ in range(2):
        done.append(repair_legacy_anchors(trw_dir, tmp_path).changed)
        assert not marker.exists()
    done.append(repair_legacy_anchors(trw_dir, tmp_path).changed)

    assert done == [2, 2, 1]
    assert [_files(fake_memory_store, f"L{i}") for i in range(5)] == [[f"src/f{i}.py"] for i in range(5)]
    assert marker.exists()


@pytest.mark.parametrize("key", ["home/components/Hero.tsx", "Users/app/models/user.py", "tmp/fixtures/data.py"])
def test_an_existing_repo_file_under_a_machine_looking_top_dir_is_kept_and_not_ambiguous(
    tmp_path: Path, key: str
) -> None:
    """A real top-level home/, Users/ or tmp/ directory in the repo is never mistaken for a machine path."""
    from trw_mcp.state._anchor_repair import _repaired_file

    (tmp_path / key).parent.mkdir(parents=True)
    (tmp_path / key).write_text("x\n", encoding="utf-8")
    assert _repaired_file(key, tmp_path) == (key, False, False)


def test_a_row_inserted_between_pages_is_repaired_before_the_marker(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newest-first paging shifts offsets: the row inserted after page 1 escapes page 2 but not the tail scan."""
    monkeypatch.setattr(_anchor_repair, "_PAGE", 2)
    for i in range(3):
        _seed(fake_memory_store, f"L{i}", str(tmp_path / "src" / f"f{i}.py").lstrip("/"))
    real_list = fake_memory_store.list_entries
    calls: list[int] = []

    def newest_first(*args: object, **kwargs: object) -> list[MemoryEntry]:
        calls.append(1)
        if len(calls) == 2:  # the second deliver's listing: a legacy row arrived after page 1
            _seed(fake_memory_store, "LNEW", str(tmp_path / "src" / "late.py").lstrip("/"))
        limit = int(kwargs.pop("limit"))  # type: ignore[call-overload]
        rows = real_list(*args, limit=10_000, **kwargs)  # type: ignore[arg-type]
        return sorted(rows, key=lambda e: e.updated_at, reverse=True)[:limit]

    monkeypatch.setattr(fake_memory_store, "list_entries", newest_first)
    marker = trw_dir / "context" / "anchors_repo_relative"

    for _ in range(2):  # pages 1 and 2: LNEW arrives before page 2 and shifts it past itself
        repair_legacy_anchors(trw_dir, tmp_path)
        assert not marker.exists()
    assert _files(fake_memory_store, "LNEW") != ["src/late.py"]  # no page has reached it
    repair_legacy_anchors(trw_dir, tmp_path)  # end of pass: the tail scan repairs it, then the marker

    assert _files(fake_memory_store, "LNEW") == ["src/late.py"]
    assert marker.exists()


@pytest.mark.parametrize("key", ["C:/src/a.py", "C:\\src\\a.py"])
@pytest.mark.parametrize("exists", [True, False])
def test_a_drive_shaped_key_is_a_valid_relative_path_and_is_kept(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, key: str, exists: bool
) -> None:
    """codex r3: ``C:/src/a.py`` is a legal relative POSIX path; a drive shape is ambiguous, never dropped."""
    if exists:
        (tmp_path / key).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / key).write_text("x\n", encoding="utf-8")
    _seed(fake_memory_store, "L1", key, "src/keep.py")

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert _files(fake_memory_store, "L1") == [key, "src/keep.py"]
    assert outcome.changed == 0
    assert outcome.ambiguous == (0 if exists else 1)


def test_ambiguity_found_only_by_the_tail_scan_reaches_the_outcome_once(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r3 known issue: a machine-shaped key first seen by the tail scan is counted, and only once."""
    monkeypatch.setattr(_anchor_repair, "_PAGE", 2)
    _seed(fake_memory_store, "L0", "src/ok.py")  # one short page: this deliver ends the pass and runs the tail scan
    real_list = fake_memory_store.list_entries
    calls: list[int] = []

    def newest_first(*args: object, **kwargs: object) -> list[MemoryEntry]:
        calls.append(1)
        if len(calls) == 2:  # arrives after the page listing, so only the tail scan (which repeats) sees it
            _seed(fake_memory_store, "LTAIL", "Users/bob/stale.py")
        limit = int(kwargs.pop("limit"))  # type: ignore[call-overload]
        rows = real_list(*args, limit=10_000, **kwargs)  # type: ignore[arg-type]
        return sorted(rows, key=lambda e: e.updated_at, reverse=True)[:limit]

    monkeypatch.setattr(fake_memory_store, "list_entries", newest_first)
    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert _files(fake_memory_store, "LTAIL") == ["Users/bob/stale.py"]
    assert outcome.ambiguous == 1
    assert outcome.review_note is not None


def _validity(store: FakeMemoryStore, entry_id: str) -> float | None:
    return store.rows[(FAKE_NAMESPACE, entry_id)].anchor_validity


@pytest.mark.parametrize("seg", [".trw", ".claude"])
@pytest.mark.parametrize("prefix", ["", "ROOT"])
def test_a_worktree_segment_is_cut_from_absolute_and_relative_keys(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, seg: str, prefix: str
) -> None:
    """FR01: ``.trw|.claude/worktrees/<id>/`` dies with its worktree, so the key keeps only the tail."""
    head = str(tmp_path).lstrip("/") + "/" if prefix else ""
    _seed(fake_memory_store, "L1", f"{head}{seg}/worktrees/wt-1/src/a.py", f"{seg}/worktrees/not-a-tail")

    assert repair_legacy_anchors(trw_dir, tmp_path).changed == 1
    assert _files(fake_memory_store, "L1") == ["src/a.py", f"{seg}/worktrees/not-a-tail"]


def test_a_key_under_a_temp_root_that_is_absent_from_the_checkout_is_dropped_and_counted(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    """FR02: a slash-stripped temp path is dead after the session; the row itself is never deleted."""
    import tempfile

    resolved = str(Path(tempfile.gettempdir()).resolve()).lstrip("/")
    temp_keys = [
        "private/tmp/claude-501/x/a.py",
        "var/folders/zz/T/b.py",
        "private/var/folders/ab/cd/T/c.py",
        # a gettempdir() of bare /tmp (Linux) is deliberately not a temp root, so use a provable one there
        f"{resolved}/other/d.py" if resolved != "tmp" else "private/tmp/other/d.py",
    ]
    (tmp_path / "var" / "folders").mkdir(parents=True)
    (tmp_path / "var" / "folders" / "keep.py").write_text("x\n", encoding="utf-8")
    for n, chunk in enumerate([temp_keys[:2], temp_keys[2:]]):  # an entry holds at most 3 anchors
        _seed(fake_memory_store, f"L{n}", *chunk, "src/keep.py")
    _seed(fake_memory_store, "LK", "var/folders/keep.py")

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert (outcome.changed, outcome.dropped_temp, outcome.dropped_absolute, outcome.ambiguous) == (2, 4, 0, 0)
    assert [_files(fake_memory_store, i) for i in ("L0", "L1", "LK")] == [
        ["src/keep.py"],
        ["src/keep.py"],
        ["var/folders/keep.py"],  # exists under the checkout: kept
    ]
    assert (FAKE_NAMESPACE, "L1") in fake_memory_store.rows  # the row stays; only its anchors went


def test_the_drop_of_an_absolute_key_is_counted(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    _seed(fake_memory_store, "L1", "/abs/path.py", "src/keep.py")

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert (outcome.dropped_absolute, outcome.dropped_temp, outcome.validity_refreshed) == (1, 0, 1)


def test_a_rewritten_row_gets_its_anchor_validity_recomputed_in_the_same_write(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    """FR03: the kept anchors are scored against the checkout; an untouched row keeps its stored score."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def sym(): ...\n", encoding="utf-8")
    _seed(fake_memory_store, "L1", ".trw/worktrees/wt-1/src/a.py", "private/tmp/gone/x.py")
    _seed(fake_memory_store, "L2", "src/a.py")
    for row_id in ("L1", "L2"):
        row = fake_memory_store.rows[(FAKE_NAMESPACE, row_id)]
        fake_memory_store.rows[(FAKE_NAMESPACE, row_id)] = row.model_copy(update={"anchor_validity": 0.0})

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert _validity(fake_memory_store, "L1") == 1.0
    assert _validity(fake_memory_store, "L2") == 0.0
    corrections = [c for c in fake_memory_store.calls if c[0] == "correct"]
    assert len(corrections) == 1  # one patch carried both the anchors and the score
    assert outcome.validity_refreshed == 1


def test_the_marker_records_repair_version_two_and_a_version_one_marker_reruns_once(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    """FR04: stores a v1 repair finished still hold worktree and temp keys; they get one more pass."""
    marker = trw_dir / "context" / "anchors_repo_relative"
    marker.parent.mkdir()
    marker.write_text("done\n", encoding="utf-8")  # what v1 wrote
    _seed(fake_memory_store, "L1", ".trw/worktrees/wt-1/src/a.py")

    first = repair_legacy_anchors(trw_dir, tmp_path)

    assert first.changed == 1 and first.complete
    assert marker.read_text(encoding="utf-8") != "done\n"
    assert '"version": 2' in (trw_dir / "context" / "anchors_repair_cursor").read_text(encoding="utf-8")
    _seed(fake_memory_store, "L2", ".trw/worktrees/wt-2/src/b.py")
    again = repair_legacy_anchors(trw_dir, tmp_path)  # a v2 marker: nothing runs
    assert again.changed == 0 and again.complete
    assert _files(fake_memory_store, "L2") == [".trw/worktrees/wt-2/src/b.py"]


def test_a_version_one_cursor_does_not_resume_a_pass(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    """A half pass left by v1 skipped rows the v2 rules would change, so it restarts from offset 0."""
    cursor = trw_dir / "context" / "anchors_repair_cursor"
    cursor.parent.mkdir()
    cursor.write_text('{"offset": 99, "held": false, "hwm": "2026-01-01T00:00:00+00:00"}', encoding="utf-8")
    _seed(fake_memory_store, "L1", ".trw/worktrees/wt-1/src/a.py")

    assert repair_legacy_anchors(trw_dir, tmp_path).changed == 1


def test_a_held_page_is_reported_held_and_incomplete(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(fake_memory_store, "L1", ".trw/worktrees/wt-1/src/a.py")
    monkeypatch.setattr(
        fake_memory_store, "correct", lambda lid, patch: {"learning_id": lid, "status": "conflict", "error": "moved"}
    )

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert outcome.held and not outcome.complete


def test_a_row_that_loses_every_anchor_keeps_its_score_and_is_not_counted_refreshed(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path
) -> None:
    """An empty anchor list would score a perfect 1.0; the repair must not stamp that on a row it emptied."""
    _seed(fake_memory_store, "L1", "private/tmp/claude-501/gone/a.py")

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert (outcome.changed, outcome.dropped_temp, outcome.validity_refreshed) == (1, 1, 0)
    assert _files(fake_memory_store, "L1") == []
    assert fake_memory_store.rows[(FAKE_NAMESPACE, "L1")].anchor_validity != 1.0


def test_a_marker_that_is_not_a_regular_file_never_blocks_and_is_not_current(tmp_path: Path) -> None:
    """codex r1 KI: reading a FIFO marker would block forever; it is simply not a v2 marker."""
    import os

    from trw_mcp.state._anchor_repair import _marker_is_current

    fifo = tmp_path / "anchors_repo_relative"
    os.mkfifo(fifo)
    assert _marker_is_current(fifo) is False
