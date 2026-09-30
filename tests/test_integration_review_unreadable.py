"""E2E-INT-REVIEW-FAIL-CLOSED (E2E-INC-111): an integration review that exists but cannot be read blocks.

``integration_review_block`` is a NO_ESCAPE hard gate. Its evidence was read inside a fail-open handler, so a
``verdict: block`` artifact that was torn, re-rooted, mis-indented or replaced by a directory produced no block
and no warning. A genuinely ABSENT artifact still means no integration review was run (unchanged).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import _check_integration_review_gate

_UNREADABLE = "integration review could not be read"


def _run(tmp_path: Path) -> Path:
    run = tmp_path / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True)
    return run


def _artifact(run: Path) -> Path:
    return run / "meta" / "integration-review.yaml"


def test_positive_control_a_readable_block_verdict_blocks(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _artifact(run).write_text("verdict: block\nfindings:\n  - severity: critical\n", encoding="utf-8")

    block, _ = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and "1 critical finding(s)" in block


def test_an_absent_artifact_still_means_no_integration_review(tmp_path: Path) -> None:
    assert _check_integration_review_gate(_run(tmp_path), FileStateReader()) == (None, None)


@pytest.mark.parametrize(
    "body",
    [
        'verdict: block\nfindings:\n  - severity: critical\n    detail: "unterminated\n',
        "- verdict: block\n",
        "verdict: block\nfindings:\n\t- severity: critical\n",
        "just a string\n",
    ],
    ids=["torn", "list-root", "tab-indent", "scalar-root"],
)
def test_an_artifact_that_exists_but_cannot_be_read_blocks_by_name(tmp_path: Path, body: str) -> None:
    run = _run(tmp_path)
    _artifact(run).write_text(body, encoding="utf-8")

    block, warning = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and _UNREADABLE in block and "integration-review.yaml" in block
    assert "trw_review" in block  # the remedy: repair or re-run the review (NO_ESCAPE: no allow_unverified)
    assert warning is None


def test_a_directory_where_the_artifact_belongs_blocks(tmp_path: Path) -> None:
    run = _run(tmp_path)
    _artifact(run).mkdir()

    block, _ = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and _UNREADABLE in block


def test_an_unstatable_artifact_is_unknown_not_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run(tmp_path)
    real_lstat = os.lstat

    def _denied(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if str(path).endswith("integration-review.yaml"):
            raise PermissionError(13, "denied")
        return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "lstat", _denied)

    block, _ = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and _UNREADABLE in block


def test_a_failing_diagnostic_never_leaves_an_unreadable_artifact_unblocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex GATE-MODE r1 lesson: the verdict is set before any fallible diagnostic."""
    from trw_mcp.tools import _delivery_helpers as helpers

    class _RaisingLogger:
        def warning(self, *_a: object, **_k: object) -> None:
            raise OSError("log sink unavailable")

    run = _run(tmp_path)
    _artifact(run).write_text("- verdict: block\n", encoding="utf-8")
    monkeypatch.setattr(helpers, "logger", _RaisingLogger())

    block, _ = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and _UNREADABLE in block


# ── codex r1 KIs: symlinks and explicit null roots ────────────────────────────


@pytest.mark.parametrize("target_body", ["verdict: pass\n", "verdict: block\n"], ids=["to-pass", "to-block"])
def test_a_symlinked_artifact_is_never_read_through(tmp_path: Path, target_body: str) -> None:
    """A link is not the reviewer's artifact: whatever it points at, the verdict is UNKNOWN and blocks."""
    run = _run(tmp_path)
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text(target_body, encoding="utf-8")
    _artifact(run).symlink_to(elsewhere)

    block, _ = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and _UNREADABLE in block


@pytest.mark.parametrize(
    "body", ["null\n", "~\n", "---\n", "--- null\n"], ids=["null", "tilde", "bare-doc", "doc-null"]
)
def test_an_explicit_null_root_blocks(tmp_path: Path, body: str) -> None:
    run = _run(tmp_path)
    _artifact(run).write_text(body, encoding="utf-8")

    block, _ = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and _UNREADABLE in block


@pytest.mark.parametrize("body", ["", "  \n\n\t\n"], ids=["empty", "whitespace"])
def test_an_emptied_artifact_keeps_the_accepted_absence_residual(tmp_path: Path, body: str) -> None:
    """INT-REVIEW-ABSENCE-POLICY (BACKLOG, P3): an emptied artifact reads like an absent one, as a deleted one does."""
    run = _run(tmp_path)
    _artifact(run).write_text(body, encoding="utf-8")

    assert _check_integration_review_gate(run, FileStateReader()) == (None, None)


def test_an_artifact_swapped_between_classification_and_read_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bytes parsed must be the file lstat classified (inode compare on the no-follow descriptor)."""
    run = _run(tmp_path)
    _artifact(run).write_text("verdict: block\nfindings:\n  - severity: critical\n", encoding="utf-8")
    from trw_mcp._checkout_access import open_under as real_open_under

    def _swap_then_open(anchor: Path, relative_path: str) -> int:
        replacement = _artifact(run).with_name("integration-review.yaml.new")
        replacement.write_text("verdict: pass\n", encoding="utf-8")
        os.replace(replacement, _artifact(run))
        return real_open_under(anchor, relative_path)

    monkeypatch.setattr("trw_mcp.state._evidence_bound_read.open_under", _swap_then_open)

    block, _ = _check_integration_review_gate(run, FileStateReader())

    assert block is not None and _UNREADABLE in block
