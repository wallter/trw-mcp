"""PRD-CORE-205 FR01/FR05/NFR02 — stable reads, scope minting, freshness.

Integration coverage (real filesystem) for the content-binding service.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import EntryState, ReceiptState, ScopeConfidence
from trw_mcp.tools._evidence_binding import (
    StableReadError,
    build_content_binding,
    content_binding_is_current,
    mint_run_owned_scope,
    read_content_entry,
)


def _write_journal(run_path: Path, files: list[str]) -> None:
    meta = run_path / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"ts": f"2026-07-10T00:00:0{i}Z", "event": "file_modified", "file": f}) for i, f in enumerate(files)
    ]
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestStableReads:
    def test_reads_regular_file_bytes(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("hello", encoding="utf-8")
        entry = read_content_entry(tmp_path, "a.py")
        assert entry.state is EntryState.FILE
        assert entry.byte_size == 5
        assert entry.byte_digest is not None and len(entry.byte_digest) == 64
        assert entry.path == "a.py"

    def test_missing_path_is_deleted_state(self, tmp_path: Path) -> None:
        entry = read_content_entry(tmp_path, "nope.py")
        assert entry.state is EntryState.DELETED

    def test_symlink_within_root_binds_target(self, tmp_path: Path) -> None:
        (tmp_path / "real.py").write_text("x", encoding="utf-8")
        (tmp_path / "link.py").symlink_to(tmp_path / "real.py")
        entry = read_content_entry(tmp_path, "link.py")
        assert entry.state is EntryState.SYMLINK
        assert entry.link_target

    def test_symlink_escaping_root_fails(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside_secret.py"
        outside.write_text("secret", encoding="utf-8")
        (tmp_path / "evil.py").symlink_to(outside)
        with pytest.raises(StableReadError) as exc:
            read_content_entry(tmp_path, "evil.py")
        assert exc.value.reason_code in {"symlink_escapes_root", "path_escapes_root"}

    def test_broken_symlink_fails(self, tmp_path: Path) -> None:
        (tmp_path / "dangling.py").symlink_to(tmp_path / "does_not_exist.py")
        with pytest.raises(StableReadError):
            read_content_entry(tmp_path, "dangling.py")


class TestScopeMinting:
    def test_scope_from_journal_paths(self, tmp_path: Path, sample_run_dir: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("a", encoding="utf-8")
        run = project / "run"
        _write_journal(run, [str(project / "src" / "a.py")])
        scope = mint_run_owned_scope(run, project, scope_id="sc1")
        assert scope.confidence is ScopeConfidence.VERIFIED
        assert scope.required_paths == ("src/a.py",)

    def test_no_journal_is_scope_unverifiable(self, tmp_path: Path) -> None:
        scope = mint_run_owned_scope(None, tmp_path, scope_id="sc1")
        assert scope.confidence is ScopeConfidence.UNVERIFIABLE
        assert scope.required_paths == ()

    def test_out_of_root_journal_paths_dropped(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / "a.py").write_text("a", encoding="utf-8")
        run = project / "run"
        _write_journal(run, [str(project / "a.py"), "/etc/passwd", str(tmp_path / "sibling.py")])
        scope = mint_run_owned_scope(run, project, scope_id="sc1")
        assert scope.required_paths == ("a.py",)


class TestServerScopeStableReadAndMutationMatrix:
    """FR01 acceptance artifact."""

    def test_server_scope_stable_read_and_mutation_matrix(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("alpha", encoding="utf-8")
        (project / "src" / "b.py").write_text("beta", encoding="utf-8")
        run = project / "run"
        _write_journal(run, [str(project / "src" / "a.py"), str(project / "src" / "b.py")])

        scope = mint_run_owned_scope(run, project, scope_id="sc1")
        outcome = build_content_binding(scope, project)
        assert outcome.state is ReceiptState.VALID
        binding = outcome.binding
        assert binding is not None

        # Current content matches.
        assert content_binding_is_current(binding, project).state is ReceiptState.VALID

        # One-byte mutation of a bound file -> stale.
        (project / "src" / "a.py").write_text("ALPHA", encoding="utf-8")
        assert content_binding_is_current(binding, project).state is ReceiptState.STALE_CONTENT

        # Restore, then delete a bound path -> stale.
        (project / "src" / "a.py").write_text("alpha", encoding="utf-8")
        assert content_binding_is_current(binding, project).state is ReceiptState.VALID
        os.remove(project / "src" / "b.py")
        assert content_binding_is_current(binding, project).state is ReceiptState.STALE_CONTENT

    def test_unrelated_out_of_scope_change_does_not_invalidate(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        (project / "src").mkdir()
        (project / "src" / "a.py").write_text("alpha", encoding="utf-8")
        run = project / "run"
        _write_journal(run, [str(project / "src" / "a.py")])
        scope = mint_run_owned_scope(run, project, scope_id="sc1")
        binding = build_content_binding(scope, project).binding
        assert binding is not None
        # Another agent writes an unrelated file NOT in the run-owned scope.
        (project / "src" / "unrelated.py").write_text("someone elses work", encoding="utf-8")
        assert content_binding_is_current(binding, project).state is ReceiptState.VALID

    def test_scope_unverifiable_never_hashes_whole_tree(self, tmp_path: Path) -> None:
        scope = mint_run_owned_scope(None, tmp_path, scope_id="sc1")
        outcome = build_content_binding(scope, tmp_path)
        assert outcome.binding is None
        assert outcome.state is ReceiptState.SCOPE_UNVERIFIABLE
