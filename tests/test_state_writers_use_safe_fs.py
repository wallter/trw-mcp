"""PRD-CORE-337 FR08 -- the R12 ``.trw/`` state writers refuse a planted symlink instead of following it.

Each writer is driven through its own public entry point (the function its MCP tool, CLI or hook
path calls), never a test-only helper. For each: a symlink planted at the writer's target raises
``UnsafeWriteError`` and the file the link points at is byte-identical afterwards; the same writer
against a plain target still produces the bytes the pre-migration writer produced (the literals
below were captured by running these inputs against the pre-migration tree, 9699f24fd). The
symlinked-PARENT case is checked on the stale-run sentinel in ``test_state_writer_entrypoints_safe_fs.py``
(R12 row 25's tombstone writer, which carried it before, was deleted with ``collect_receipts`` by CORE-313).

The FR08 writers outside FR05's four audited trees are held by the census file's extension
(``test_no_raw_checkout_writes_census.py``); their behaviour is proven in
``test_state_writer_entrypoints_safe_fs.py``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.exceptions import UnsafeWriteError

from tests._planted_symlink import assert_untouched, plant_symlink
from trw_mcp.state.requirements_registry import (
    ANCHOR_FILENAME,
    LEDGER_FILENAME,
    REGISTRY_FILENAME,
    RegistryBuildResult,
    RegistryWriter,
    action_digest,
    build_registry,
    persist_registry,
)

# --- requirements_registry.persist_registry (R12 row 19) ---------------------------------------------


def _empty_registry(project: Path) -> tuple[Path, RegistryBuildResult]:
    prds = project / "prds"
    prds.mkdir(parents=True)
    registry_dir = project / ".trw" / "registry"
    return registry_dir, build_registry(prds, registry_dir / LEDGER_FILENAME)


def test_requirements_registry_write_refuses_a_symlinked_target(tmp_path: Path) -> None:
    registry_dir, registry = _empty_registry(tmp_path / "project")
    link = registry_dir / REGISTRY_FILENAME
    victim = plant_symlink(link, tmp_path / "outside")

    with pytest.raises(UnsafeWriteError) as refused:
        persist_registry(registry, registry_dir)

    assert refused.value.reason == "symlink_leaf"
    assert_untouched(link, victim)


def test_requirements_registry_write_keeps_the_pre_migration_bytes(tmp_path: Path) -> None:
    registry_dir, registry = _empty_registry(tmp_path / "project")

    target = persist_registry(registry, registry_dir)

    # The pre-migration renderer, verbatim: indent-2 sorted JSON plus one trailing newline.
    expected = json.dumps(
        {"registry": registry.canonical_document(), "receipt_digest": registry.receipt_digest()},
        sort_keys=True,
        indent=2,
    )
    assert target.read_bytes() == (expected + "\n").encode("utf-8")
    assert not target.is_symlink()


# --- _scheduling_ledger._write_anchor, via RegistryWriter (R12 row 15) --------------------------------


def _writer(project: Path) -> tuple[RegistryWriter, Path]:
    ledger = project / ".trw" / "registry" / LEDGER_FILENAME
    return RegistryWriter(ledger, utc_today=lambda: date(2026, 9, 26)), ledger


def test_scheduling_ledger_anchor_refuses_a_symlinked_target(tmp_path: Path) -> None:
    writer, ledger = _writer(tmp_path / "project")
    link = ledger.parent / ANCHOR_FILENAME
    # The append READS the anchor first (reads are PRD-CORE-316's), so the link names a valid genesis anchor.
    genesis = b'{"head_digest": "genesis", "sequence": 0}\n'
    victim = plant_symlink(link, tmp_path / "outside", genesis)

    with pytest.raises(UnsafeWriteError):
        writer.advance_evaluation_epoch(authorization_receipt="receipt-1", actor="tester")

    assert_untouched(link, victim, genesis)


def test_scheduling_ledger_anchor_keeps_the_pre_migration_bytes(tmp_path: Path) -> None:
    writer, ledger = _writer(tmp_path / "project")

    action = writer.advance_evaluation_epoch(authorization_receipt="receipt-1", actor="tester")

    anchor = ledger.parent / ANCHOR_FILENAME
    expected = json.dumps({"sequence": 1, "head_digest": action_digest(action)}, sort_keys=True) + "\n"
    assert anchor.read_bytes() == expected.encode("utf-8")


# --- RegistryWriter._append_locked, the ledger append itself (CORE-337-D residual) ---------------------


def test_scheduling_ledger_append_refuses_a_symlinked_ledger(tmp_path: Path) -> None:
    writer, ledger = _writer(tmp_path / "project")
    # The append READS the ledger first, so the link names an empty (valid, genesis) ledger outside the project.
    victim = plant_symlink(ledger, tmp_path / "outside", b"")

    with pytest.raises(UnsafeWriteError):
        writer.advance_evaluation_epoch(authorization_receipt="receipt-1", actor="tester")

    assert_untouched(ledger, victim, b"")


def test_scheduling_ledger_append_keeps_the_pre_migration_bytes(tmp_path: Path) -> None:
    writer, ledger = _writer(tmp_path / "project")

    first = writer.advance_evaluation_epoch(authorization_receipt="receipt-1", actor="tester")
    second = writer.advance_evaluation_epoch(authorization_receipt="receipt-2", actor="tester")

    # The pre-migration writer: one sorted-key JSON line per action, appended.
    lines = [json.dumps(a.model_dump(mode="json"), sort_keys=True) + "\n" for a in (first, second)]
    assert ledger.read_bytes() == "".join(lines).encode("utf-8")


# --- claude_md._sync_hash._write_stored_hash, via execute_claude_md_sync (R12 row 17) -----------------


def _sync(project: Path) -> dict[str, object]:
    """``execute_claude_md_sync`` on *project* -- the suite's autouse fixture pins the project root to tmp_path."""
    import trw_mcp.state.claude_md as claude_md
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.claude_md._sync import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    trw_dir = project / ".trw"
    llm = MagicMock()
    llm.available = False
    with (
        patch.object(claude_md, "resolve_trw_dir", return_value=trw_dir),
        patch.object(claude_md, "resolve_project_root", return_value=project),
    ):
        result = execute_claude_md_sync(
            scope="root",
            target_dir=None,
            config=TRWConfig(trw_dir=str(trw_dir)),
            reader=FileStateReader(),
            llm=llm,
        )
    return dict(result)


def _sync_project(project: Path) -> Path:
    trw_dir = project / ".trw"
    for sub in ("learnings/entries", "reflections", "context", "patterns"):
        (trw_dir / sub).mkdir(parents=True, exist_ok=True)
    return trw_dir / "context" / "claude_md_hash.txt"


def test_instruction_sync_hash_refuses_a_symlinked_target(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    project = tmp_path
    link = _sync_project(project)
    victim = plant_symlink(link, tmp_path_factory.mktemp("outside"))

    with pytest.raises(UnsafeWriteError):
        _sync(project)

    assert_untouched(link, victim)


def test_instruction_sync_hash_keeps_the_pre_migration_bytes(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._sync_hash import _compute_sync_hash

    project = tmp_path
    hash_file = _sync_project(project)

    _sync(project)

    # Pre-migration shape: the bare hex digest, no trailing newline; the next sync reads it as a cache hit.
    assert hash_file.read_bytes() == _compute_sync_hash().encode("utf-8")
    assert _sync(project)["status"] == "unchanged"
