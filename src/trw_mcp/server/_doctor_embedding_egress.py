"""Embedding cache state + egress posture for ``trw-mcp doctor`` (PRD-SEC-014-FR04).

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling so the
doctor module stays well under the effective-LOC gate.

Runtime model loads never download (PRD-CORE-302 W40): a model reaches the
machine only at install time or through ``trw-mcp models fetch``. The row
therefore reports one fact — whether the configured model is complete in the
local Hugging Face cache — and the fix when it is not.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["embedding_egress_report"]

_FETCH_COMMAND = "trw-mcp models fetch"


def _daemon_embedder(trw_dir: Path | None) -> dict[str, object] | None:
    """The memory daemon's ``embedder`` block (reading it never loads the model); ``None`` when it could not be asked."""
    if trw_dir is None:
        return None
    from trw_mcp.state._store_selection import StoreUnavailableError, measuring_only, selected_store

    try:
        with measuring_only():
            store, namespace = selected_store(trw_dir)
        return dict(store.embedder_status(namespace))
    except (StoreUnavailableError, ValueError):  # trw-fail-silent-allow: None is "not asked", and the row says so
        return None


def embedding_egress_report(model: str, *, embeddings_enabled: bool, trw_dir: Path | None = None) -> tuple[str, str]:
    """Return ``(status, message)`` for the ``embedding_egress`` doctor row.

    Fail-open: a probe failure is reported as an unknown cache state rather than
    raising, so a cache layout this build does not recognise degrades visibly.

    ``embeddings_enabled=false`` is THIS project's flag; the memory daemon is one process per user serving every
    project and reads its own ``MEMORY_EMBEDDINGS_ENABLED`` (UF-MEM-04), so with the flag off the row asks the daemon
    (*trw_dir* names the checkout to ask through) rather than claiming no model is loaded.
    """
    if not embeddings_enabled:
        embedder = _daemon_embedder(trw_dir)
        if embedder is None and trw_dir is not None:
            return "PASS", (
                "embeddings_enabled=false in this project; the memory daemon could not be asked whether it has "
                "loaded an embedding model."
            )
        if embedder and (embedder.get("loaded") or embedder.get("available")):
            return "WARN", (
                "embeddings_enabled=false in this project's config, but the memory daemon (one process serving every "
                f"project of this user) still has its embedding model ({'loaded' if embedder.get('loaded') else 'available'}). "
                "The daemon-wide switch is MEMORY_EMBEDDINGS_ENABLED=false in the daemon's environment, then restart it."
            )
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
