"""PRD-CORE-219 FR05 — candidate-ref retention across ALL run layouts.

The ``gc`` subcommand's referenced-set scan (``server/_subcommands.py::_run_gc``)
must find checkpoint files in FLAT and OLD_NESTED run layouts, not only the
PROPER ``{task}/{run_id}/meta`` layout — otherwise a referenced candidate in a
non-PROPER run is wrongly collected after 30 days (documented iter_run_dirs
gotcha). These tests enter through the real subcommand handler.
"""

from __future__ import annotations

import argparse
import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest


def _set_mtime(path: Path, mtime: float) -> None:
    """Force the modification time of *path* to *mtime* seconds since epoch."""
    import os

    os.utime(path, (mtime, mtime))


def _write_aged_candidate_journal(project_root: Path, transaction_id: str, age_days: int) -> None:
    """Persist a collection-eligible candidate journal aged *age_days* old.

    CANDIDATE_ONLY is not a protected state, and no ``candidate_ref`` is set, so
    collection needs no git repo — the only thing that can spare it from the
    30-day sweep is a checkpoint reference.
    """
    from trw_mcp.models.git_commit_transaction import TransactionJournal, TransactionState
    from trw_mcp.state.git_commit_transaction import _journal_path, write_journal

    journal = TransactionJournal(
        transaction_id=transaction_id,
        state=TransactionState.CANDIDATE_ONLY,
    )
    write_journal(project_root, journal)
    # cleanup_candidates uses the journal file mtime as the created-day clock.
    _set_mtime(_journal_path(project_root, transaction_id), time.time() - age_days * 86400)


def _write_flat_layout_checkpoint(runs_root: Path, run_id: str, transaction_id: str) -> None:
    """Write a FLAT-layout run's checkpoint referencing *transaction_id*.

    FLAT layout is ``runs_root/<run_id>/meta/checkpoints.jsonl`` (no ``task``
    parent level), so the old PROPER-only ``*/*/meta`` glob would never scan it.
    """
    meta = runs_root / run_id / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"candidate_evidence": {"transaction_id": transaction_id}})
    (meta / "checkpoints.jsonl").write_text(line + "\n", encoding="utf-8")


def _invoke_gc_json(project_root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run the ``gc`` subcommand (wet, JSON) against *project_root*; return payload."""
    from trw_mcp.server import _subcommands
    from trw_mcp.state import _paths, _pin_store

    monkeypatch.setattr(_paths, "resolve_project_root", lambda: project_root)
    # Defeat the 1s pin-store read cache leaking a prior test's project root.
    monkeypatch.setattr(_pin_store, "_pin_store_cache", None, raising=False)

    buf = io.StringIO()
    args = argparse.Namespace(staleness_hours=None, grace_hours=None, dry_run=False, as_json=True)
    with redirect_stdout(buf), pytest.raises(SystemExit) as exit_info:
        _subcommands._run_gc(args)
    assert exit_info.value.code == 0
    # Cached structlog loggers can prepend log lines; parse the trailing doc.
    out = buf.getvalue()
    idx = out.rindex("\n{") + 1 if "\n{" in out else out.index("{")
    return json.loads(out[idx:])  # type: ignore[no-any-return]


def test_gc_flat_layout_checkpoint_reference_protects_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: a FLAT-layout run's checkpoint reference retains its candidate.

    The referenced-set scan must use ``**/meta/checkpoints.jsonl`` — the old
    PROPER-layout-only ``*/*/meta`` glob missed FLAT/OLD_NESTED runs, so their
    referenced candidates were wrongly collected after 30 days.
    """
    project_root = tmp_path
    (project_root / ".trw" / "runs").mkdir(parents=True, exist_ok=True)
    _write_aged_candidate_journal(project_root, "txn-flat", age_days=40)
    _write_flat_layout_checkpoint(project_root / ".trw" / "runs", "flatrun", "txn-flat")

    payload = _invoke_gc_json(project_root, monkeypatch)

    refs = payload["candidate_refs"]
    assert "txn-flat" in refs["retained"], "FLAT-layout reference must protect the candidate"
    assert "txn-flat" not in refs["collected"]


def test_gc_unreferenced_aged_candidate_is_collected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: the SAME aged candidate collects when nothing references it.

    Proves the retention above is genuinely caused by the checkpoint scan, not
    by the age/eligibility logic failing to fire.
    """
    project_root = tmp_path
    (project_root / ".trw" / "runs").mkdir(parents=True, exist_ok=True)
    _write_aged_candidate_journal(project_root, "txn-orphan", age_days=40)

    payload = _invoke_gc_json(project_root, monkeypatch)

    refs = payload["candidate_refs"]
    assert "txn-orphan" in refs["collected"], "unreferenced aged candidate must collect"
    assert "txn-orphan" not in refs["retained"]
