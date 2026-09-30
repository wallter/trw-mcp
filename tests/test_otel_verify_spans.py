"""Verify and gate spans point at durable records and are never evidence (PRD-CORE-345 FR02-FR04, NFR01-03)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests._otel_support import assert_keys_registered, assert_no_canary, assert_no_unkeyed_digest
from tests.test_deliver_outcome_record import _EXITS, _ORACLE, _evaluate, _observed, _records, _run_dir

CANARY = "CANARY-a11e-gate-text"


def _spans(exporter: InMemorySpanExporter, prefix: str) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name.startswith(prefix)]


# --- FR03: gate span per outcome record ---


@pytest.mark.parametrize("exit_name", sorted(_EXITS))
def test_gate_span_mirrors_the_outcome_record(
    exit_name: str, otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from trw_mcp.telemetry.otel_verify import GATE_KEYS

    run = _run_dir(tmp_path)
    with trace.get_tracer("t").start_as_current_span("tools/call trw_deliver") as parent:
        _evaluate(exit_name, monkeypatch, run, tmp_path / ".trw")
    (record,) = _records(run)
    (span,) = _spans(otel_spans, "com.trwframework.gate")
    attrs = dict(span.attributes or {})
    assert span.name == "com.trwframework.gate deliver"
    assert span.parent is not None and span.parent.span_id == parent.get_span_context().span_id
    assert attrs["com.trwframework.gate.decision"] == record["decision"]
    assert attrs["com.trwframework.gate.outcome_id"] == record["outcome_id"]
    assert attrs["com.trwframework.gate.raw_ref"] == f"meta/decisions/outcome-{record['outcome_id']}.json"
    assert attrs["com.trwframework.gate.verification_status"] == "unverified"
    assert ("com.trwframework.gate.block_reason" in attrs) is (record["decision"] == "block")
    assert_keys_registered([span], GATE_KEYS)
    assert_no_unkeyed_digest([span])
    assert span.events == ()


def test_block_reasons_come_from_the_closed_table(
    otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from trw_mcp.telemetry.otel_verify import BLOCK_REASONS
    from trw_mcp.tools._deliver_outcome import EXIT_SITES

    assert set(BLOCK_REASONS) == EXIT_SITES  # an unmapped exit site fails here
    reasons = {}
    for name in ("build_authority", "structured", "structured_refused", "formation"):
        run = _run_dir(tmp_path / name)
        otel_spans.clear()
        _evaluate(name, monkeypatch, run, tmp_path / ".trw")
        (span,) = _spans(otel_spans, "com.trwframework.gate")
        reasons[name] = span.attributes["com.trwframework.gate.block_reason"]
    assert reasons == {
        "build_authority": "evidence_missing",
        "structured": "policy_unsatisfied",
        "structured_refused": "invalid_exception",
        "formation": "policy_unsatisfied",
    }


def test_exception_expiry_only_and_no_free_text(
    otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tests.test_deliver_outcome_record as t

    record = json.dumps(
        {"failed_command": CANARY, "residual_risk": CANARY, "owner": CANARY, "expiry_iso": "2099-01-01"}
    )
    monkeypatch.setitem(t._EXITS, "structured_overridden", ({"review_block": CANARY}, None, True, record))
    run = _run_dir(tmp_path)
    _evaluate("structured_overridden", monkeypatch, run, tmp_path / ".trw")
    (span,) = _spans(otel_spans, "com.trwframework.gate")
    assert span.attributes["com.trwframework.gate.exception.expires_at"] == "2099-01-01"
    assert_no_canary(list(otel_spans.get_finished_spans()), CANARY)
    assert (
        CANARY
        not in (run / "meta" / "decisions")
        .joinpath(next(p.name for p in (run / "meta" / "decisions").glob("outcome-*.json")))
        .read_text()
    )


def test_evidence_ids_are_capped_at_32(
    otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _run_dir(tmp_path)
    receipts = run / "meta" / "receipts" / "build"
    receipts.mkdir(parents=True)
    for i in range(40):
        (receipts / f"br-{i:02d}.json").write_text("{}")
    _evaluate("advisory_clean", monkeypatch, run, tmp_path / ".trw")
    (span,) = _spans(otel_spans, "com.trwframework.gate")
    assert len(span.attributes["com.trwframework.gate.evidence_ids"]) == 32
    assert span.attributes["com.trwframework.gate.evidence_ids_truncated"] is True


def test_verification_status_is_hard_coded_unverified() -> None:
    from trw_mcp.telemetry import otel_verify

    assert otel_verify.VERIFICATION_STATUS == "unverified"
    source = Path(otel_verify.__file__).read_text(encoding="utf-8")
    assert source.count('"verified"') == 0  # no code path can emit it


# --- NFR01: decisions identical with no provider, with one, and with a raising projector ---


@pytest.mark.parametrize("exit_name", sorted(_EXITS))
def test_a_raising_projector_changes_nothing(
    exit_name: str, otel_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from trw_mcp.telemetry import otel_verify

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("tracer broken")

    monkeypatch.setattr(otel_verify, "_start", boom)
    run = _run_dir(tmp_path)
    assert _observed(*_evaluate(exit_name, monkeypatch, run, tmp_path / ".trw"), exit_name) == _ORACLE[exit_name]
    assert len(_records(run)) == 1  # the record is written independently of the span
    assert _spans(otel_spans, "com.trwframework.gate") == []


# --- FR02: verify span at the receipt write point ---


def _build_receipt(command_results: tuple[Any, ...]) -> Any:
    return SimpleNamespace(receipt_id="br-abc123", command_results=command_results)


def test_verify_spans_per_build_command_and_none_for_idempotent_writes(
    otel_spans: InMemorySpanExporter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state import _evidence_persistence as ep
    from trw_mcp.telemetry.otel_verify import VERIFY_KEYS

    monkeypatch.setattr(ep, "canonical_receipt_bytes", lambda _m: b'{"x":1}')
    results = (
        SimpleNamespace(command_id="tests", command_class="test", exit_code=0, test_count=12),
        SimpleNamespace(command_id="static_checks", command_class="static", exit_code=1, test_count=None),
    )
    receipt = _build_receipt(results)
    run = _run_dir(tmp_path)
    assert ep.write_receipt(run, "build", "br-abc123", cast("Any", receipt)).reason_code == "written"
    assert ep.write_receipt(run, "build", "br-abc123", cast("Any", receipt)).reason_code == "idempotent"
    spans = _spans(otel_spans, "com.trwframework.verify")
    assert [s.name for s in spans] == ["com.trwframework.verify tests", "com.trwframework.verify static_checks"]
    assert [s.attributes["com.trwframework.verification.evidence.result"] for s in spans] == ["pass", "fail"]
    for span in spans:
        assert span.attributes["com.trwframework.verification.evidence.provenance"] == "self_reported"
        assert span.attributes["com.trwframework.verification.evidence.id"] == "br-abc123"
        assert span.attributes["com.trwframework.verification.evidence.raw_ref"] == "meta/receipts/build/br-abc123.json"
    assert_keys_registered(spans, VERIFY_KEYS)
    assert_no_unkeyed_digest(spans)


@pytest.mark.parametrize(
    ("family", "digest", "verdict", "provenance", "result"),
    [
        ("cross_model", "a" * 64, "block", "imported", "fail"),
        ("cross_model", "", "pass", "self_reported", "pass"),
        ("subagent", "", "warn", "self_reported", "pass"),
    ],
)
def test_review_verify_span(
    otel_spans: InMemorySpanExporter, family: str, digest: str, verdict: str, provenance: str, result: str
) -> None:
    from trw_mcp.telemetry.otel_verify import project_receipt

    model = SimpleNamespace(
        receipt_id="rr-1",
        reviewer_family=family,
        external_receipt_digest=digest,
        verdict=SimpleNamespace(value=verdict),
    )
    project_receipt("review", model, Path("run-1"))
    (span,) = _spans(otel_spans, "com.trwframework.verify")
    attrs = dict(span.attributes or {})
    assert (
        attrs["com.trwframework.verification.evidence.provenance"],
        attrs["com.trwframework.verification.evidence.result"],
    ) == (provenance, result)
    assert ("com.trwframework.verification.evidence.assessed" in attrs) is (provenance == "imported")
    assert_no_unkeyed_digest([span])  # the external digest never reaches the span


def test_build_with_no_command_results_is_not_measured(otel_spans: InMemorySpanExporter) -> None:
    from trw_mcp.telemetry.otel_verify import project_receipt

    project_receipt("build", _build_receipt(()), Path("run-1"))
    (span,) = _spans(otel_spans, "com.trwframework.verify")
    assert span.name == "com.trwframework.verify build_check"
    assert span.attributes["com.trwframework.verification.evidence.result"] == "not_measured"


# --- NFR03: spans are not evidence ---


def test_gate_and_evidence_modules_never_import_opentelemetry() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    targets = [*src.glob("tools/_deliver*.py"), *src.glob("state/_evidence*.py"), src / "models" / "gate_decision.py"]
    offenders = []
    for path in targets:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.startswith("opentelemetry") for n in names):
                offenders.append(path.name)
    assert targets and offenders == []


def test_no_caller_uses_a_projector_return_value() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    used = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "project_receipt(" not in text and "project_outcome(" not in text:
            continue
        tree = ast.parse(text)
        statement_calls = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("project_receipt", "project_outcome"):
                if id(node) not in statement_calls:
                    used.append(path.name)
    assert used == []
