"""Compatibility-safe projection of persisted task-profile observability."""

from __future__ import annotations

from typing_extensions import TypedDict


class TaskProfileObservabilityDict(TypedDict, total=False):
    capability_tier: str
    recommended_effort: str
    effort_source: str
    effort_adapter_status: str


def extract_task_profile_observability(task_profile: object) -> TaskProfileObservabilityDict:
    """Return canonical fields while accepting legacy persisted key names.

    The legacy ``model_tier`` key is still read on INPUT, because run state
    persisted before the rename still carries it. It is no longer echoed on
    OUTPUT: it was a byte-for-byte duplicate of ``capability_tier`` shipped on
    every trw_init, trw_status, and trw_session_start response, and no caller
    or downstream tool ever read it. The asymmetry is deliberate — accept the
    old name, emit only the canonical one.
    """
    if not isinstance(task_profile, dict):
        return {}
    capability_tier = str(task_profile.get("capability_tier") or task_profile.get("model_tier") or "")
    return {
        "capability_tier": capability_tier,
        "recommended_effort": str(
            task_profile.get("recommended_effort") or task_profile.get("reasoning_effort") or "inherit"
        ),
        "effort_source": str(task_profile.get("effort_source") or "harness_default"),
        "effort_adapter_status": str(task_profile.get("effort_adapter_status") or "inherited"),
    }


def apply_task_profile_observability(result: dict[str, object], task_profile: object) -> None:
    """Merge the projection into a heterogeneous tool-result mapping."""
    result.update(extract_task_profile_observability(task_profile))
