"""Pure client-edge mapping for provider-neutral execution-effort advice.

The adapter returns configuration advice only. It never writes client config
and never claims that the parent harness applied the returned value.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from trw_mcp.models.config._model_capabilities import (
    ANTHROPIC_MODEL_CATALOG_VERSION,
    lookup_model_effort_capabilities,
)
from trw_mcp.models.task_profile_types import EffortAdapterStatus, ExecutionEffort

_ORDERED_EFFORTS: tuple[ExecutionEffort, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")
_SAFE_DEFAULT_ADAPTERS: dict[str, tuple[str, frozenset[ExecutionEffort]]] = {
    "codex": ("codex-safe-2026-07-10", frozenset({"minimal", "low", "medium", "high"})),
    "claude-code": ("claude-code-safe-2026-07-10", frozenset({"low", "medium", "high"})),
}


class EffortAdapterDecision(BaseModel):
    """Side-effect-free client mapping decision, not an applied setting."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    adapter_id: str
    recommended_effort: ExecutionEffort
    harness_value: str | None = None
    status: EffortAdapterStatus
    rationale: str


def _closest_supported(
    recommended_effort: ExecutionEffort,
    supported_efforts: frozenset[ExecutionEffort],
) -> ExecutionEffort:
    requested_index = _ORDERED_EFFORTS.index(recommended_effort)
    candidates = [value for value in _ORDERED_EFFORTS if value in supported_efforts]
    return min(
        candidates,
        key=lambda value: (abs(_ORDERED_EFFORTS.index(value) - requested_index), _ORDERED_EFFORTS.index(value)),
    )


def resolve_effort_adapter(
    *,
    client_id: str,
    recommended_effort: ExecutionEffort,
    supported_efforts: frozenset[ExecutionEffort] | None = None,
    active_model: str | None = None,
) -> EffortAdapterDecision:
    """Map or clamp normalized advice using declared client capabilities.

    Capability precedence: an explicit ``supported_efforts`` set (trusted,
    caller-supplied) outranks a trusted ``active_model`` catalog lookup
    (PRD-CORE-209), which outranks the versioned safe default base. A catalog
    entry declaring an empty set resolves to ``unsupported`` — never a clamp.
    """
    if recommended_effort == "inherit":
        return EffortAdapterDecision(
            client_id=client_id,
            adapter_id=f"{client_id}:inherit",
            recommended_effort=recommended_effort,
            status="inherited",
            rationale="TRW leaves the harness default unchanged",
        )

    catalog_efforts = (
        lookup_model_effort_capabilities(active_model)
        if supported_efforts is None and active_model is not None
        else None
    )
    default_adapter = _SAFE_DEFAULT_ADAPTERS.get(client_id)
    if supported_efforts is not None:
        adapter_id = f"{client_id}:explicit"
        declared: frozenset[ExecutionEffort] | None = supported_efforts
    elif catalog_efforts is not None:
        adapter_id = f"{client_id}:{ANTHROPIC_MODEL_CATALOG_VERSION}"
        declared = catalog_efforts
    else:
        adapter_id = default_adapter[0] if default_adapter is not None else f"{client_id}:unsupported"
        declared = default_adapter[1] if default_adapter else None
    capabilities = frozenset(value for value in declared or () if value != "inherit")
    if not capabilities:
        rationale = (
            f"the trusted model catalog declares no effort support for {active_model}"
            if catalog_efforts is not None
            else "no execution-effort adapter capabilities are declared for this client"
        )
        return EffortAdapterDecision(
            client_id=client_id,
            adapter_id=adapter_id,
            recommended_effort=recommended_effort,
            status="unsupported",
            rationale=rationale,
        )

    if recommended_effort in capabilities:
        return EffortAdapterDecision(
            client_id=client_id,
            adapter_id=adapter_id,
            recommended_effort=recommended_effort,
            harness_value=recommended_effort,
            status="mapped",
            rationale="the declared client capability supports the normalized value",
        )

    clamped = _closest_supported(recommended_effort, capabilities)
    return EffortAdapterDecision(
        client_id=client_id,
        adapter_id=adapter_id,
        recommended_effort=recommended_effort,
        harness_value=clamped,
        status="clamped",
        rationale=f"the declared client capability clamps {recommended_effort} to {clamped}",
    )
