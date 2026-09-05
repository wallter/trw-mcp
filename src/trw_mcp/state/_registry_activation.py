"""Nested WIP-limit activation gate (PRD-QUAL-121-FR04).

Belongs to the ``state/requirements_registry.py`` facade. Re-exported there
for back-compat — import these names from the facade, not from this module.

The gate is a pure function of an already-reconciled registry: it never reads
the ledger and never writes. ``RegistryWriter.set_execution_state`` is the
sole production caller and holds the cross-process lock around it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from trw_mcp.models.requirements import ExecutionState, RequirementRegistryEntry

if TYPE_CHECKING:
    from trw_mcp.state.requirements_registry import RegistryBuildResult


class ActivationRefusedError(RuntimeError):
    """WIP-limit refusal (PRD-QUAL-121-FR04): carries the occupied slots."""

    def __init__(self, reason: str, occupied_slots: list[str]) -> None:
        super().__init__(reason)
        self.occupied_slots = occupied_slots


@dataclass(slots=True)
class ActivationDecision:
    """Typed WIP-activation outcome — failures name the occupied slots."""

    allowed: bool
    reason: str
    occupied_slots: list[str] = field(default_factory=list)


def evaluate_activation(registry: RegistryBuildResult, prd_id: str) -> ActivationDecision:
    """Check nested WIP limits for activating ``prd_id`` (PRD-QUAL-121-FR04).

    Any status other than ``"ok"`` is an UNKNOWN result and refuses activation.
    That set now includes PRD-CORE-244-FR07's ``"epoch_unset"``: a registry whose
    expiry was never evaluated cannot say whether the WIP slots it is counting
    are still current, so it is fail-closed for activation exactly as
    ``"stale_scheduling_head"`` is — not optimistically permissive.
    """
    if registry.status != "ok":
        return ActivationDecision(False, f"registry unknown: {registry.status}")
    candidate = next((entry for entry in registry.entries if entry.prd_id == prd_id), None)
    if candidate is None:
        return ActivationDecision(False, f"{prd_id} is not in the executable registry")

    limits = registry.limits
    wip_states = {ExecutionState.ACTIVE.value, ExecutionState.BLOCKED_EXTERNAL.value}
    wip = [entry for entry in registry.entries if str(entry.execution_state) in wip_states]

    def _ids(items: list[RequirementRegistryEntry]) -> list[str]:
        return sorted(entry.prd_id for entry in items)

    if str(candidate.execution_state) == ExecutionState.BLOCKED_EXTERNAL.value:
        owner_blocked = [
            entry
            for entry in wip
            if entry.owner == candidate.owner
            and str(entry.execution_state) == ExecutionState.BLOCKED_EXTERNAL.value
            and entry.prd_id != prd_id
        ]
        if len(owner_blocked) >= limits.blocked_external_exception_max:
            return ActivationDecision(
                False,
                f"blocked-external exception limit {limits.blocked_external_exception_max} "
                f"for owner {candidate.owner} is occupied",
                _ids(owner_blocked),
            )

    p0 = [entry for entry in wip if entry.priority == "P0" and entry.prd_id != prd_id]
    p0_p1 = [entry for entry in wip if entry.priority in ("P0", "P1") and entry.prd_id != prd_id]
    checks: list[tuple[bool, list[RequirementRegistryEntry], str, int]] = [
        (candidate.priority == "P0", p0, "global P0 active", limits.global_p0_active_max),
        (candidate.priority in ("P0", "P1"), p0_p1, "global P0/P1 active", limits.global_p0_p1_active_max),
        (
            candidate.priority == "P0",
            [entry for entry in p0 if entry.owner == candidate.owner],
            f"per-owner P0 active ({candidate.owner})",
            limits.per_owner_p0_active_max,
        ),
        (
            candidate.priority in ("P0", "P1"),
            [entry for entry in p0_p1 if entry.owner == candidate.owner],
            f"per-owner P0/P1 active ({candidate.owner})",
            limits.per_owner_p0_p1_active_max,
        ),
    ]
    for applies, occupied, label, maximum in checks:
        if applies and len(occupied) >= maximum:
            return ActivationDecision(False, f"{label} limit {maximum} is occupied", _ids(occupied))
    return ActivationDecision(True, "activation permitted within limits")
