"""CORE-205 FR01: authoritative scopes bind journal path identity, not link targets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import EntryState, ReceiptState, ScopeConfidence
from trw_mcp.state._evidence_binding import (
    build_content_binding,
    content_binding_is_current,
    mint_run_owned_scope,
)


def _journal(root: Path, paths: list[str]) -> Path:
    run = root / "run"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "events.jsonl").write_text(
        "".join(json.dumps({"event": "file_modified", "file": path}) + "\n" for path in paths),
        encoding="utf-8",
    )
    return run


@pytest.mark.parametrize("journal_absolute", [False, True])
def test_journal_link_identity_survives_mint_and_retarget_invalidates(tmp_path: Path, journal_absolute: bool) -> None:
    (tmp_path / "a").write_bytes(b"same target bytes")
    (tmp_path / "b").write_bytes(b"same target bytes")
    link = tmp_path / "link"
    link.symlink_to("a")
    run = _journal(tmp_path, [str(link) if journal_absolute else "link"])
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope")
    assert scope.confidence is ScopeConfidence.VERIFIED
    assert scope.required_paths == ("link",)
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert outcome.binding.entries[0].state is EntryState.SYMLINK
    assert outcome.binding.entries[0].path == "link"
    assert outcome.binding.entries[0].link_target == "a"
    assert content_binding_is_current(outcome.binding, tmp_path).state is ReceiptState.VALID
    link.unlink()
    link.symlink_to("b")
    assert content_binding_is_current(outcome.binding, tmp_path).state is ReceiptState.STALE_CONTENT


@pytest.mark.parametrize("kind", ["broken", "escaping", "cyclic"])
def test_in_root_link_name_is_retained_even_when_target_is_unsafe(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"not in project")
    target = {"broken": "missing", "escaping": str(outside), "cyclic": "link"}[kind]
    (root / "link").symlink_to(target)
    run = _journal(root, [str(root / "link")])
    scope = mint_run_owned_scope(run, root, scope_id="scope")
    assert scope.required_paths == ("link",), "unsafe targets cannot erase changed journal paths"
    outcome = build_content_binding(scope, root)
    assert outcome.state is not ReceiptState.VALID
    assert outcome.binding is None


def test_deleted_link_name_remains_authoritative(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"target")
    link = tmp_path / "link"
    link.symlink_to("a")
    run = _journal(tmp_path, [str(link)])
    link.unlink()
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope")
    assert scope.required_paths == ("link",)
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert outcome.binding.entries[0].state is EntryState.DELETED
    link.symlink_to("a")
    assert content_binding_is_current(outcome.binding, tmp_path).state is ReceiptState.STALE_CONTENT


def test_parent_alias_file_path_is_preserved_and_retarget_detected(tmp_path: Path) -> None:
    for directory, content in (("first", b"first bytes"), ("other", b"other bytes")):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "file").write_bytes(content)
    alias = tmp_path / "alias"
    alias.symlink_to("first", target_is_directory=True)
    run = _journal(tmp_path, [str(alias / "file")])
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope")
    assert scope.required_paths == ("alias/file",)
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert outcome.binding.entries[0].path == "alias/file"
    alias.unlink()
    alias.symlink_to("other", target_is_directory=True)
    assert content_binding_is_current(outcome.binding, tmp_path).state is ReceiptState.STALE_CONTENT


@pytest.mark.parametrize("operator_absolute", [False, True])
def test_operator_paths_store_normalized_relative_identity(tmp_path: Path, operator_absolute: bool) -> None:
    (tmp_path / "target").write_bytes(b"target")
    (tmp_path / "link").symlink_to("target")
    run = _journal(tmp_path, [])
    path = str(tmp_path / "link") if operator_absolute else "link"
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope", operator_paths=(path,))
    assert scope.required_paths == ("link",)
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert outcome.binding.entries[0].state is EntryState.SYMLINK


def test_operator_dot_prefix_is_normalized_without_following_link(tmp_path: Path) -> None:
    (tmp_path / "target").write_bytes(b"target")
    (tmp_path / "link").symlink_to("target")
    run = _journal(tmp_path, [])
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope", operator_paths=("./link",))
    assert scope.confidence is ScopeConfidence.VERIFIED
    assert scope.required_paths == ("link",)
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert outcome.binding.entries[0].state is EntryState.SYMLINK


@pytest.mark.parametrize("source", ["journal", "operator"])
def test_relative_traversal_reentry_is_unverifiable_not_ignored(tmp_path: Path, source: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "valid").write_bytes(b"valid")
    (root / "target").write_bytes(b"target")
    (root / "link").symlink_to("target")
    ambiguous = "../project/link"
    run = _journal(root, ["valid", ambiguous] if source == "journal" else ["valid"])
    operator = (ambiguous,) if source == "operator" else ()
    scope = mint_run_owned_scope(run, root, scope_id="scope", operator_paths=operator)
    assert scope.confidence is ScopeConfidence.UNVERIFIABLE
    outcome = build_content_binding(scope, root)
    assert outcome.state is ReceiptState.SCOPE_UNVERIFIABLE
    assert outcome.binding is None


def test_proposed_links_add_identity_without_replacing_required_paths(tmp_path: Path) -> None:
    (tmp_path / "required").write_bytes(b"required")
    (tmp_path / "target").write_bytes(b"target")
    (tmp_path / "proposed").symlink_to("target")
    run = _journal(tmp_path, [str(tmp_path / "required")])
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope", proposed_paths=(str(tmp_path / "proposed"),))
    assert scope.required_paths == ("required",)
    assert scope.proposed_paths == ("proposed",)
    assert scope.effective_paths == ("proposed", "required")
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert {entry.path for entry in outcome.binding.entries} == {"required", "proposed"}


@pytest.mark.parametrize("journal_root", ["canonical", "provided_alias"])
def test_absolute_journal_paths_support_canonical_and_provided_root_alias(tmp_path: Path, journal_root: str) -> None:
    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "project-alias"
    alias.symlink_to(root, target_is_directory=True)
    (root / "target").write_bytes(b"target")
    (root / "link").symlink_to("target")
    recorded_root = root.resolve() if journal_root == "canonical" else alias
    run = _journal(root, [str(recorded_root / "link")])
    scope = mint_run_owned_scope(run, alias, scope_id="scope")
    assert scope.required_paths == ("link",)
    outcome = build_content_binding(scope, alias)
    assert outcome.state is ReceiptState.VALID
    assert outcome.binding is not None
    assert outcome.binding.entries[0].state is EntryState.SYMLINK
    assert content_binding_is_current(outcome.binding, alias).state is ReceiptState.VALID


@pytest.mark.parametrize(
    "bad_record",
    [
        "{malformed json",
        "[]",
        '{"event": "file_modified"}',
        '{"event": "file_modified", "file": 123}',
        '{"event": "file_modified", "file": ""}',
        '{"event": "file_modified", "file": "a/../escape"}',
        '{"event": "file_modified", "data": {"file": null}}',
    ],
)
def test_malformed_required_journal_record_cannot_yield_partial_positive_scope(tmp_path: Path, bad_record: str) -> None:
    (tmp_path / "valid").write_bytes(b"valid source")
    run = _journal(tmp_path, ["valid"])
    journal = run / "meta" / "events.jsonl"
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(bad_record + "\n")
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope")
    assert scope.confidence is ScopeConfidence.UNVERIFIABLE
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.SCOPE_UNVERIFIABLE
    assert outcome.binding is None


def test_unrelated_normal_events_do_not_invalidate_complete_scope(tmp_path: Path) -> None:
    (tmp_path / "valid").write_bytes(b"valid source")
    run = _journal(tmp_path, ["valid"])
    journal = run / "meta" / "events.jsonl"
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "tool_call", "data": {"tool_name": "trw_recall"}}) + "\n")
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope")
    assert scope.confidence is ScopeConfidence.VERIFIED
    assert scope.required_paths == ("valid",)
    assert build_content_binding(scope, tmp_path).state is ReceiptState.VALID


@pytest.mark.parametrize("source", ["journal", "operator"])
@pytest.mark.parametrize(
    "malformed_path",
    ["back\\slash", "null\x00byte", "empty//segment", ".", "./.", "x" * 1025, "\ud800"],
    ids=["backslash", "nul", "empty-segment", "dot-only", "multiple-dots", "overlong", "surrogate"],
)
def test_malformed_required_path_is_unverifiable_without_crashing(
    tmp_path: Path, source: str, malformed_path: str
) -> None:
    (tmp_path / "valid").write_bytes(b"valid source")
    paths = ["valid", malformed_path] if source == "journal" else ["valid"]
    run = _journal(tmp_path, paths)
    operators = (malformed_path,) if source == "operator" else ()
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope", operator_paths=operators)
    assert scope.confidence is ScopeConfidence.UNVERIFIABLE
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.SCOPE_UNVERIFIABLE
    assert outcome.binding is None


def test_invalid_utf8_journal_is_unverifiable_without_partial_positive_scope(tmp_path: Path) -> None:
    (tmp_path / "valid").write_bytes(b"valid source")
    run = _journal(tmp_path, ["valid"])
    journal = run / "meta" / "events.jsonl"
    journal.write_bytes(journal.read_bytes() + b"\xff\n")
    scope = mint_run_owned_scope(run, tmp_path, scope_id="scope")
    assert scope.confidence is ScopeConfidence.UNVERIFIABLE
    outcome = build_content_binding(scope, tmp_path)
    assert outcome.state is ReceiptState.SCOPE_UNVERIFIABLE
    assert outcome.binding is None
