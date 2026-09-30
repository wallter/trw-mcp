"""Verify and gate spans projected from durable records (PRD-CORE-345 FR02-FR04, NFR02-NFR03).

A span POINTS AT a durable record (a receipt or a deliver outcome) by opaque id and run-relative
path; it is never proof, and no TRW code reads it back. Every function here returns None, builds no
attribute unless the span is recording, and swallows its own faults (``otel_projection_failed``), so
an emitter error can never become a delivery error. No free text, no content, no digest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import structlog
from opentelemetry import trace

logger = structlog.get_logger(__name__)

_tracer = trace.get_tracer("trw_mcp.verify")
_V = "com.trwframework.verification."
_G = "com.trwframework.gate."
RUN_ID = "com.trwframework.run.id"
VERIFY_KEYS = frozenset(
    {
        f"{_V}check.name",
        f"{_V}evidence.id",
        f"{_V}evidence.raw_ref",
        f"{_V}evidence.method",
        f"{_V}evidence.provenance",
        f"{_V}evidence.result",
        f"{_V}evidence.assessed",
        f"{_V}evidence.coverage",
        RUN_ID,
    }
)
GATE_KEYS = frozenset(
    {
        f"{_G}name",
        f"{_G}decision",
        f"{_G}delivery_id",
        f"{_G}policy_ref",
        f"{_G}policy_version_id",
        f"{_G}block_reason",
        f"{_G}evidence_ids",
        f"{_G}evidence_ids_truncated",
        f"{_G}raw_ref",
        f"{_G}outcome_id",
        f"{_G}exception.expires_at",
        f"{_G}verification_status",
        RUN_ID,
    }
)
ARRAY_CAP = 32
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_NAME = re.compile(r"[a-z0-9_.]{1,64}")
#: exit_site -> block_reason (closed; a test fails on an unmapped site). Finer reasons are not derivable.
BLOCK_REASONS: Mapping[str, str] = {
    "no_escape": "policy_unsatisfied",
    "structured": "policy_unsatisfied",
    "build_authority": "evidence_missing",
    "acceptance_integrity": "policy_unsatisfied",
    "plan_acceptance": "policy_unsatisfied",
    "formation": "policy_unsatisfied",
    "requirement_drift": "policy_unsatisfied",
    "advisory": "policy_unsatisfied",
}


def _opaque(value: object) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _start(name: str) -> trace.Span:
    return _tracer.start_span(name, kind=trace.SpanKind.INTERNAL, record_exception=False, set_status_on_exception=False)


def project_receipt(receipt_type: str, model: object, run_path: Path) -> None:
    """One verify span per written receipt (a build receipt: one per command result)."""
    try:
        for check, attrs in _receipt_checks(receipt_type, model):
            span = _start(f"com.trwframework.verify {check}")
            if span.is_recording():
                receipt_id = _opaque(getattr(model, "receipt_id", None))
                span.set_attribute(f"{_V}check.name", check)
                if receipt_id:
                    span.set_attribute(f"{_V}evidence.id", receipt_id)
                    span.set_attribute(f"{_V}evidence.raw_ref", f"meta/receipts/{receipt_type}/{receipt_id}.json")
                if run_id := _opaque(run_path.name):
                    span.set_attribute(RUN_ID, run_id)
                for key, value in attrs.items():
                    span.set_attribute(f"{_V}evidence.{key}", value)
            span.end()
    except Exception:  # justified: fail-open, a projection never touches the host (CONF-04.4)
        logger.debug("otel_projection_failed", kind="receipt", exc_info=True)


def _receipt_checks(receipt_type: str, model: object) -> list[tuple[str, dict[str, str | bool]]]:
    if receipt_type == "build":
        results = getattr(model, "command_results", ()) or ()
        if not results:
            return [("build_check", {"method": "test", "provenance": "self_reported", "result": "not_measured"})]
        checks: list[tuple[str, dict[str, str | bool]]] = []
        for result in results:
            name = str(result.command_id) if _NAME.fullmatch(str(result.command_id)) else "build_check"
            if result.exit_code != 0:
                outcome = "fail"
            elif result.command_class == "test" and not result.test_count:
                outcome = "not_measured"
            else:
                outcome = "pass"
            checks.append((name, {"method": "test", "provenance": "self_reported", "result": outcome}))
        return checks
    if receipt_type == "review":
        verdict = str(getattr(getattr(model, "verdict", ""), "value", getattr(model, "verdict", "")))
        imported = getattr(model, "reviewer_family", "") == "cross_model" and bool(
            getattr(model, "external_receipt_digest", "")
        )
        attrs: dict[str, str | bool] = {
            "method": "review",
            "provenance": "imported" if imported else "self_reported",
            "result": "fail" if verdict == "block" else "pass",
        }
        if imported:
            attrs["assessed"] = True
        return [("review", attrs)]
    if receipt_type == "verification":
        outcome = str(getattr(getattr(model, "outcome", ""), "value", getattr(model, "outcome", "")))
        result = {"pass": "pass", "fail": "fail"}.get(outcome, "not_measured")
        return [("verification", {"method": "test", "provenance": "self_reported", "result": result})]
    return []


def project_outcome(outcome: object, run_path: Path, delivery_id: str | None = None) -> None:
    """The ``com.trwframework.gate deliver`` span for one outcome record (a child of the tool span)."""
    try:
        span = _start("com.trwframework.gate deliver")
        if span.is_recording():
            _gate_attributes(span, outcome, run_path, delivery_id)
        span.end()
    except Exception:  # justified: fail-open, a projection never touches the host (CONF-04.4)
        logger.debug("otel_projection_failed", kind="outcome", exc_info=True)


def _gate_attributes(span: trace.Span, outcome: object, run_path: Path, delivery_id: str | None) -> None:
    decision = str(getattr(outcome, "decision", ""))
    exit_site = str(getattr(outcome, "exit_site", ""))
    outcome_id = _opaque(getattr(outcome, "outcome_id", None))
    span.set_attribute(f"{_G}name", "deliver")
    span.set_attribute(f"{_G}decision", decision)
    span.set_attribute(f"{_G}policy_ref", f"trw.deliver_gate.{getattr(outcome, 'policy_mode', '') or 'default'}")
    version = f"{getattr(outcome, 'framework_version', '')}+{getattr(outcome, 'config_version_id', '')}"
    if _ID.fullmatch(version):
        span.set_attribute(f"{_G}policy_version_id", version)
    if decision == "block":
        reason = BLOCK_REASONS[exit_site]
        if exit_site == "structured" and getattr(outcome, "override_refused", False):
            reason = "invalid_exception"
        span.set_attribute(f"{_G}block_reason", reason)
    receipts: Sequence[str] = [r for r in getattr(outcome, "available_receipt_ids", ()) if _opaque(r)]
    span.set_attribute(f"{_G}evidence_ids", list(receipts[:ARRAY_CAP]))
    span.set_attribute(f"{_G}evidence_ids_truncated", len(receipts) > ARRAY_CAP)
    if outcome_id:
        span.set_attribute(f"{_G}outcome_id", outcome_id)
        span.set_attribute(f"{_G}raw_ref", f"meta/decisions/outcome-{outcome_id}.json")
    if (delivery := _opaque(delivery_id)) is not None:
        span.set_attribute(f"{_G}delivery_id", delivery)
    expires = getattr(outcome, "exception_expires_at", None)
    if decision == "pass_with_exception" and isinstance(expires, str) and _ID.fullmatch(expires):
        span.set_attribute(f"{_G}exception.expires_at", expires)
    if run_id := _opaque(run_path.name):
        span.set_attribute(RUN_ID, run_id)
    span.set_attribute(f"{_G}verification_status", VERIFICATION_STATUS)


#: TRW honesty rule (stricter than Part B): ``verified`` needs every referenced receipt to come from a
#: producer that executes the check itself. No current producer does -- each records what its caller
#: reports -- so every gate span says ``unverified``. Changing this is a PRD change, never a flag.
# trw:intentional hard-coded; a span must never claim verification the evidence does not have
VERIFICATION_STATUS = "unverified"
