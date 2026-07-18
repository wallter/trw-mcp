"""OQ-001 — verifiable reviewer identity on trw_review.

A genuinely independent reviewer (subagent/second session) can record its OWN
run/session identity on the review, but ONLY when that identity is anchored to
framework-recorded state (run.yaml under .trw/runs + the .trw/runtime/pins.json
pin store). A caller-supplied claim that cannot be verified falls back to the
delivering run's identity, so it classifies no better than
``asserted_independent`` — the receipt is never self-mintable (L-ZCyz).
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._review_provenance import (
    RunIdentity,
    build_reviewer_block,
    classify_review_independence,
    resolve_verified_reviewer_identity,
    review_receipt_satisfied,
)

DELIVERING = RunIdentity(run_id="run-deliver", session_id="sess-deliver")


def _make_run(runs_root: Path, writer: FileStateWriter, *, run_id: str, session_id: str = "") -> Path:
    run_dir = runs_root / "some-task" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    payload: dict[str, object] = {"run_id": run_id, "task": "some-task", "status": "active"}
    if session_id:
        payload["owner_session_id"] = session_id
    writer.write_yaml(meta / "run.yaml", payload)
    return run_dir


def _make_pins(tmp_path: Path, entries: dict[str, str]) -> Path:
    pins = tmp_path / "runtime" / "pins.json"
    pins.parent.mkdir(parents=True, exist_ok=True)
    pins.write_text(
        json.dumps({sess: {"run_path": run_path, "pid": 1} for sess, run_path in entries.items()}),
        encoding="utf-8",
    )
    return pins


def _resolve(
    tmp_path: Path,
    *,
    run_id: str | None = None,
    session_id: str | None = None,
    delivering: RunIdentity = DELIVERING,
) -> RunIdentity | None:
    return resolve_verified_reviewer_identity(
        run_id,
        session_id,
        delivering,
        runs_root=tmp_path / "runs",
        pins_path=tmp_path / "runtime" / "pins.json",
    )


# --------------------------------------------------------------------------- #
# resolve_verified_reviewer_identity — anchoring rules
# --------------------------------------------------------------------------- #


def test_verified_distinct_run_id_resolves(tmp_path: Path, writer: FileStateWriter) -> None:
    _make_run(tmp_path / "runs", writer, run_id="run-reviewer", session_id="sess-reviewer")
    identity = _resolve(tmp_path, run_id="run-reviewer")
    assert identity == RunIdentity(run_id="run-reviewer", session_id="sess-reviewer")


def test_unknown_run_id_is_unverifiable(tmp_path: Path, writer: FileStateWriter) -> None:
    _make_run(tmp_path / "runs", writer, run_id="run-reviewer")
    assert _resolve(tmp_path, run_id="run-fabricated") is None


def test_run_id_matching_delivering_run_is_rejected(tmp_path: Path, writer: FileStateWriter) -> None:
    _make_run(tmp_path / "runs", writer, run_id="run-deliver", session_id="sess-deliver")
    assert _resolve(tmp_path, run_id="run-deliver") is None


def test_path_shaped_run_id_claim_is_rejected(tmp_path: Path, writer: FileStateWriter) -> None:
    _make_run(tmp_path / "runs", writer, run_id="run-reviewer")
    assert _resolve(tmp_path, run_id="../runs/some-task/run-reviewer") is None
    assert _resolve(tmp_path, run_id="some-task/run-reviewer") is None


def test_run_yaml_run_id_mismatch_with_dirname_is_rejected(tmp_path: Path, writer: FileStateWriter) -> None:
    run_dir = _make_run(tmp_path / "runs", writer, run_id="run-other")
    # Directory is named run-other but claims resolve by dirname glob; the
    # recorded run_id must equal the claim or verification fails.
    (run_dir / "meta" / "run.yaml").write_text("run_id: something-else\n", encoding="utf-8")
    assert _resolve(tmp_path, run_id="run-other") is None


def test_session_claim_conflicting_with_recorded_session_is_rejected(tmp_path: Path, writer: FileStateWriter) -> None:
    _make_run(tmp_path / "runs", writer, run_id="run-reviewer", session_id="sess-reviewer")
    assert _resolve(tmp_path, run_id="run-reviewer", session_id="sess-imposter") is None


def test_session_only_claim_verifies_via_pin_store(tmp_path: Path, writer: FileStateWriter) -> None:
    reviewer_run = _make_run(tmp_path / "runs", writer, run_id="run-reviewer")
    _make_pins(tmp_path, {"sess-reviewer": str(reviewer_run)})
    identity = _resolve(tmp_path, session_id="sess-reviewer")
    assert identity == RunIdentity(run_id="run-reviewer", session_id="sess-reviewer")


def test_session_only_unregistered_pin_is_unverifiable(tmp_path: Path) -> None:
    _make_pins(tmp_path, {})
    assert _resolve(tmp_path, session_id="sess-nobody") is None


def test_session_only_matching_delivering_session_is_rejected(tmp_path: Path, writer: FileStateWriter) -> None:
    reviewer_run = _make_run(tmp_path / "runs", writer, run_id="run-reviewer")
    _make_pins(tmp_path, {"sess-deliver": str(reviewer_run)})
    assert _resolve(tmp_path, session_id="sess-deliver") is None


def test_session_pin_resolving_to_delivering_run_is_rejected(tmp_path: Path, writer: FileStateWriter) -> None:
    delivering_run = _make_run(tmp_path / "runs", writer, run_id="run-deliver")
    _make_pins(tmp_path, {"sess-second": str(delivering_run)})
    assert _resolve(tmp_path, session_id="sess-second") is None


def test_empty_claims_resolve_to_none(tmp_path: Path) -> None:
    assert _resolve(tmp_path) is None
    assert _resolve(tmp_path, run_id="  ", session_id="") is None


def test_empty_delivering_identity_cannot_verify_difference(tmp_path: Path, writer: FileStateWriter) -> None:
    _make_run(tmp_path / "runs", writer, run_id="run-reviewer")
    assert _resolve(tmp_path, run_id="run-reviewer", delivering=RunIdentity()) is None


# --------------------------------------------------------------------------- #
# build_reviewer_block + classification end-to-end
# --------------------------------------------------------------------------- #


def test_verified_identity_stamps_block_and_classifies_independent(tmp_path: Path, writer: FileStateWriter) -> None:
    _make_run(tmp_path / "runs", writer, run_id="run-reviewer", session_id="sess-reviewer")
    verified = _resolve(tmp_path, run_id="run-reviewer")
    assert verified is not None
    block = build_reviewer_block(None, None, source="subagent", verified_identity=verified)
    assert block["run_id"] == "run-reviewer"
    assert block["session_id"] == "sess-reviewer"
    assert block["identity_verified"] is True
    review_data: dict[str, object] = {"reviewer": block}
    assert classify_review_independence(review_data, DELIVERING) == "independent"
    assert review_receipt_satisfied("P0", review_data, DELIVERING, gate_mode="block") is True


def test_unverified_claim_falls_back_to_asserted_independent(tmp_path: Path, writer: FileStateWriter) -> None:
    delivering_run = _make_run(tmp_path / "runs", writer, run_id="run-deliver", session_id="sess-deliver")
    verified = _resolve(tmp_path, run_id="run-fabricated")
    assert verified is None
    reader = FileStateReader()
    block = build_reviewer_block(delivering_run, reader, source="subagent", verified_identity=verified)
    assert block["run_id"] == "run-deliver"
    assert "identity_verified" not in block
    review_data: dict[str, object] = {"reviewer": block}
    assert classify_review_independence(review_data, DELIVERING) == "asserted_independent"
    assert review_receipt_satisfied("P0", review_data, DELIVERING, gate_mode="block") is False


# --------------------------------------------------------------------------- #
# Production entrypoint — the registered trw_review MCP tool
# --------------------------------------------------------------------------- #


def _bootstrap_project(tmp_path: Path, writer: FileStateWriter) -> tuple[Path, Path]:
    """Create a .trw project with a delivering run and a distinct reviewer run."""
    trw = tmp_path / ".trw"
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "learnings" / "index.yaml").write_text("total_entries: 0\n", encoding="utf-8")
    runs_root = trw / "runs"
    delivering_run = _make_run(runs_root, writer, run_id="run-deliver", session_id="sess-deliver")
    (delivering_run / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    reviewer_run = _make_run(runs_root, writer, run_id="run-reviewer", session_id="sess-reviewer")
    return delivering_run, reviewer_run


def test_trw_review_tool_records_verified_reviewer_identity(
    monkeypatch: object, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
) -> None:
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    from tests._ceremony_helpers import make_ceremony_server

    delivering_run, _ = _bootstrap_project(tmp_path, writer)
    tools = make_ceremony_server(monkeypatch, tmp_path)
    result = tools["trw_review"].fn(
        findings=[{"category": "quality", "severity": "info", "description": "audit finding"}],
        run_path=str(delivering_run),
        reviewer_source="subagent",
        reviewer_run_id="run-reviewer",
    )
    assert result["reviewer_identity_verified"] is True
    review_data = reader.read_yaml(delivering_run / "meta" / "review.yaml")
    assert isinstance(review_data, dict)
    delivering = RunIdentity(run_id="run-deliver", session_id="sess-deliver")
    assert classify_review_independence(review_data, delivering) == "independent"
    assert review_receipt_satisfied("P1", review_data, delivering, gate_mode="block") is True


def test_trw_review_tool_fabricated_identity_stays_asserted(
    monkeypatch: object, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
) -> None:
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    from tests._ceremony_helpers import make_ceremony_server

    delivering_run, _ = _bootstrap_project(tmp_path, writer)
    tools = make_ceremony_server(monkeypatch, tmp_path)
    result = tools["trw_review"].fn(
        findings=[{"category": "quality", "severity": "info", "description": "audit finding"}],
        run_path=str(delivering_run),
        reviewer_source="subagent",
        reviewer_run_id="run-fabricated",
    )
    assert result["reviewer_identity_verified"] is False
    review_data = reader.read_yaml(delivering_run / "meta" / "review.yaml")
    assert isinstance(review_data, dict)
    delivering = RunIdentity(run_id="run-deliver", session_id="sess-deliver")
    assert classify_review_independence(review_data, delivering) == "asserted_independent"
    assert review_receipt_satisfied("P1", review_data, delivering, gate_mode="block") is False
