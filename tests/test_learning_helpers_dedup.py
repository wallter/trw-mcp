"""Tests for learning helper dedup handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._learning_helpers_test_support import _CFG, set_project_root  # noqa: F401
from tests._memory_fixtures import DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._learning_helpers import LearningParams, check_and_handle_dedup


class TestCheckAndHandleDedup:
    """Tests for semantic dedup check helper."""

    def test_returns_none_when_disabled(self, tmp_path: Path) -> None:
        """When dedup is disabled, returns None (proceed to store)."""
        cfg = _CFG.model_copy(update={"dedup_enabled": False})
        result = check_and_handle_dedup(
            LearningParams(
                summary="summary",
                detail="detail",
                learning_id="L-test001",
                tags=["tag"],
                evidence=["evidence"],
                impact=0.8,
                source_type="agent",
                source_identity="",
            ),
            tmp_path / "entries",
            FileStateReader(),
            FileStateWriter(),
            cfg,
        )
        assert result is None

    def test_returns_none_when_no_duplicate(self, tmp_path: Path) -> None:
        """When no duplicate found, returns None."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.action = "store"
        mock_result.existing_id = None
        mock_result.similarity = 0.1

        with patch(
            "trw_mcp.state.dedup.dedup_verdict",
            return_value=mock_result,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="new summary",
                    detail="new detail",
                    learning_id="L-test002",
                    tags=[],
                    evidence=[],
                    impact=0.5,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                FileStateReader(),
                FileStateWriter(),
                _CFG,
            )
            assert result is None

    def test_returns_skip_result_on_exact_duplicate(self, tmp_path: Path) -> None:
        """When dedup says skip, returns a skip result dict."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        mock_result = MagicMock()
        mock_result.action = "skip"
        mock_result.existing_id = "L-existing001"
        mock_result.similarity = 0.98

        with patch(
            "trw_mcp.state.dedup.dedup_verdict",
            return_value=mock_result,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="duplicate summary",
                    detail="duplicate detail",
                    learning_id="L-test003",
                    tags=[],
                    evidence=[],
                    impact=0.5,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                FileStateReader(),
                FileStateWriter(),
                _CFG,
            )
            assert result is not None
            assert result["status"] == "skipped"
            assert result["duplicate_of"] == "L-existing001"
            assert result["similarity"] == 0.98

    def test_returns_merge_result_on_near_duplicate(self, tmp_path: Path) -> None:
        """When dedup says merge, merges and returns a merge result dict."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        existing_data = {
            "id": "L-existing002",
            "summary": "Existing learning",
            "detail": "Existing detail",
            "tags": [],
            "evidence": [],
            "impact": 0.7,
        }
        writer.write_yaml(entries_dir / "existing.yaml", existing_data)

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing002"
        mock_dedup.similarity = 0.88

        with (
            patch(
                "trw_mcp.state.dedup.dedup_verdict",
                return_value=mock_dedup,
            ),
            patch(
                "trw_mcp.state.dedup.merge_into_survivor",
            ) as mock_merge,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-duplicate summary",
                    detail="near-duplicate detail",
                    learning_id="L-test004",
                    tags=["tag"],
                    evidence=["evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )
            assert result is not None
            assert result["status"] == "merged"
            assert result["merged_into"] == "L-existing002"
            assert result["new_id"] == "L-test004"
            assert "message" in result
            assert mock_merge.called

    def test_merge_skips_index_yaml(self, tmp_path: Path) -> None:
        """Line 178: index.yaml is skipped when scanning for merge target."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        writer.write_yaml(
            entries_dir / "index.yaml",
            {
                "id": "L-existing010",
                "summary": "Index entry",
            },
        )
        writer.write_yaml(
            entries_dir / "real-entry.yaml",
            {
                "id": "L-existing010",
                "summary": "Real entry",
                "detail": "Detail",
                "tags": [],
                "evidence": [],
                "impact": 0.7,
            },
        )

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing010"
        mock_dedup.similarity = 0.85

        with (
            patch(
                "trw_mcp.state.dedup.dedup_verdict",
                return_value=mock_dedup,
            ),
            patch(
                "trw_mcp.state.dedup.merge_into_survivor",
            ) as mock_merge,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-dup summary",
                    detail="near-dup detail",
                    learning_id="L-test010",
                    tags=["tag"],
                    evidence=["evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        assert result["merged_into"] == "L-existing010"
        assert result["new_id"] == "L-test010"
        assert mock_merge.called
        actual_path = mock_merge.call_args[0][0]
        assert actual_path.name == "real-entry.yaml"

    def test_merge_inner_read_exception_continues(self, tmp_path: Path) -> None:
        """Lines 210-211: Exception reading a yaml file during merge scan continues."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        (entries_dir / "corrupt.yaml").write_text("{{invalid", encoding="utf-8")
        writer.write_yaml(
            entries_dir / "valid.yaml",
            {
                "id": "L-existing020",
                "summary": "Valid",
                "detail": "Detail",
                "tags": [],
                "evidence": [],
                "impact": 0.7,
            },
        )

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing020"
        mock_dedup.similarity = 0.85

        reader = FileStateReader()

        with (
            patch(
                "trw_mcp.state.dedup.dedup_verdict",
                return_value=mock_dedup,
            ),
            patch(
                "trw_mcp.state.dedup.merge_into_survivor",
            ) as mock_merge,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-dup summary",
                    detail="near-dup detail",
                    learning_id="L-test020",
                    tags=["tag"],
                    evidence=["evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        assert result["merged_into"] == "L-existing020"
        assert result["new_id"] == "L-test020"
        assert mock_merge.called

    def test_merge_syncs_merged_yaml_to_backend(self, tmp_path: Path) -> None:
        """Merged YAML state is written back to SQLite after dedup merges."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        writer.write_yaml(
            entries_dir / "existing.yaml",
            {
                "id": "L-existing030",
                "summary": "Existing learning",
                "detail": "short detail",
                "tags": ["existing"],
                "evidence": ["existing-evidence"],
                "impact": 0.6,
                "recurrence": 1,
                "merged_from": [],
                "assertions": [{"type": "grep_present", "pattern": "old", "target": "**/*.py"}],
            },
        )

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-existing030"
        mock_dedup.similarity = 0.89
        store = FakeMemoryStore()
        store.put("Existing learning", "default", {"entry_id": "L-existing030"})

        with (
            patch("trw_mcp.state.dedup.dedup_verdict", return_value=mock_dedup),
            patch("trw_mcp.state._store_selection.selected_store", return_value=(store, "default")),
            patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path / ".trw"),
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="Existing learning",
                    detail="this replacement detail is much longer than the old one",
                    learning_id="L-test030",
                    tags=["new-tag"],
                    evidence=["new-evidence"],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                    assertions=[{"type": "glob_exists", "pattern": "", "target": "src/main.py"}],
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        merged = store.get("L-existing030")
        assert merged is not None
        assert merged.recurrence == 2
        assert merged.importance == 0.8
        assert merged.tags == ["existing", "new-tag"]
        assert merged.evidence == ["existing-evidence", "new-evidence"]
        assert merged.merged_from == ["L-test030"]
        assert "Merged from L-test030" in merged.detail
        assert len(merged.assertions) == 2

    def test_a_dedup_failure_raises_instead_of_storing_an_unchecked_copy(self, tmp_path: Path) -> None:
        """PRD-CORE-302 C4: a daemon refusal is not "no duplicate"; storing anyway would skip the check."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir(parents=True)

        with (
            patch("trw_mcp.state.dedup.dedup_verdict", side_effect=RuntimeError("dedup boom")),
            pytest.raises(RuntimeError, match="dedup boom"),
        ):
            check_and_handle_dedup(
                LearningParams(
                    summary="summary",
                    detail="detail",
                    learning_id="L-test005",
                    tags=[],
                    evidence=[],
                    impact=0.5,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                FileStateReader(),
                FileStateWriter(),
                _CFG,
            )


# ---------------------------------------------------------------------------
# PRD-FIX-130-FR07: bounded id-to-path resolution on the merge verdict
# ---------------------------------------------------------------------------


class _CountingReader(FileStateReader):
    """A reader that records which entry YAML files were opened."""

    def __init__(self, entries_dir: Path) -> None:
        super().__init__()
        self.entries_dir = entries_dir
        self.opened: list[str] = []

    def read_yaml(self, path: Path) -> dict[str, object]:  # type: ignore[override]
        if path.parent == self.entries_dir and path.name != "index.yaml":
            self.opened.append(path.name)
        return super().read_yaml(path)


def _seed_real_entry(trw_dir: Path, summary: str) -> str:
    """Store one learning through the real path so backend row and sidecar agree."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._learn_impl import execute_learn

    result = execute_learn(
        summary=summary,
        detail="detail body for the bounded merge-resolution probe",
        trw_dir=trw_dir,
        config=TRWConfig(embeddings_enabled=False, dedup_enabled=False),
    )
    return str(result["learning_id"])


class TestBoundedMergeResolution:
    """FR07: resolving the survivor must not scan the corpus.

    The pre-change merge branch globbed the entries directory, sorted it, and
    read YAML files until one carried the matching id. Measured at 45,945 ms
    worst case against 6,532 files — and the worst case IS the common one,
    because the filenames are date-prefixed so a recently re-learned entry sorts
    LAST. It ran inside the per-record journal replay.

    NON-VACUITY: restore the sorted glob-and-read loop and
    ``test_merge_resolves_existing_id_without_scanning_the_corpus`` fails on the
    opened-file count (it reads every decoy that sorts before the target).
    """

    def test_merge_resolves_existing_id_without_scanning_the_corpus(
        self, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.state import _paths as paths_mod

        trw_dir = daemon_checkout.trw_dir
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        monkeypatch.setattr(paths_mod, "resolve_trw_dir", lambda *_a, **_kw: trw_dir)  # type: ignore[attr-defined]

        existing_id = _seed_real_entry(trw_dir, "bounded merge resolution survivor entry summary")
        writer = FileStateWriter()
        # Decoys that sort BEFORE the survivor: a linear scan reads all of them.
        for i in range(30):
            writer.write_yaml(
                entries_dir / f"0001-01-01-decoy-{i:03d}.yaml",
                {"id": f"L-decoy{i:03d}", "summary": f"decoy {i}", "detail": "d", "impact": 0.5},
            )

        reader = _CountingReader(entries_dir)
        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = existing_id
        mock_dedup.similarity = 0.87

        # The REAL merge runs. Mocking merge_into_survivor here hid two of the three
        # reads the bound is about (FIX130-06): merge_into_survivor re-read the
        # survivor, and _sync_merged_entry_to_backend read it again.
        with patch("trw_mcp.state.dedup.dedup_verdict", return_value=mock_dedup):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="near-duplicate of the bounded merge resolution survivor",
                    detail="near-dup detail body that is longer than the survivor's own detail",
                    learning_id="L-fr07001",
                    tags=["tag"],
                    evidence=[],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                reader,
                writer,
                _CFG,
            )

        assert result is not None
        assert result["status"] == "merged"
        assert result["merged_into"] == existing_id
        # TOTAL reads, not distinct filenames: a `set` collapses repeated reads of
        # the SAME file, which is exactly the cost FR07 bounds. The resolver reads
        # the survivor once and hands its parsed body to the merge and to the
        # backend sync, so a resolvable merge costs exactly one entry read.
        assert len(reader.opened) == 1, f"merge read {reader.opened} (FR07 bound is 1)"
        assert not any(name.startswith("0001-01-01-decoy") for name in reader.opened), reader.opened

        # Scope guard: the merge SEMANTICS are unchanged by the read bound.
        merged = FileStateReader().read_yaml(entries_dir / reader.opened[0])
        assert str(merged["id"]) == existing_id
        assert "L-fr07001" in [str(x) for x in merged["merged_from"]]
        assert int(str(merged["recurrence"])) == 2
        assert "tag" in [str(t) for t in merged["tags"]]

    def test_unresolvable_id_refuses_the_merge_and_returns_the_no_match_outcome(
        self, tmp_path: Path, monkeypatch: object
    ) -> None:
        """No survivor is guessed: the caller falls through to normal storage."""
        from trw_mcp.state import _paths as paths_mod

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        monkeypatch.setattr(paths_mod, "resolve_trw_dir", lambda *_a, **_kw: trw_dir)  # type: ignore[attr-defined]

        writer = FileStateWriter()
        writer.write_yaml(
            entries_dir / "2026-01-01-a-different-entry.yaml",
            {"id": "L-someoneelse", "summary": "an entry with a different id", "detail": "d", "impact": 0.5},
        )

        mock_dedup = MagicMock()
        mock_dedup.action = "merge"
        mock_dedup.existing_id = "L-nosuchid"
        mock_dedup.similarity = 0.87

        with (
            patch("trw_mcp.state.dedup.dedup_verdict", return_value=mock_dedup),
            patch("trw_mcp.state.dedup.merge_into_survivor") as mock_merge,
        ):
            result = check_and_handle_dedup(
                LearningParams(
                    summary="a summary whose survivor cannot be resolved at all",
                    detail="detail",
                    learning_id="L-fr07002",
                    tags=["tag"],
                    evidence=[],
                    impact=0.8,
                    source_type="agent",
                    source_identity="",
                ),
                entries_dir,
                FileStateReader(),
                writer,
                _CFG,
            )

        assert result is None, "an unresolvable id must never merge into a nearby entry"
        assert not mock_merge.called

    def test_resolve_entry_path_refuses_a_file_carrying_a_different_id(self, tmp_path: Path) -> None:
        """The computed candidate is accepted only when it PROVES the id."""
        from trw_mcp.state._entry_paths import resolve_entry_path

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            entries_dir / "2026-01-01-colliding-slug.yaml",
            {"id": "L-otherid", "summary": "colliding slug", "detail": "d", "impact": 0.5},
        )
        row = {"summary": "colliding slug", "created": "2026-01-01"}

        with patch("trw_mcp.state.memory_adapter.find_entry_by_id", return_value=row):
            assert resolve_entry_path(entries_dir, "L-wantedid", FileStateReader(), trw_dir=trw_dir) is None
            assert resolve_entry_path(entries_dir, "L-otherid", FileStateReader(), trw_dir=trw_dir) is not None


# ---------------------------------------------------------------------------
# Release-verify R3: a failed merge leaves nothing behind, so a retry merges once
# ---------------------------------------------------------------------------


class _FlakyStore(FakeMemoryStore):
    """A store whose ``correct`` fails as scripted: ``raise``, ``apply_then_raise`` or a refusal status."""

    def __init__(self, script: list[str]) -> None:
        super().__init__()
        self.script = script

    def correct(self, learning_id, patch):  # type: ignore[no-untyped-def]
        step = self.script.pop(0) if self.script else "ok"
        if step == "raise":
            raise RuntimeError("daemon unreachable before the write was sent")
        if step == "not_found":
            return {"status": "not_found", "error": f"{learning_id} is gone"}
        result = super().correct(learning_id, patch)
        if step == "apply_then_raise":
            raise RuntimeError("the connection failed after memory_update was sent, so it may have been applied")
        return result


class _FailingOnceWriter(FileStateWriter):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next = True

    def write_yaml(self, path: Path, data: dict[str, object]) -> None:  # type: ignore[override]
        if self.fail_next:
            self.fail_next = False
            raise OSError("disk full")
        super().write_yaml(path, data)


def _merge_setup(tmp_path: Path, store: FakeMemoryStore) -> Path:
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir(parents=True)
    FileStateWriter().write_yaml(
        entries_dir / "existing.yaml",
        {"id": "L-survivor", "summary": "Survivor", "detail": "short", "recurrence": 1, "merged_from": []},
    )
    store.put("Survivor", "default", {"entry_id": "L-survivor"})
    return entries_dir


def _learn_duplicate(tmp_path: Path, entries_dir: Path, store: FakeMemoryStore, learning_id: str, writer=None):  # type: ignore[no-untyped-def]
    verdict = MagicMock(action="merge", existing_id="L-survivor", similarity=0.9)
    with (
        patch("trw_mcp.state.dedup.dedup_verdict", return_value=verdict),
        patch("trw_mcp.state._store_selection.selected_store", return_value=(store, "default")),
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path / ".trw"),
    ):
        return check_and_handle_dedup(
            LearningParams(
                summary="Survivor",
                detail="a longer restatement of the survivor",
                learning_id=learning_id,
                tags=[],
                evidence=[],
                impact=0.5,
                source_type="agent",
                source_identity="",
            ),
            entries_dir,
            FileStateReader(),
            writer or FileStateWriter(),
            _CFG,
        )


@pytest.mark.parametrize("failure", ["raise", "apply_then_raise", "not_found", "sidecar_write"])
def test_a_retried_merge_after_a_failure_counts_the_duplicate_once(tmp_path: Path, failure: str) -> None:
    store = _FlakyStore([] if failure == "sidecar_write" else [failure])
    entries_dir = _merge_setup(tmp_path, store)
    writer = _FailingOnceWriter() if failure == "sidecar_write" else None

    with pytest.raises((RuntimeError, OSError)):
        _learn_duplicate(tmp_path, entries_dir, store, "L-attempt1", writer)
    assert FileStateReader().read_yaml(entries_dir / "existing.yaml")["recurrence"] == 1, (
        "the sidecar changed although the merge failed"
    )

    result = _learn_duplicate(tmp_path, entries_dir, store, "L-attempt2")

    assert result is not None and result["status"] == "merged"
    merged = store.get("L-survivor")
    assert merged is not None and merged.recurrence == 2
    assert merged.merged_from == ["L-attempt2"]
    assert FileStateReader().read_yaml(entries_dir / "existing.yaml")["recurrence"] == 2
