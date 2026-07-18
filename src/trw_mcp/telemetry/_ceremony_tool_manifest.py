"""Complete ceremony-tool execution inventory (PRD-CORE-215 FR04).

Belongs to the ``tool_call_timing.py`` facade — the public surface
(``ceremony_tool_disposition`` etc.) is re-exported there per the PRD reference.

The manifest classifies EXACTLY the 16 ceremony tools into a typed execution
disposition, declares each tool's response budget, its request-identity policy,
and the authoritative owner store that dedupes its effect. Invariants:

- ``operation_backed`` requires an owner and is EXACTLY ``trw_deliver`` (owned
  by the PRD-CORE-208 delivery journal); no tool may return a handle without a
  durable owner. ``trw_delivery_status``/``trw_delivery_recover`` only READ from
  that journal (CORE-208 remains their authority), so they are synchronous_only.
- ``synchronous_bounded`` (only ``trw_prd_validate``) returns a visibly-partial
  result at its internal budget instead of continuing past the deadline.
- ``synchronous_only`` tools (the other 14 entries) must commit or return a
  typed rejection before their budget and never continue invisibly.
- Every named tool has one disposition, one budget, one request-identity
  policy, and one owner — enforced at construction.

Lookups FAIL CLOSED: an unregistered tool raises ``UnknownCeremonyToolError``
rather than returning a default disposition, and duplicate registration raises
``DuplicateCeremonyToolError``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

# The execution-disposition vocabulary has ONE canonical definition on the
# typed result envelope (PRD-CORE-215 FR02); the manifest imports it rather than
# maintaining a second enum that could drift.
from trw_mcp.models.config._fields_prd import _PRDFields
from trw_mcp.models.tool_result import CeremonyExecutionClass

# --- Owner store names (authoritative dedupe/effect owner per tool) ----------

# The PRD-CORE-208 delivery journal owns delivery idempotency, lifecycle,
# status, and recovery. The delivery family delegates to it (no second journal).
_CORE_208_DELIVERY_JOURNAL = "PRD-CORE-208 delivery journal"

# --- Response budgets (seconds) ----------------------------------------------
#
# Explicit, documented knobs — never inline magic numbers. Each row references
# one of these named budgets so the classification stays DRY and tunable.

# A read-only bounded lookup that performs no write.
_BUDGET_READ_ONLY_LOOKUP_S = 5.0
# A request-identified synchronous mutation that must commit or return a typed
# rejection before returning (no invisible post-budget continuation).
_BUDGET_SYNC_MUTATION_S = 10.0
# The heavier session-start finalization path (recall + run resolve + health
# probes) which still commits synchronously within one process.
_BUDGET_SESSION_START_S = 30.0
# Budget at which trw_prd_validate returns a visibly-partial result (skipped
# dynamic-check groups) rather than continuing past the deadline. There is ONE
# source of truth for this budget: the ``prd_validate_budget_seconds`` config
# knob, enforced at ``tools/requirements.py`` (see ``prd_quality.py`` where the
# breach raises ``validation_partial``). The manifest reflects that live default
# instead of hardcoding a second, divergent number.
_BUDGET_PRD_VALIDATE_S = float(_PRDFields.model_fields["prd_validate_budget_seconds"].default)
# Budget within which an operation-backed delivery call returns either a fast
# accepted handle or a terminal result (dedupe/lifecycle owned by CORE-208).
_BUDGET_OPERATION_BACKED_S = 10.0


class RequestIdentityPolicy(str, Enum):
    """Whether a tool requires a request identity to dedupe its effect."""

    # Mutating row: request identity is mandatory; an exact retry resolves the
    # prior write and a conflicting input is rejected by the owner store.
    REQUIRED = "required"
    # Read-only row: no mutation, so no dedupe key is required.
    READ_ONLY = "read_only"


class UnknownCeremonyToolError(KeyError):
    """Raised (fail-closed) when a tool is absent from the ceremony manifest."""


class DuplicateCeremonyToolError(ValueError):
    """Raised when a tool name is registered more than once."""


@dataclass(frozen=True)
class CeremonyToolSpec:
    """One immutable ceremony-tool classification row."""

    name: str
    disposition: CeremonyExecutionClass
    budget_seconds: float
    request_identity: RequestIdentityPolicy
    owner: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ceremony tool spec requires a non-empty name")
        if self.budget_seconds <= 0:
            raise ValueError(f"{self.name}: budget_seconds must be positive")
        # Every named tool has exactly one authoritative owner store; an
        # operation-backed tool in particular cannot exist without one (a tool
        # may not return a handle without a durable owner).
        if not self.owner:
            if self.disposition is CeremonyExecutionClass.OPERATION_BACKED:
                raise ValueError(f"{self.name}: operation_backed requires an owner store")
            raise ValueError(f"{self.name}: requires an authoritative owner store")


def build_ceremony_tool_manifest(specs: Sequence[CeremonyToolSpec]) -> Mapping[str, CeremonyToolSpec]:
    """Build an immutable name->spec manifest, rejecting duplicate names."""
    manifest: dict[str, CeremonyToolSpec] = {}
    for spec in specs:
        if spec.name in manifest:
            raise DuplicateCeremonyToolError(spec.name)
        manifest[spec.name] = spec
    return MappingProxyType(manifest)


# The exact 16-tool inventory (PRD-CORE-215 FR04 / §4 Ceremony Operation
# Inventory). Only trw_deliver is operation-backed by the CORE-208 journal;
# trw_prd_validate is synchronous-bounded; the remaining 14 are synchronous-only
# (delivery_status/recover READ CORE-208 but do not return their own handle).
_CEREMONY_TOOL_SPECS: tuple[CeremonyToolSpec, ...] = (
    CeremonyToolSpec(
        "trw_session_start",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SESSION_START_S,
        RequestIdentityPolicy.REQUIRED,
        "ceremony session/run store plus result finalizer",
    ),
    CeremonyToolSpec(
        "trw_status",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_READ_ONLY_LOOKUP_S,
        RequestIdentityPolicy.READ_ONLY,
        "run status projector",
    ),
    CeremonyToolSpec(
        "trw_heartbeat",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "run liveness store",
    ),
    CeremonyToolSpec(
        "trw_adopt_run",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "run ownership store",
    ),
    CeremonyToolSpec(
        "trw_pre_compact_checkpoint",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "run checkpoint store",
    ),
    CeremonyToolSpec(
        "trw_init",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "bootstrap project/run store",
    ),
    CeremonyToolSpec(
        "trw_prd_create",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "PRD store plus registry sync",
    ),
    CeremonyToolSpec(
        "trw_prd_validate",
        CeremonyExecutionClass.SYNCHRONOUS_BOUNDED,
        _BUDGET_PRD_VALIDATE_S,
        RequestIdentityPolicy.READ_ONLY,
        "PRD validator/cache",
    ),
    CeremonyToolSpec(
        "trw_learn",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "learning store",
    ),
    CeremonyToolSpec(
        "trw_learn_update",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "learning store",
    ),
    CeremonyToolSpec(
        "trw_checkpoint",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "run checkpoint store",
    ),
    CeremonyToolSpec(
        "trw_build_check",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "build receipt writer",
    ),
    CeremonyToolSpec(
        "trw_review",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        "review receipt store",
    ),
    CeremonyToolSpec(
        "trw_deliver",
        CeremonyExecutionClass.OPERATION_BACKED,
        _BUDGET_OPERATION_BACKED_S,
        RequestIdentityPolicy.REQUIRED,
        _CORE_208_DELIVERY_JOURNAL,
    ),
    # Delivery status/recover are synchronous_only: they READ the CORE-208
    # delivery journal (which stays their authoritative owner) but return their
    # own bounded result rather than a fresh operation-backed handle. Only
    # trw_deliver is operation_backed. (PRD-CORE-215 FR04 / §4 inventory.)
    CeremonyToolSpec(
        "trw_delivery_status",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_READ_ONLY_LOOKUP_S,
        RequestIdentityPolicy.READ_ONLY,
        _CORE_208_DELIVERY_JOURNAL,
    ),
    CeremonyToolSpec(
        "trw_delivery_recover",
        CeremonyExecutionClass.SYNCHRONOUS_ONLY,
        _BUDGET_SYNC_MUTATION_S,
        RequestIdentityPolicy.REQUIRED,
        _CORE_208_DELIVERY_JOURNAL,
    ),
)

CEREMONY_TOOL_MANIFEST: Mapping[str, CeremonyToolSpec] = build_ceremony_tool_manifest(_CEREMONY_TOOL_SPECS)


def ceremony_tool_spec(name: str) -> CeremonyToolSpec:
    """Return the manifest row for *name*, failing closed on an unknown tool."""
    try:
        return CEREMONY_TOOL_MANIFEST[name]
    except KeyError:
        raise UnknownCeremonyToolError(name) from None


def ceremony_tool_disposition(name: str) -> CeremonyExecutionClass:
    """Return the execution disposition for *name*, failing closed on unknown."""
    return ceremony_tool_spec(name).disposition


def ceremony_tool_names() -> frozenset[str]:
    """Return the exact set of classified ceremony-tool names."""
    return frozenset(CEREMONY_TOOL_MANIFEST)


__all__ = [
    "CEREMONY_TOOL_MANIFEST",
    "CeremonyExecutionClass",
    "CeremonyToolSpec",
    "DuplicateCeremonyToolError",
    "RequestIdentityPolicy",
    "UnknownCeremonyToolError",
    "build_ceremony_tool_manifest",
    "ceremony_tool_disposition",
    "ceremony_tool_names",
    "ceremony_tool_spec",
]
