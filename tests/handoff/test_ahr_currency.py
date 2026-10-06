"""R-SUP-1..R-SUP-4 in ``trw-mcp handoff check``: supersession across every handoffs directory, forks,
expired and minimal siblings, tier downgrades and duplicate ids."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.handoff._cli_support import _check, _filled_sealed, repo  # noqa: F401
from trw_mcp.handoff import digest, load, seal

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _sibling(prior: Path, name: str, where: Path | None = None, *, supersedes: bool = True, **edits: object) -> Path:
    """A sealed record of the same subject, ``created_at`` half a second later, optionally superseding ``prior``."""
    doc = load(prior)
    doc["handoff_id"] = name
    doc["created_at"] = doc["as_of"]["at"] = doc["created_at"].replace("Z", ".5Z")
    doc["supersedes"] = [{"handoff_id": load(prior)["handoff_id"], "digest": digest(load(prior))}] if supersedes else []
    doc.pop("integrity", None)
    doc |= edits
    out = (where or prior.parent) / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seal(doc)), encoding="utf-8")
    return out


def _standard(capsys: pytest.CaptureFixture[str]) -> Path:
    return _filled_sealed(capsys, "--tier", "standard", "--subject", "swap-refusal", "--next-read", "a.txt")[0]


def test_superseded_then_fork(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _standard(capsys)
    second = _sibling(first, "ho-second")
    code, report = _check(capsys, first)
    sup = report["supersession"]
    assert code == 1 and sup["status"] == "superseded" and sup["superseded_by"] == ["ho-second"]
    assert sup["newest"]["handoff_id"] == "ho-second"
    assert _check(capsys, second)[1]["supersession"]["status"] == "current"
    _sibling(first, "ho-rival", supersedes=False)
    code, report = _check(capsys, second)
    assert code == 1 and report["supersession"]["status"] == "fork"
    assert sorted(report["supersession"]["current"]) == ["ho-rival", "ho-second"]


def test_supersession_is_found_across_handoffs_directories(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The record sits in ``.trw/handoffs``; its successor in a run's ``handoffs`` dir still supersedes it."""
    first = _standard(capsys)
    assert first.parent == repo / ".trw" / "handoffs"
    _sibling(first, "ho-in-run", repo / ".trw" / "runs" / "task" / "r1" / "handoffs")
    code, report = _check(capsys, first)
    assert code == 1 and report["supersession"]["status"] == "superseded"
    assert report["supersession"]["superseded_by"] == ["ho-in-run"]


def test_expired_sibling_is_not_a_head(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _standard(capsys)
    old = _sibling(first, "ho-old", supersedes=False)
    doc = load(old)
    doc |= {"created_at": "2020-01-01T00:00:00Z", "expires_at": "2020-01-08T00:00:00Z"}
    doc["as_of"]["at"] = "2020-01-01T00:00:00Z"
    doc.pop("integrity")
    old.write_text(json.dumps(seal(doc)), encoding="utf-8")
    assert _check(capsys, first)[1]["supersession"]["status"] == "current"


def test_minimal_file_record_never_forms_a_fork(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _standard(capsys)
    note = load(first)
    for key in ("readback", "integrity"):
        note.pop(key)
    note["objective"].pop("intent")
    (first.parent / "ho-note.json").write_text(
        json.dumps(note | {"handoff_id": "ho-note", "tier": "minimal"}), encoding="utf-8"
    )
    assert _check(capsys, first)[1]["supersession"]["status"] == "current"


def test_successor_with_a_lower_tier_is_flagged(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _standard(capsys)
    lower = load(first)
    for key in ("readback", "integrity"):
        lower.pop(key)
    lower["objective"].pop("intent")
    lower |= {"handoff_id": "ho-lower", "tier": "minimal"}
    lower["supersedes"] = [{"handoff_id": load(first)["handoff_id"], "digest": digest(load(first))}]
    (first.parent / "ho-lower.json").write_text(json.dumps(lower), encoding="utf-8")
    code, report = _check(capsys, first)
    assert code == 1
    assert report["supersession"]["tier_downgrade"] == [
        {"handoff_id": "ho-lower", "supersedes": load(first)["handoff_id"]}
    ]


def test_same_id_with_different_content_is_duplicate_id(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    first = _standard(capsys)
    doc = load(first)
    doc["objective"]["goal"] = "A different goal under the same id."
    doc.pop("integrity")
    copy_dir = repo / ".trw" / "runs" / "task" / "r2" / "handoffs"
    copy_dir.mkdir(parents=True)
    (copy_dir / first.name).write_text(json.dumps(seal(doc)), encoding="utf-8")
    code, report = _check(capsys, first)
    assert code == 1 and report["supersession"]["duplicate_id"] == [doc["handoff_id"]]
