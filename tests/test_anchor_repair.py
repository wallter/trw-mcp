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
        ("tmp/deleted_source.py", True),
        ("var/deleted_source.py", True),
        ("src/deleted.py", False),
    ],
)
def test_a_stale_relative_key_is_kept_counted_when_machine_shaped_and_never_holds_the_marker(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, key: str, machine_shaped: bool
) -> None:
    _seed(fake_memory_store, "L1", key)

    outcome = repair_legacy_anchors(trw_dir, tmp_path)

    assert outcome == (0, int(machine_shaped))
    assert _files(fake_memory_store, "L1") == [key]  # never deleted, whether or not the file exists
    assert (trw_dir / "context" / "anchors_repo_relative").exists()  # a clean pass, ambiguity or not


def test_a_relative_key_spelled_like_the_home_directory_is_kept(
    fake_memory_store: FakeMemoryStore, trw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With home=/srv/alice, ``srv/alice/deleted_source.py`` is a relative key: a spelling is not provenance."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/srv/alice")))
    _seed(fake_memory_store, "L1", "srv/alice/deleted_source.py")

    assert repair_legacy_anchors(trw_dir, tmp_path) == (0, 1)
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
    """A checkout-prefixed ``tmp/...`` key becomes ``tmp/deleted_source.py`` and must then stay."""
    root = tmp_path / "proj"
    root.mkdir()
    _seed(fake_memory_store, "L1", str(root / "tmp" / "deleted_source.py").lstrip("/"))

    assert repair_legacy_anchors(trw_dir, root).changed == 1
    assert repair_legacy_anchors(trw_dir, root).changed == 0
    assert _files(fake_memory_store, "L1") == ["tmp/deleted_source.py"]


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
