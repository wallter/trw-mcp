"""CORE-205 evidence binds a repository instance, not its display basename."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ContentBinding, ReceiptState, RunOwnedScope
from trw_mcp.state import _evidence_binding as binding
from trw_mcp.tools._evidence_writers import (
    load_latest_build_evidence,
    parse_build_command_results,
    record_build_receipt,
)

from ._evidence_factories import write_journal


def _mint(root: Path) -> tuple[RunOwnedScope, ContentBinding, Path]:
    root.mkdir(parents=True)
    source = root / "source.txt"
    source.write_bytes(b"identical source bytes")
    run = root / "run"
    write_journal(run, [str(source)])
    scope = binding.mint_run_owned_scope(run, root, scope_id="scope")
    result = binding.build_content_binding(scope, root)
    assert result.state is ReceiptState.VALID
    assert result.binding is not None
    return scope, result.binding, run


def _forbid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_fd: int, _size: int) -> str:
        pytest.fail("repository identity must be checked before reading payload")

    monkeypatch.setattr(binding, "_read_file_digest", forbidden)


@pytest.mark.parametrize("basename", ["repo", "different-name", "Repo"])
def test_cross_root_evidence_rejected_before_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, basename: str
) -> None:
    first = tmp_path / "first" / "repo"
    second = tmp_path / "second" / basename
    scope, recorded, _ = _mint(first)
    _mint(second)
    _forbid_payload(monkeypatch)
    for result in (binding.build_content_binding(scope, second), binding.content_binding_is_current(recorded, second)):
        assert result.state is ReceiptState.INVALID
        assert result.reason_code == "project_identity_mismatch"
        assert result.binding is None


def test_same_root_and_symlink_alias_share_current_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    scope, recorded, _ = _mint(root)
    alias = tmp_path / "root-alias"
    alias.symlink_to(root, target_is_directory=True)
    for supplied in (root, alias):
        assert binding.build_content_binding(scope, supplied).state is ReceiptState.VALID
        assert binding.content_binding_is_current(recorded, supplied).state is ReceiptState.VALID


@pytest.mark.parametrize("change", ["move", "replace"])
def test_root_move_or_replacement_invalidates_recorded_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root = tmp_path / "repo"
    scope, recorded, _ = _mint(root)
    moved = tmp_path / "moved"
    root.rename(moved)
    if change == "replace":
        shutil.copytree(moved, root)
        supplied = root
    else:
        supplied = moved
    _forbid_payload(monkeypatch)
    for result in (
        binding.build_content_binding(scope, supplied),
        binding.content_binding_is_current(recorded, supplied),
    ):
        assert result.state is ReceiptState.INVALID
        assert result.reason_code == "project_identity_mismatch"


@pytest.mark.parametrize(
    ("identity", "state"),
    [
        ("repo", ReceiptState.LEGACY_UNBOUND),
        ("", ReceiptState.INVALID),
        ("root-v2:sha256:" + "a" * 64, ReceiptState.INVALID),
        ("root-v1:sha256:not-a-digest", ReceiptState.INVALID),
    ],
)
def test_legacy_and_malformed_identity_remain_parseable_but_nonpositive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: str, state: ReceiptState
) -> None:
    root = tmp_path / "repo"
    _, recorded, _ = _mint(root)
    payload = recorded.model_dump()
    payload["project_identity"] = identity
    legacy = ContentBinding(**payload)
    assert ContentBinding.model_validate_json(legacy.model_dump_json()) == legacy
    _forbid_payload(monkeypatch)
    assert binding.content_binding_is_current(legacy, root).state is state


@pytest.mark.parametrize("consumer", ["build", "freshness"])
def test_root_swap_after_final_file_read_invalidates_complete_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    root = tmp_path / "repo"
    scope, recorded, _ = _mint(root)
    original = binding._read_entries_with_budget

    def swap_after_reads(*args: object, **kwargs: object) -> object:
        entries = original(*args, **kwargs)  # type: ignore[arg-type]
        moved = tmp_path / "old-root"
        root.rename(moved)
        shutil.copytree(moved, root)
        return entries

    monkeypatch.setattr(binding, "_read_entries_with_budget", swap_after_reads)
    result = (
        binding.build_content_binding(scope, root)
        if consumer == "build"
        else binding.content_binding_is_current(recorded, root)
    )
    assert result.state is ReceiptState.UNSTABLE_READ
    assert result.reason_code == "project_identity_changed"
    assert result.binding is None


def test_persisted_server_build_receipt_cannot_be_replayed_in_same_named_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "first" / "repo"
    _, _, run = _mint(root)
    commands = parse_build_command_results(
        [
            {"command_id": "tests", "label": "pytest", "command_class": "test", "exit_code": 0},
            {"command_id": "static_checks", "label": "ruff", "command_class": "static", "exit_code": 0},
        ]
    )
    assert commands is not None
    outcome = record_build_receipt(
        run,
        root,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="full",
        coverage_pct=None,
        policy_mode="enforce",
        command_results=commands,
    )
    assert outcome is not None and outcome.ok
    state, receipt = load_latest_build_evidence(run, root)
    assert state.state is ReceiptState.VALID
    assert receipt is not None
    other = tmp_path / "second" / "repo"
    shutil.copytree(root, other)
    _forbid_payload(monkeypatch)
    state, _ = load_latest_build_evidence(other / "run", other)
    assert state.state is ReceiptState.INVALID
    assert state.reason_code == "project_identity_mismatch"


def test_missing_root_is_unavailable_not_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    scope, recorded, _ = _mint(root)
    root.rename(tmp_path / "moved")
    _forbid_payload(monkeypatch)
    for result in (binding.build_content_binding(scope, root), binding.content_binding_is_current(recorded, root)):
        assert result.state is ReceiptState.DEGRADED
        assert result.reason_code == "project_identity_unavailable"


def test_unobservable_root_cannot_mint_verified_scope(tmp_path: Path) -> None:
    from trw_mcp.models._evidence_core import ScopeConfidence

    scope = binding.mint_run_owned_scope(None, tmp_path / "missing", scope_id="scope")
    assert scope.confidence is ScopeConfidence.UNVERIFIABLE
    assert not scope.project_identity


def test_identity_capability_absence_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state._evidence_identity import ProjectIdentityError, resolve_project_identity

    monkeypatch.delattr(os, "O_DIRECTORY")
    with pytest.raises(ProjectIdentityError, match="project_identity_unavailable"):
        resolve_project_identity(tmp_path)


def test_identity_root_swap_during_open_rejected_and_descriptor_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state._evidence_identity import ProjectIdentityError, resolve_project_identity

    root = tmp_path / "root"
    root.mkdir()
    original_open = os.open
    captured: list[int] = []

    def swap_after_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
        descriptor = original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]
        if Path(path) == root:
            captured.append(descriptor)
            root.rename(tmp_path / "old-root")
            root.mkdir()
        return descriptor

    monkeypatch.setattr(os, "open", swap_after_open)
    with pytest.raises(ProjectIdentityError, match="project_identity_changed"):
        resolve_project_identity(root)
    assert len(captured) == 1
    with pytest.raises(OSError):
        os.fstat(captured[0])
