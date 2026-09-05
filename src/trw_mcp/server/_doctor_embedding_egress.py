"""Embedding cache state + egress posture for ``trw-mcp doctor`` (PRD-SEC-014-FR04).

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling so the
doctor module stays well under the effective-LOC gate.

An operator reasoning about what leaves the machine had two documented surfaces
for the consent flags and none for embedding traffic — which those flags do not
govern at all. This reports the two facts that do govern it: whether the
configured model is already complete in the local Hugging Face cache, and the
resulting posture.
"""

from __future__ import annotations

import os

__all__ = ["embedding_egress_report"]

_OFFLINE_ENV_VARS = ("TRW_OFFLINE", "HF_HUB_OFFLINE")
_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")

_POSTURE_DETAIL = {
    "cache-first": "the complete local snapshot is used and no huggingface.co request is made.",
    "offline-forced": "an uncached model raises LocalOnlyViolationError rather than downloading.",
    "network-capable": "a huggingface.co request may occur on first embed; set TRW_OFFLINE=1 to block it.",
}


def _offline_switch_engaged() -> str:
    """Return the name of an engaged offline switch, or an empty string."""
    return next(
        (name for name in _OFFLINE_ENV_VARS if os.environ.get(name, "").strip().lower() in _TRUTHY_ENV_VALUES),
        "",
    )


def embedding_egress_report(model: str, *, embeddings_enabled: bool) -> tuple[str, str]:
    """Return ``(status, message)`` for the ``embedding_egress`` doctor row.

    Fail-open: any probe failure is reported as an unknown cache state with the
    conservative ``network-capable`` posture rather than raising, so a cache
    layout this build does not recognise degrades visibly instead of silently.
    """
    if not embeddings_enabled:
        return "PASS", "embeddings_enabled=false — no embedding egress is possible."
    try:
        from trw_memory.embeddings._hf_cache import CacheState, probe_model_cache
        from trw_memory.models.config import MemoryConfig

        state = probe_model_cache(model).state
        local_only = bool(MemoryConfig().local_only)
    except Exception as exc:  # justified: diagnostic must never abort the doctor report
        return "WARN", (
            f"model '{model}': cache state unknown ({type(exc).__name__}), posture network-capable — "
            "a huggingface.co request may occur on first embed."
        )

    switch = _offline_switch_engaged()
    if state is CacheState.COMPLETE:
        posture = "cache-first"
    elif switch or local_only:
        posture = "offline-forced"
    else:
        posture = "network-capable"
    detail = _POSTURE_DETAIL[posture]
    if posture == "offline-forced":
        detail = f"downloads are blocked by {switch or 'memory_local_only=True'}; {detail}"
    status = "WARN" if posture == "network-capable" else "PASS"
    return status, f"model '{model}': cache {state.value}, posture {posture} — {detail}"
