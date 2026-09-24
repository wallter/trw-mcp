"""Pre-journal preflight for ``execute_learn``: dep resolution + accept gates.

Belongs to the ``_learn_impl.py`` facade. Everything in this module runs BEFORE
the write-ahead journal record is written, and that boundary is exactly what
makes it one cohesive slice: a learning may only be journaled (and therefore
crash-replayed) once every gate here has ACCEPTED it. Extracted so
``_learn_impl`` stays under the 350 effective-LOC gate.

Both helpers deliberately keep their default/collaborator imports function-local
so they resolve off the SOURCE module at call time — suites that patch
``trw_mcp.state.analytics.*``, ``trw_mcp.state.memory_adapter.*``,
or ``trw_mcp.clients.llm.LLMClient`` keep
seeing their patch through this indirection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from trw_mcp.tools._learn_metadata import _LearnLogger
from trw_mcp.tools._learn_side_effects import _content_policy_reject
from trw_mcp.tools._learning_helpers import is_noise_summary

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import LearnResultDict


@dataclass(frozen=True)
class LearnDeps:
    """Collaborators the learn path calls: injected test seams or defaults.

    justified Any: these mirror ``execute_learn``'s optional test-seam
    overrides for heterogeneous functions (store_learning, generate_learning_id,
    save_learning_entry, update_analytics, list_active_learnings,
    check_and_handle_dedup) whose return values feed typed downstream calls.
    Narrowing to ``Callable[..., object]`` does not type the *results*, so it
    forces ~10 ``cast`` calls at every use site for zero added safety; the real
    contract is enforced by the concrete default each field falls back to.
    """

    store: Any
    generate_id: Any
    save_entry: Any
    update_analytics: Any
    list_active: Any
    dedup: Any


def resolve_learn_deps(
    adapter_store: Any,
    generate_learning_id: Any,
    save_learning_entry: Any,
    update_analytics: Any,
    list_active_learnings: Any,
    check_and_handle_dedup: Any,
) -> LearnDeps:
    """Return each injected dep, falling back to its production default."""
    from trw_mcp.state.analytics import generate_learning_id as _default_gen_id
    from trw_mcp.state.analytics import save_learning_entry as _default_save
    from trw_mcp.state.analytics import update_analytics as _default_update_a
    from trw_mcp.state.memory_adapter import list_active_learnings as _default_list
    from trw_mcp.state.memory_adapter import store_learning as _default_store
    from trw_mcp.tools._learning_helpers import check_and_handle_dedup as _default_dedup

    return LearnDeps(
        store=adapter_store or _default_store,
        generate_id=generate_learning_id or _default_gen_id,
        save_entry=save_learning_entry or _default_save,
        update_analytics=update_analytics or _default_update_a,
        list_active=list_active_learnings or _default_list,
        dedup=check_and_handle_dedup or _default_dedup,
    )


def run_accept_gates(
    summary: str,
    detail: str,
    log: _LearnLogger,
) -> LearnResultDict | None:
    """Run every write-time acceptance gate; return a rejection or ``None``.

    Gate order is load-bearing: empty-content check, deterministic noise filter, then the
    content policy. A non-``None`` result is
    terminal — the caller must return it WITHOUT journaling, because a rejected
    learning must never be durably recorded or replayed.
    """
    # Empty capture is not durable knowledge. Either field alone remains valid;
    # do not impose a minimum length or a new utility heuristic.
    if not summary.strip() and not detail.strip():
        return {
            "status": "rejected",
            "reason": "empty_content",
            "message": "Provide a nonempty summary or detail; empty learning was not persisted.",
        }

    # PRD-QUAL-032-FR09: Reject auto-generated noise entries early
    if is_noise_summary(summary):
        return {
            "status": "rejected",
            "reason": "noise_filter",
            "message": f"Summary matches noise pattern — not persisted: {summary[:60]}",
        }

    # Security audit 2026-04-18 H2: content policy (length caps + injection
    # patterns). Protects the stored-prompt-injection surface since recalled
    # learnings are surfaced verbatim to future agents via trw_session_start,
    # trw_recall, and the trw://learnings/summary resource.
    policy_reject = _content_policy_reject(summary, detail)
    if policy_reject is not None:
        log.warning(
            "learn_content_policy_rejected",
            reason=policy_reject["reason"],
            summary_preview=summary[:60],
        )
        return cast("LearnResultDict", policy_reject)

    return None


__all__ = ["LearnDeps", "resolve_learn_deps", "run_accept_gates"]
