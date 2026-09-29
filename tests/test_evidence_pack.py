"""PRD-CORE-323 slice S1 -- ``trw-mcp run evidence-pack`` (FR01, FR03, FR05, FR06, NFR01-NFR03).

Every behavioural test drives the verb through the real argparse tree and the
``SUBCOMMAND_HANDLERS`` dispatcher on a fixture run built with the production
writers (``FileEventLogger``, ``record_build_receipt``, ``record_review_receipt``)
and asserts on the exit code and the pack bytes. Per the PRD's failing-first rule,
nothing here imports ``trw_mcp.evidence_pack`` at module scope: before the package
existed every test failed on argparse's usage exit 2, never on an ImportError.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._evidence_pack_fixture import (
    PLANTED_EMAIL,
    PLANTED_KEY,
    TRW_MCP_SRC,
    TRW_MEMORY_SRC,
    build_fixture,
    canonical,
    clone_build_receipts,
    dispatch,
    export,
    load_pack,
    receipt_block,
    receipt_item,
    sealed_entries,
    snapshot,
)
from tests._stdio_harness import pinned_server_env
from tests._timing import assert_budget

pytestmark = pytest.mark.integration

_FORBIDDEN = re.compile(r"\b(compliant|conformant|certified|audit-ready)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# FR05 -- the failing-first test for slice S1
# ---------------------------------------------------------------------------


def test_cli_pack_is_byte_identical_and_names_fixture_ids(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    first, second = tmp_path / "out-1.json", tmp_path / "out-2.json"

    assert export(fx, first) == 0
    assert export(fx, second) == 0

    data = first.read_bytes()
    assert data == second.read_bytes()
    text = data.decode("utf-8")
    for known in ("PRD-CORE-901", "PRD-CORE-902", fx.positive_build, fx.negative_build, fx.review):
        assert known in text, known


# ---------------------------------------------------------------------------
# FR01 -- requirements section
# ---------------------------------------------------------------------------


def test_cli_pack_lists_scoped_prd_ids(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path, scope_extra=("PRD-CORE-999",))
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    entries = load_pack(out)["sections"]["requirements"]["entries"]  # type: ignore[index]
    by_id = {entry.get("prd_id"): entry for entry in entries}
    for prd_id, rel in (
        ("PRD-CORE-901", "docs/requirements-aare-f/prds/PRD-CORE-901.md"),
        ("PRD-CORE-902", "docs/requirements-aare-f/prds/PRD-CORE-902-second.md"),
    ):
        entry = by_id[prd_id]
        assert entry["label"] == "observed"
        assert entry["as_of"] == "export_snapshot"
        assert entry["source"] == {"path": rel}
        assert entry["file_digest_basis"] == "raw_bytes"
        assert entry["file_sha256"] == hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
        assert entry["verification_mappings"][0]["requirement_id"] == f"{prd_id}-FR01"
        assert entry["matrix_rows"][0]["requirement"] == "FR01"
        assert entry["call_chains"]["FR01"] == {
            "chain": ["cli:trw-mcp run evidence-pack", "trw_mcp.evidence_pack.build_pack"],
            "malformed": False,
        }
    (unresolved,) = [entry for entry in entries if entry["label"] == "unknown"]
    assert unresolved["reason"] == "prd_not_found"
    assert unresolved["scope_entry"] == "PRD-CORE-999"
    assert len(entries) == 3


# ---------------------------------------------------------------------------
# FR03 -- evidence section and the shared classifier
# ---------------------------------------------------------------------------


def test_cli_pack_marks_edited_binding_not_positive(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert export(fx, before) == 0
    (tmp_path / "src" / "feature.py").write_text("EDITED AFTER THE RECEIPT\n", encoding="utf-8")
    assert export(fx, after) == 0

    first, second = load_pack(before), load_pack(after)
    pos_before, pos_after = (
        receipt_item(first, "build", fx.positive_build),
        receipt_item(second, "build", fx.positive_build),
    )
    assert pos_before["at_export"]["positivity_at_export"] is True
    assert pos_after["at_export"]["positivity_at_export"] is False
    assert pos_after["at_export"]["derivation"] == "recomputed_at_export"
    assert pos_after["at_export"]["tree_state"] == "dirty_or_unknown"
    assert pos_after["at_export"]["export_head"] is None
    # The run_record half did not move: same receipt bytes, same digest.
    assert pos_after["record"] == pos_before["record"]
    raw = (fx.run / "meta" / "receipts" / "build" / f"{fx.positive_build}.json").read_bytes()
    assert pos_after["record"]["file_sha256"] == hashlib.sha256(raw).hexdigest()

    assert receipt_item(second, "build", fx.negative_build)["at_export"]["positivity_at_export"] is False
    corrupt = receipt_item(second, "build", fx.corrupt_build)["record"]
    assert (corrupt["label"], corrupt["reason"]) == ("unknown", "receipt_unparsable")
    assert receipt_block(second, "build")["found"] == "3 build receipts found"
    assert receipt_block(second, "verification") == {
        "found": "0 verification receipts found",
        "items": [],
        "kept": 0,
        "total": 0,
    }
    evidence = second["sections"]["evidence"]  # type: ignore[index]
    capability = evidence["capability_integration"]
    assert (capability["label"], capability["reason"]) == ("unknown", "not_persisted_by_deliver")
    for name in ("decisions", "verdict"):
        assert second["sections"][name]["section"] == name  # type: ignore[index]  (built since S2)


@pytest.mark.parametrize("edited", [False, True], ids=["current", "edited-binding"])
def test_receipt_classifier_matches_the_trust_gate(tmp_path: Path, edited: bool) -> None:
    """Parity: the extracted classifier and the trust gate's loop agree receipt by receipt."""
    from trw_mcp.models._evidence_plans import RequiredValidationPlan
    from trw_mcp.models._evidence_records import BuildReceipt
    from trw_mcp.state._evidence_gates import validate_build_receipt
    from trw_mcp.state._trust_receipts import classify_receipt, collect_positive_trust_evidence

    fx = build_fixture(tmp_path)
    if edited:
        (tmp_path / "src" / "feature.py").write_text("EDITED\n", encoding="utf-8")
    kinds, contributing = collect_positive_trust_evidence(fx.run, tmp_path)
    counted = {receipt_id for receipt_id, _digest in contributing}
    builds = fx.run / "meta" / "receipts" / "build"

    for receipt_id in (fx.positive_build, fx.negative_build, fx.corrupt_build):
        raw = (builds / f"{receipt_id}.json").read_bytes()
        verdict = classify_receipt(fx.run, "build", raw, tmp_path)
        assert verdict.positive == (receipt_id in counted), receipt_id
        if verdict.receipt is not None:
            assert isinstance(verdict.receipt, BuildReceipt)
            plan_path = fx.run / "meta" / "plans" / "validation" / f"{verdict.receipt.plan_id}.json"
            plan = RequiredValidationPlan.model_validate_json(plan_path.read_bytes())
            assert verdict.positive == validate_build_receipt(verdict.receipt, plan, tmp_path).is_positive
    assert kinds == (set() if edited else {"build"})
    corrupt = classify_receipt(fx.run, "build", b"{not json", tmp_path)
    assert (corrupt.positive, corrupt.reason) == (False, "receipt_unparsable")
    review = classify_receipt(fx.run, "review", b"{}", tmp_path)
    assert (review.positive, review.reason) == (False, "kind_not_trust_eligible")


# ---------------------------------------------------------------------------
# FR06 -- redaction before digest and write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("project_under_home", [False, True], ids=["plain-root", "root-under-HOME"])
def test_cli_pack_redacts_review_receipt_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_under_home: bool
) -> None:
    if project_under_home:
        # redact_secrets rewrites $HOME before redact_paths runs; the path must still become <project>.
        monkeypatch.setenv("HOME", str(tmp_path.parent))
    planted_path = str(tmp_path / "src" / "feature.py")
    fx = build_fixture(tmp_path, review_reason=f"key {PLANTED_KEY} mail {PLANTED_EMAIL} at {planted_path} end")
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    data = out.read_bytes()
    for planted in (PLANTED_KEY, PLANTED_EMAIL, str(tmp_path)):
        assert planted.encode() not in data, planted
    record = receipt_item(load_pack(out), "review", fx.review)["record"]
    assert record["label"] == "redacted"
    assert record["redactor"] == "redact_secrets+redact_paths"
    assert "<REDACTED:api_key>" in record["degraded_reason"]
    assert "<project>/src/feature.py" in record["degraded_reason"]
    raw = (fx.run / "meta" / "receipts" / "review" / f"{fx.review}.json").read_bytes()
    assert record["file_digest_basis"] == "redacted_text"
    assert record["file_sha256"] != hashlib.sha256(raw).hexdigest()
    # Every entry's digest is over its own in-pack (redacted) bytes, never over source text.
    entries = sealed_entries(load_pack(out))
    assert len(entries) >= 8
    for entry in entries:
        body = {key: value for key, value in entry.items() if key != "sha256"}
        assert entry["sha256"] == hashlib.sha256(canonical(body)).hexdigest()


def test_cli_pack_redacts_before_truncating_a_long_field(tmp_path: Path) -> None:
    """A key straddling character 4,096 is redacted whole; truncating first would leave half a key."""
    lead = "| FR02 | "
    filler = "x " * ((4070 - len(lead)) // 2)
    row = f"{lead}{filler}{PLANTED_KEY} " + "y " * 3000 + "|"
    assert len(row) > 9_000 and row.index(PLANTED_KEY) < 4096 < row.index(PLANTED_KEY) + len(PLANTED_KEY)
    fx = build_fixture(tmp_path, matrix_extra=row + "\n")
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    data = out.read_bytes()
    assert PLANTED_KEY[:12].encode() not in data
    entry = next(e for e in load_pack(out)["sections"]["requirements"]["entries"] if e.get("prd_id") == "PRD-CORE-901")  # type: ignore[index]
    long_row = entry["matrix_rows"][1]["row"]
    assert len(long_row) == 4096
    assert "<REDACTED:api_key>" in long_row
    assert entry["truncated"] == {"matrix_rows[1].row": len(row) - len(PLANTED_KEY) + len("<REDACTED:api_key>")}
    assert entry["label"] == "redacted"
    assert entry["file_digest_basis"] == "redacted_text"


# ---------------------------------------------------------------------------
# FR05 -- header, help text, wording, containment
# ---------------------------------------------------------------------------

_LIMIT_LINES = (
    "Evidence pack for readiness review. Not a conformance, certification or compliance claim.",
    "Redaction is pattern-based; not exhaustive.",
    "Verification shows integrity since export; not authorship or truth.",
)


def test_cli_pack_header_and_help_carry_the_limit_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fx = build_fixture(tmp_path)
    out = tmp_path / "pack.json"
    assert export(fx, out) == 0
    stdout = capsys.readouterr().out
    assert dispatch(["run", "evidence-pack", "--help"]) == 0
    help_text = capsys.readouterr().out

    header = load_pack(out)["header"]
    assert isinstance(header, dict)
    assert (header["posture"], header["redaction_limit"], header["verify_limit"]) == _LIMIT_LINES
    assert header["schema"] == "trw.evidence_pack.v1"
    assert header["run_identity"] == ".trw/runs/task/run-1"
    assert header["head_commit"] is None
    assert set(header) == {
        "schema",
        "run_identity",
        "head_commit",
        "trw_mcp_version",
        "posture",
        "redaction_limit",
        "verify_limit",
    }
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", json.dumps(header))
    for line in _LIMIT_LINES:
        assert line in help_text
    for text in (out.read_text(encoding="utf-8"), help_text, stdout):
        assert not _FORBIDDEN.search(text), _FORBIDDEN.search(text)
        assert not re.search(r"\bverified\b", text, re.IGNORECASE)


@pytest.mark.parametrize(
    ("where", "reason"),
    [("outside", "run_path_outside_project"), ("not-a-run", "run_path_not_a_run")],
)
def test_cli_pack_refuses_a_bad_run_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], where: str, reason: str
) -> None:
    target = tmp_path.parent / f"outside-{tmp_path.name}" if where == "outside" else tmp_path / "docs"
    (target / "meta").mkdir(parents=True, exist_ok=where == "outside")
    if where == "not-a-run":
        (target / "meta").rmdir()
    out = tmp_path / "pack.json"

    assert dispatch(["run", "evidence-pack", str(target), "--out", str(out)]) == 2

    assert f"refused: {reason}" in capsys.readouterr().err
    assert not out.exists()


# ---------------------------------------------------------------------------
# NFR01 / NFR02 -- receipt budget, receipt cap, size refusal
# ---------------------------------------------------------------------------


def test_cli_pack_produces_the_expected_receipt_count(tmp_path: Path) -> None:
    """Correctness half (unmarked, gating): the speed half below only measures time."""
    fx = build_fixture(tmp_path)
    clone_build_receipts(fx, 196)  # 3 build + 1 review + 196 = 200 receipts
    out = tmp_path / "pack.json"
    assert export(fx, out) == 0
    assert receipt_block(load_pack(out), "build")["total"] == 199


@pytest.mark.requires_local_timing
def test_cli_pack_within_budget(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    clone_build_receipts(fx, 196)  # 3 build + 1 review + 196 = 200 receipts
    out = tmp_path / "pack.json"
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        if export(fx, out) != 0:
            pytest.fail("export() returned nonzero mid-timing loop")
        timings.append(time.perf_counter() - started)
    assert_budget("evidence_pack_export_200_receipts", min(timings), 10.0, "s")


def test_cli_pack_caps_receipts_per_type(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    clone_build_receipts(fx, 1001)
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    block = receipt_block(load_pack(out), "build")
    assert (block["kept"], block["total"]) == (1000, 1004)
    assert len(block["items"]) == 1000
    assert block["found"] == "1004 build receipts found"


def test_cli_pack_refuses_oversized_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Each row is under the 4,096-character field cap, but JSON escapes every backslash, so
    # ~5.2 MB of PRD text renders past the 8 MiB pack cap.
    cell = "\\\\ " * 1330  # 3,990 characters, 2,660 of them backslashes
    rows = "".join(f"| FR{index:04d} | {cell} |\n" for index in range(1300))
    fx = build_fixture(tmp_path, matrix_extra=rows)
    out = tmp_path / "pack.json"

    assert export(fx, out) == 2

    assert "refused: pack_too_large" in capsys.readouterr().err
    assert not out.exists()
    assert not list(tmp_path.glob(".pack.json.*"))


# ---------------------------------------------------------------------------
# NFR03 -- offline, reads only, writes only the output
# ---------------------------------------------------------------------------


def test_cli_pack_writes_only_the_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import sqlite3

    import trw_mcp.state.validation.call_chain as call_chain

    fx = build_fixture(tmp_path)
    out = tmp_path / "pack.json"

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the export must not open sockets, databases or a probe server")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(sqlite3, "connect", _refuse)
    monkeypatch.setattr(call_chain, "registered_tool_sites", _refuse)
    before = snapshot(tmp_path, out)

    assert export(fx, out) == 0

    assert snapshot(tmp_path, out) == before
    assert out.is_file()
    assert not (tmp_path / ".trw" / "memory").exists()


def test_cli_pack_subprocess_matches_in_process_bytes(tmp_path: Path) -> None:
    """The installed entry point (``python -m trw_mcp.server``) writes the same bytes, and only them."""
    fx = build_fixture(tmp_path)
    in_process = tmp_path / "in-process.json"
    assert export(fx, in_process) == 0
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([TRW_MCP_SRC, TRW_MEMORY_SRC])
    env["TRW_PROJECT_ROOT"] = str(tmp_path)
    outputs = []
    for name in ("sub-1.json", "sub-2.json"):
        target = tmp_path / name
        before = snapshot(tmp_path, target)
        result = subprocess.run(
            [sys.executable, "-m", "trw_mcp.server", "run", "evidence-pack", str(fx.run), "--out", str(target)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env=pinned_server_env(env),
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert snapshot(tmp_path, target) == before
        outputs.append(target.read_bytes())
    assert outputs[0] == outputs[1] == in_process.read_bytes()


# ---------------------------------------------------------------------------
# FR04 -- the failing-first test for slice S2
# ---------------------------------------------------------------------------


def test_cli_pack_journal_projection_ignores_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-terminal journal operation exports byte-identically across a clock change, and is listed."""
    from tests._evidence_pack_s2_fixture import journal_operation, section

    fx = build_fixture(tmp_path)
    operation_id = journal_operation(tmp_path)  # claimed, one step started: not terminal
    first, second = tmp_path / "t0.json", tmp_path / "t1.json"

    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    monkeypatch.setattr(time, "time_ns", lambda: int(base * 1e9))
    assert export(fx, first) == 0
    # Past the lease and the stale-lease window: a clock-reading projection would flip lease_current.
    later = base + 3 * 24 * 3600
    monkeypatch.setattr(time, "time", lambda: later)
    monkeypatch.setattr(time, "time_ns", lambda: int(later * 1e9))
    assert export(fx, second) == 0

    assert first.read_bytes() == second.read_bytes()
    verdict = section(load_pack(first), "verdict")
    assert operation_id in json.dumps(verdict)
    for clock_field in ("lease_current", "recovery_eligible", "lease_owner", "capability_hash"):
        assert clock_field not in json.dumps(verdict), clock_field


# ---------------------------------------------------------------------------
# FR02 -- decisions section
# ---------------------------------------------------------------------------


def test_cli_pack_counts_each_event_once(tmp_path: Path) -> None:
    """Checkpoints from checkpoints.jsonl only, selected events once each, the dated mirror never read."""
    from tests._evidence_pack_s2_fixture import checkpoint, log_event, section

    fx = build_fixture(tmp_path)  # already logged one file_modified event
    log_event(fx, "run_init", {"task": "task"})
    checkpoint(fx, "first checkpoint")
    log_event(fx, "phase_enter", {"phase": "implement"})
    for tool in ("trw_learn", "trw_recall", "trw_learn"):
        log_event(fx, "tool_call", {"tool_name": tool, "success": True})
    log_event(fx, "decision", {"question_ids": ["q1"], "backend": "fixture"})
    checkpoint(fx, "second checkpoint")
    log_event(fx, "delivery_gate_overridden", {"gate_type": "build_gate", "block": "no build"})
    meta = fx.run / "meta"
    assert list(meta.glob("events-*.jsonl")), "the real logger writes the dated mirror"
    assert (meta / "checkpoints.jsonl").is_file()
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    decisions = section(load_pack(out), "decisions")
    checkpoints = decisions["checkpoints"]["entries"]  # type: ignore[index]
    assert [entry["message"] for entry in checkpoints] == ["first checkpoint", "second checkpoint"]
    assert all("state" not in entry for entry in checkpoints)
    events = decisions["events"]  # type: ignore[index]
    assert [entry["event"] for entry in events["entries"]] == [
        "run_init",
        "phase_enter",
        "decision",
        "delivery_gate_overridden",
    ]
    lines = [entry["source"]["line"] for entry in events["entries"]]
    assert lines == sorted(lines) and len(set(lines)) == 4
    assert (events["total"], events["kept"]) == (4, 4)
    tools = decisions["tool_calls"]  # type: ignore[index]
    assert tools["tool_call_counts"] == {"trw_learn": 2, "trw_recall": 1}
    assert (tools["trw_learn_calls"], tools["learning_linkage"]) == (2, "not_recorded")
    assert tools["not_copied_event_counts"] == {"checkpoint": 2, "file_modified": 1}
    prd_decisions = decisions["prd_decisions"]  # type: ignore[index]
    assert [(entry["source"]["path"].rsplit("/", 1)[-1], entry["heading"]) for entry in prd_decisions] == [
        ("PRD-CORE-901.md", "Decisions"),
        ("PRD-CORE-902-second.md", "Decisions"),
    ]
    assert prd_decisions[0]["text"] == "- D1: keep it small."
    assert prd_decisions[0]["as_of"] == "export_snapshot"
    assert not (tmp_path / ".trw" / "memory").exists()
