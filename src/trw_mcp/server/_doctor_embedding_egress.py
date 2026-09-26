"""Embedding cache state + egress posture for ``trw-mcp doctor`` (PRD-SEC-014-FR04).

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling so the
doctor module stays well under the effective-LOC gate.

Runtime model loads never download (PRD-CORE-302 W40): a model reaches the
machine only at install time or through ``trw-mcp models fetch``. The row
therefore reports one fact — whether the configured model is complete in the
local Hugging Face cache — and the fix when it is not.
"""

from __future__ import annotations

__all__ = ["embedding_egress_report"]

_FETCH_COMMAND = "trw-mcp models fetch"


def embedding_egress_report(model: str, *, embeddings_enabled: bool) -> tuple[str, str]:
    """Return ``(status, message)`` for the ``embedding_egress`` doctor row.

    Fail-open: a probe failure is reported as an unknown cache state rather than
    raising, so a cache layout this build does not recognise degrades visibly.
    """
    if not embeddings_enabled:
        return "PASS", "embeddings_enabled=false — no embedding model is loaded."
    try:
        from trw_memory._model_pin import pinned_revision
        from trw_memory.embeddings._hf_cache import CacheState, probe_model_cache

        state = probe_model_cache(model).state
    except Exception as exc:  # justified: diagnostic must never abort the doctor report
        return "WARN", f"model '{model}': cache state unknown ({type(exc).__name__}); fix: {_FETCH_COMMAND}."

    if state is CacheState.COMPLETE:
        status, detail = "PASS", "loaded from the local cache; runtime makes no huggingface.co request."
    else:
        status = "WARN"
        detail = f"not cached, so embeddings are off (runtime never downloads); fix: {_FETCH_COMMAND}."
    if pinned_revision(model) is None:
        # PRD-CORE-302 FR06: an unpinned model loads whatever ``main`` names today.
        status = "WARN"
        detail = f"{detail} Unpinned: loads revision main, so the weights can change under the store."
    return status, f"model '{model}': cache {state.value} — {detail}"
