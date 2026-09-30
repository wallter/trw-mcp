"""E2E-EVIDENCE-BOUND-READ: the one fail-closed reader behind the safety-critical and integration-review gates."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state._evidence_bound_read import EvidenceUnreadable, read_evidence_mapping

_REL = "meta/evidence.yaml"


def _anchor(tmp_path: Path) -> Path:
    (tmp_path / "meta").mkdir()
    return tmp_path


def _write(anchor: Path, body: str | bytes) -> Path:
    path = anchor / _REL
    if isinstance(body, bytes):
        path.write_bytes(body)
    else:
        path.write_text(body, encoding="utf-8")
    return path


def test_a_mapping_is_returned(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    _write(anchor, "verdict: block\nprd_scope: [PRD-SEC-1]\n")

    assert read_evidence_mapping(anchor, _REL) == {"verdict": "block", "prd_scope": ["PRD-SEC-1"]}


def test_only_a_truly_absent_artifact_is_none(tmp_path: Path) -> None:
    assert read_evidence_mapping(_anchor(tmp_path), _REL) is None


@pytest.mark.parametrize("body", ["", " \n\t\n"], ids=["empty", "whitespace"])
def test_an_emptied_artifact_is_absent_only_when_the_caller_opts_in(tmp_path: Path, body: str) -> None:
    anchor = _anchor(tmp_path)
    _write(anchor, body)

    assert read_evidence_mapping(anchor, _REL, empty_is_absent=True) is None
    with pytest.raises(EvidenceUnreadable):
        read_evidence_mapping(anchor, _REL)


@pytest.mark.parametrize(
    "body",
    ["null\n", "~\n", "---\n", "- a\n", "just text\n", "a: [unclosed\n", b"\xff\xfe not utf-8\n"],
    ids=["null", "tilde", "bare-doc", "list", "scalar", "invalid-yaml", "not-utf8"],
)
def test_anything_but_a_mapping_is_unreadable_even_when_empty_is_absent(tmp_path: Path, body: str | bytes) -> None:
    anchor = _anchor(tmp_path)
    _write(anchor, body)

    with pytest.raises(EvidenceUnreadable):
        read_evidence_mapping(anchor, _REL, empty_is_absent=True)


def test_a_symlink_is_never_followed(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    target = tmp_path / "elsewhere.yaml"
    target.write_text("verdict: pass\n", encoding="utf-8")
    (anchor / _REL).symlink_to(target)

    with pytest.raises(EvidenceUnreadable, match="not a regular file"):
        read_evidence_mapping(anchor, _REL)


def test_a_symlinked_parent_directory_is_never_followed(tmp_path: Path) -> None:
    real = tmp_path / "real-meta"
    real.mkdir()
    (real / "evidence.yaml").write_text("verdict: pass\n", encoding="utf-8")
    (tmp_path / "meta").symlink_to(real)

    with pytest.raises(EvidenceUnreadable):
        read_evidence_mapping(tmp_path, _REL)


def test_a_directory_is_unreadable(tmp_path: Path) -> None:
    anchor = _anchor(tmp_path)
    (anchor / _REL).mkdir()

    with pytest.raises(EvidenceUnreadable, match="not a regular file"):
        read_evidence_mapping(anchor, _REL)


def test_an_unstatable_artifact_is_unreadable_not_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    anchor = _anchor(tmp_path)
    real_lstat = os.lstat

    def _denied(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if str(path).endswith(_REL):
            raise PermissionError(13, "denied")
        return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", _denied)

    with pytest.raises(EvidenceUnreadable, match="cannot be examined"):
        read_evidence_mapping(anchor, _REL)


def test_an_artifact_replaced_after_classification_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp._checkout_access import open_under as real_open_under

    anchor = _anchor(tmp_path)
    path = _write(anchor, "verdict: block\n")

    def _swap_then_open(root: Path, relative: str) -> int:
        replacement = path.with_name("evidence.yaml.new")
        replacement.write_text("verdict: pass\n", encoding="utf-8")
        os.replace(replacement, path)
        return real_open_under(root, relative)

    monkeypatch.setattr("trw_mcp.state._evidence_bound_read.open_under", _swap_then_open)

    with pytest.raises(EvidenceUnreadable, match="could not be read"):
        read_evidence_mapping(anchor, _REL)


def test_a_malformed_relative_path_is_unreadable_not_an_escape(tmp_path: Path) -> None:
    """Codex r1 KI: a ValueError while examining (a NUL in the path) is normalised like any unreadable state."""
    with pytest.raises(EvidenceUnreadable, match="cannot be examined"):
        read_evidence_mapping(_anchor(tmp_path), "meta/evi\x00dence.yaml")


def test_an_unexpected_read_failure_is_unreadable_not_an_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r1 KI: a non-OSError from the bound read (e.g. a platform without os.O_DIRECTORY) must still block."""
    anchor = _anchor(tmp_path)
    _write(anchor, "verdict: block\n")

    def _broken(_root: Path, _relative: str) -> int:
        raise AttributeError("module 'os' has no attribute 'O_DIRECTORY'")

    monkeypatch.setattr("trw_mcp.state._evidence_bound_read.open_under", _broken)

    with pytest.raises(EvidenceUnreadable, match="could not be read"):
        read_evidence_mapping(anchor, _REL)


@pytest.mark.parametrize("gate", ["integration-review", "safety-critical"])
def test_both_gates_block_on_an_unexpected_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gate: str
) -> None:
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._delivery_helpers import _check_integration_review_gate
    from trw_mcp.tools._delivery_safety_critical_gate import _scope_from_run_yaml

    run = _anchor(tmp_path)
    (run / "meta" / "integration-review.yaml").write_text("verdict: pass\n", encoding="utf-8")
    (run / "meta" / "run.yaml").write_text("prd_scope: []\n", encoding="utf-8")

    def _broken(_root: Path, _relative: str) -> int:
        raise AttributeError("simulated platform gap")

    monkeypatch.setattr("trw_mcp.state._evidence_bound_read.open_under", _broken)

    if gate == "integration-review":
        block, _ = _check_integration_review_gate(run, FileStateReader())
        assert block is not None and "could not be read" in block
    else:
        assert _scope_from_run_yaml(run) == ([], ("meta/run.yaml",))
