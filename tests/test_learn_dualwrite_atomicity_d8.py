"""D8 regression: YAML sidecar must never survive a DB store the DB rejected.

Root cause (memory-hygiene audit 2026-07-09, learning L-CEp5): the learn write
path is a non-atomic dual-write. ``store_learning`` translates a storage failure
into ``{"status": "error", ...}`` instead of raising (JSON-RPC boundary), and the
orchestrator in ``_learn_impl.execute_learn`` used to fall through to
``_save_yaml_backup`` regardless — producing a YAML sidecar with no DB row
(unrecallable), and, because the entry never lands in the DB, semantic dedup can
never suppress a retry, so the same summary accumulates one orphan sidecar per
attempt (the observed "one summary 92x" pathology).

Both tests below assert the sidecar write is strictly downstream of a confirmed
DB write. ``test_store_error_writes_no_orphan_yaml`` FAILS before the fix (a
``*.yaml`` entry file is written) and PASSES after (no entry file). The dedup-skip
test locks in that a rejected (deduped) insert also produces no sidecar.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config import TRWConfig


def _entry_yaml_files(entries_dir: Path) -> list[Path]:
    """Per-learning sidecar files (exclude the aggregate index.yaml)."""
    return [p for p in entries_dir.glob("*.yaml") if p.name != "index.yaml"]


def _run_learn(trw_dir: Path, **overrides: object) -> object:
    from trw_mcp.tools._learn_impl import execute_learn

    kwargs: dict[str, object] = {
        "summary": "d8 atomicity probe summary",
        "detail": "detail body for the d8 dual-write atomicity probe",
        "trw_dir": trw_dir,
        "config": TRWConfig(dedup_enabled=False, embeddings_enabled=False),
        "tags": ["d8-probe"],
        "impact": 0.5,
        # No duplicate found -> proceed to the store path.
        "_check_and_handle_dedup": lambda *a, **k: None,
    }
    kwargs.update(overrides)
    return execute_learn(**kwargs)  # type: ignore[arg-type]


class TestDualWriteAtomicityD8:
    def test_store_error_writes_no_orphan_yaml(self, tmp_path: Path) -> None:
        """A failed DB store must NOT leave a YAML sidecar behind.

        What value would make this fail: any implementation that writes the
        sidecar when the store returned ``status="error"`` — i.e. the pre-fix
        fall-through. The assertion is on real filesystem state (entry-yaml
        count == 0), so it cannot pass while an orphan is written.
        """
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        def _failing_store(_trw_dir: Path, *, learning_id: str, **_kw: object) -> dict[str, object]:
            # Mirror _store_error_result: the store swallows a StorageError and
            # returns an error dict rather than raising.
            return {
                "learning_id": learning_id,
                "path": f"sqlite://{learning_id}",
                "status": "error",
                "error": "simulated disk-full StorageError",
                "distribution_warning": "",
            }

        result = _run_learn(trw_dir, _adapter_store=_failing_store)

        assert isinstance(result, dict)
        assert result.get("status") == "error"
        orphans = _entry_yaml_files(entries_dir)
        assert orphans == [], f"store failed but orphan sidecar(s) were written: {[p.name for p in orphans]}"

    def test_successful_store_does_write_yaml(self, tmp_path: Path) -> None:
        """Control: a SUCCESSFUL store still writes the sidecar (no regression)."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        def _ok_store(_trw_dir: Path, *, learning_id: str, **_kw: object) -> dict[str, object]:
            return {
                "learning_id": learning_id,
                "path": f"sqlite://{learning_id}",
                "status": "recorded",
                "distribution_warning": "",
            }

        result = _run_learn(trw_dir, _adapter_store=_ok_store)

        assert isinstance(result, dict)
        assert result.get("status") == "recorded"
        assert len(_entry_yaml_files(entries_dir)) == 1, "successful store must write exactly one sidecar"

    def test_dedup_reject_writes_no_orphan_yaml(self, tmp_path: Path) -> None:
        """A dedup-rejected (skipped) insert must not write a sidecar either.

        What value would make this fail: any path that writes a sidecar for an
        insert the dedup verdict rejected. Asserts on real entry-yaml count.
        """
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        def _skip_dedup(params: object, *_a: object, **_k: object) -> dict[str, object]:
            return {
                "status": "skipped",
                "learning_id": getattr(params, "learning_id", "L-x"),
                "duplicate_of": "L-existing",
                "similarity": 0.99,
                "message": "Near-identical entry already exists: L-existing",
            }

        def _must_not_store(*_a: object, **_k: object) -> dict[str, object]:
            raise AssertionError("store must not run for a dedup-rejected insert")

        result = _run_learn(trw_dir, _check_and_handle_dedup=_skip_dedup, _adapter_store=_must_not_store)

        assert isinstance(result, dict)
        assert result.get("status") == "skipped"
        assert _entry_yaml_files(entries_dir) == []
