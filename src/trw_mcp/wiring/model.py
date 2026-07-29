"""Typed vocabulary for the observation-based wiring detector (PRD-CORE-232).

The detector answers one question per contract: **did this thing produce
anything?** Everything here exists to make a finding *actionable* — NFR04 says
a finding a reader cannot act on is a false positive by definition, so
``Finding`` structurally refuses to exist without a named contract, both sides
of the edge, and the evidence that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EdgeClass(str, Enum):
    """The seven contract-edge classifications (FR09)."""

    NEVER_FIRED = "NEVER_FIRED"
    CONSUMER_ORPHAN = "CONSUMER_ORPHAN"
    PRODUCER_ORPHAN = "PRODUCER_ORPHAN"
    SCHEMA_DIVERGENCE = "SCHEMA_DIVERGENCE"
    PARTIAL_GUARD = "PARTIAL_GUARD"
    INERT_BRANCH = "INERT_BRANCH"
    PREDICATE_COVERAGE = "PREDICATE_COVERAGE"


class ContractKind(str, Enum):
    """What sort of observable output a registry entry declares."""

    CHANNEL_RENDER = "channel_render"
    MIRROR_PAIR = "mirror_pair"
    SIDECAR = "sidecar"
    EVENT_STREAM = "event_stream"
    GATE_PREDICATE = "gate_predicate"
    CALL_SITE = "call_site"
    DETECTOR_SELF = "detector_self"


class RegistryError(ValueError):
    """Raised when a registry entry is malformed.

    Rejection happens at *construction* time, never at scan time — a contract
    that cannot be checked must fail loudly when it is declared, not silently
    contribute zero findings later (PRD §7 negative test
    ``test_malformed_registry_entry_rejected``).
    """


@dataclass(frozen=True)
class Finding:
    """One classified contract edge.

    Attributes:
        contract_id: stable identifier of the contract, e.g. ``channel:cc-02-...``.
        edge_class: which of the seven signatures fired.
        producer_side: the side that is supposed to write/define. Never empty.
        consumer_side: the side that reads/depends. Never empty.
        evidence: the observation that produced the finding — file paths,
            counts, the exact string searched for. Never empty (NFR04).
        remedy: the concrete next action a reader can take.
    """

    contract_id: str
    edge_class: EdgeClass
    producer_side: str
    consumer_side: str
    evidence: str
    remedy: str

    def __post_init__(self) -> None:
        for field_name in ("contract_id", "producer_side", "consumer_side", "evidence", "remedy"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise RegistryError(
                    f"Finding.{field_name} must be a non-empty string "
                    f"(NFR04: an unactionable finding is a false positive). Got: {value!r}"
                )

    @property
    def key(self) -> str:
        """Stable identity used by the baseline (``EDGE_CLASS::contract_id``)."""
        return f"{self.edge_class.value}::{self.contract_id}"

    def render(self) -> str:
        """One human-readable block naming the contract, both sides, evidence, remedy."""
        return (
            f"[{self.edge_class.value}] {self.contract_id}\n"
            f"    producer: {self.producer_side}\n"
            f"    consumer: {self.consumer_side}\n"
            f"    evidence: {self.evidence}\n"
            f"    remedy:   {self.remedy}"
        )
