"""Durable deliver-outcome record (PRD-CORE-345 FR01). Belongs to the ``_deliver_gate_dispatch`` facade.

``evaluate_delivery_gates`` opens an :func:`outcome_scope`, every exit reports its closed-table
``exit_site``, and two existing log points (the hard-block log and the accepted-override audit) note
gate types into a private accumulator. :func:`record_outcome` then writes one ``outcome-<uuid4>.json``.

Audit, never authority: nothing here is read back into a decision, and a write fault is logged
(``deliver_outcome_record_failed``) and swallowed, so a block stays a block and a pass stays a pass.
Raise paths out of the cascade write no record.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.gate_decision import DeliverOutcome

logger = structlog.get_logger(__name__)

#: Every exit of ``evaluate_delivery_gates``; a census test fails on a new unlisted ``return``.
EXIT_SITES: frozenset[str] = frozenset(
    {
        "no_escape",
        "structured",
        "build_authority",
        "acceptance_integrity",
        "plan_acceptance",
        "formation",
        "requirement_drift",
        "advisory",
    }
)
#: Receipt types whose ids are listed as ``available_receipt_ids`` (not "consulted": evaluators do not say).
_RECEIPT_TYPES = ("build", "review")
_MAX_RECEIPT_IDS = 256


@dataclass
class OutcomeAccumulator:
    """Side channel for one evaluation: filled by log points, read only by :func:`record_outcome`."""

    exit_site: str = ""
    blocked_gate_types: list[str] = field(default_factory=list)
    overridden_gate_types: list[str] = field(default_factory=list)
    override_refused: bool = False
    exception_expires_at: str | None = None


_CURRENT: ContextVar[OutcomeAccumulator | None] = ContextVar("trw_deliver_outcome", default=None)


@contextmanager
def outcome_scope() -> Iterator[OutcomeAccumulator]:
    accumulator = OutcomeAccumulator()
    token = _CURRENT.set(accumulator)
    try:
        yield accumulator
    finally:
        _CURRENT.reset(token)


def exit_at(site: str, blocked: bool) -> bool:
    """Name the exit that decided and hand ``blocked`` back unchanged."""
    accumulator = _CURRENT.get()
    if accumulator is not None:
        accumulator.exit_site = site
    return blocked


def note_blocked(gate_type: str, *, refused: bool = False) -> None:
    accumulator = _CURRENT.get()
    if accumulator is not None:
        accumulator.blocked_gate_types.append(gate_type)
        accumulator.override_refused = accumulator.override_refused or refused


def note_overridden(gate_type: str, record: object) -> None:
    accumulator = _CURRENT.get()
    if accumulator is not None:
        accumulator.overridden_gate_types.append(gate_type)
        expiry = record.get("expiry_iso") if isinstance(record, Mapping) else None
        if isinstance(expiry, str) and _iso_date(expiry):
            accumulator.exception_expires_at = expiry


def _iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:  # trw-fail-silent-allow: False IS the answer ("not an ISO date"); the expiry is omitted
        return False
    return True


def record_outcome(
    accumulator: OutcomeAccumulator, blocked: bool, resolved_run: Path | None, trw_dir: Path | None
) -> None:
    """Write the outcome record; never raises and never changes ``blocked``."""
    if resolved_run is None:
        return
    try:
        from trw_mcp._delivery_boundary import journal_step

        with journal_step("S23b"):
            outcome = _build(accumulator, blocked, resolved_run, trw_dir)
            from trw_mcp._checkout_write import write_checkout_file

            path = resolved_run / "meta" / "decisions" / f"outcome-{outcome.outcome_id}.json"
            # Descriptor-anchored under the run dir: a symlinked component is refused (UnsafeWriteError) and lands
            # in the logged branch below, so an unsafe write never changes the gate.
            write_checkout_file(resolved_run, path, outcome.model_dump_json(exclude_none=True) + "\n")
    # trw-fail-silent-allow: audit is not authority; the fault is logged and the gate decision stands (operator rule)
    except Exception as exc:
        logger.warning("deliver_outcome_record_failed", error_type=type(exc).__name__, run=str(resolved_run))
        return
    # PRD-CORE-345 FR03: the gate span points at the record just written (never raises).
    from trw_mcp.telemetry.otel_verify import project_outcome

    project_outcome(outcome, resolved_run)


def _build(accumulator: OutcomeAccumulator, blocked: bool, resolved_run: Path, trw_dir: Path | None) -> DeliverOutcome:
    from trw_mcp.models.config import get_config
    from trw_mcp.models.gate_decision import DeliverOutcome
    from trw_mcp.state._evidence_persistence import list_receipt_ids
    from trw_mcp.state._helpers import read_framework_version

    if blocked:
        decision = "block"
    elif accumulator.overridden_gate_types:
        decision = "pass_with_exception"
    else:
        decision = "pass"
    receipts = [rid for kind in _RECEIPT_TYPES for rid in list_receipt_ids(resolved_run, kind)]
    # model_validate: the strict model rejects an exit site outside the closed table (-> logged, no record)
    return DeliverOutcome.model_validate(
        {
            "outcome_id": uuid.uuid4().hex,
            "run_id": resolved_run.name,
            "decision": decision,
            "exit_site": accumulator.exit_site,
            "blocked_gate_types": tuple(accumulator.blocked_gate_types),
            "overridden_gate_types": tuple(accumulator.overridden_gate_types),
            "override_refused": accumulator.override_refused,
            "available_receipt_ids": tuple(receipts[:_MAX_RECEIPT_IDS]),
            "exception_expires_at": accumulator.exception_expires_at if decision == "pass_with_exception" else None,
            "policy_mode": str(getattr(get_config(), "deliver_gate_mode", "")),
            "framework_version": read_framework_version(),
            "config_version_id": _config_version_id(trw_dir),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _config_version_id(trw_dir: Path | None) -> str:
    """An opaque id from the config file's mtime and size (hashing its content is forbidden)."""
    try:
        st = (Path(trw_dir) / "config.yaml").stat() if trw_dir is not None else None
    except OSError:
        return "cfg-absent"
    return f"cfg-{st.st_mtime_ns}-{st.st_size}" if st is not None else "cfg-absent"
