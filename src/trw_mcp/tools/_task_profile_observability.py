"""Compatibility-safe projection of persisted task-profile observability."""

from __future__ import annotations

from typing_extensions import TypedDict


class TaskProfileObservabilityDict(TypedDict, total=False):
    capability_tier: str
    model_tier: str
    recommended_effort: str
    effort_source: str
    effort_adapter_status: str


def extract_task_profile_observability(task_profile: object) -> TaskProfileObservabilityDict:
    """Return canonical fields while accepting legacy persisted key names."""
    if not isinstance(task_profile, dict):
        return {}
    capability_tier = str(task_profile.get("capability_tier") or task_profile.get("model_tier") or "")
    return {
        "capability_tier": capability_tier,
        "model_tier": capability_tier,
        "recommended_effort": str(
            task_profile.get("recommended_effort") or task_profile.get("reasoning_effort") or "inherit"
        ),
        "effort_source": str(task_profile.get("effort_source") or "harness_default"),
        "effort_adapter_status": str(task_profile.get("effort_adapter_status") or "inherited"),
    }


def apply_task_profile_observability(result: dict[str, object], task_profile: object) -> None:
    """Merge the projection into a heterogeneous tool-result mapping."""
    result.update(extract_task_profile_observability(task_profile))
