"""PRD-CORE-253 FR01 — the moved-checkout observation at ``trw_session_start``.

A session-start step in its own module rather than appended to the already
over-gate ``_ceremony_session_start_steps.py``, following the
``_project_handoff_readback.py`` precedent.

**Why it exists.** The FR01 project identity is a digest over a checkout's
canonical root, so it is stable while the path is stable and *changes when the
path does*. Renaming or moving a directory therefore orphans that checkout's
rows under the old namespace, silently: recall simply returns nothing and an
agent has no way to tell "no memory yet" from "the memory is one rename away".

**Detection, never correction.** The signal is deliberately narrow -- the
current project namespace has ZERO rows and at least one populated
``project:<same-slug>-*`` sibling exists -- because that is the exact shape a
move leaves behind, and a fresh clone of a differently named project produces
none of it. Even then this step only reports and names the repair command. A
silent auto-merge on a path change would be indistinguishable from two genuinely
different projects that happened to occupy the same path over time.

**Fail-open, but never silent.** Session start is the mandated first action; an
advisory about a possible rename must never take it down. So no failure path
here raises — but none of them claims "nothing to report" either. Every return
carries a ``status`` of ``measured``, ``absent`` or ``not_measured``: a census
that could not run and a census that ran clean used to be the same omitted key,
which is the ambiguity this step exists to remove.
"""

from __future__ import annotations

import structlog

from trw_mcp.models.typed_dicts import (
    MovedCheckoutCandidateDict as MovedCheckoutCandidateDict,
)
from trw_mcp.models.typed_dicts import (
    MovedCheckoutDict as MovedCheckoutDict,
)

logger = structlog.get_logger(__name__)

__all__ = ["MovedCheckoutDict", "step_moved_checkout"]


def _not_measured(reason: str) -> MovedCheckoutDict:
    """The readback could not run. ``reason`` is an error class or a state name."""
    return MovedCheckoutDict(status="not_measured", reason=reason)


def step_moved_checkout() -> MovedCheckoutDict:
    """Return the moved-checkout observation, always status-bearing.

    Builds its census with ``trw_memory.namespaces.curate.store_census`` -- the
    SAME function the ``memory_namespace_diagnose`` tool uses -- over the
    machine-local user-space store. Two implementations of "what namespaces
    exist" is how the tool and this advisory would come to disagree about
    whether a checkout looks moved, so there is one.

    Reaching for the store directory rather than a hardcoded ``memory.db``
    matters: under ``memory_single_store_path`` the corpus is one file, and
    without it every namespace is its own file under the same directory. A probe
    that stat-ed one filename was blind to the second layout entirely.
    """
    try:
        from trw_memory.models.config import MemoryConfig
        from trw_memory.namespaces.curate import detect_moved_checkout, store_census
        from trw_memory.namespaces.identity import resolve_project_namespace
        from trw_memory.user_paths import resolve_user_memory_dir
    except ImportError:  # pragma: no cover - trw-memory is a hard dependency
        return _not_measured("trw_memory_unavailable")

    user_memory_dir = resolve_user_memory_dir(create=False)
    if not user_memory_dir.exists():
        # No user store is a MEASURED answer: there are no sibling namespaces to
        # have moved away from, so nothing was missed.
        return MovedCheckoutDict(status="absent")
    try:
        config = MemoryConfig(storage_path=str(user_memory_dir))
        namespace = resolve_project_namespace()
        observation = detect_moved_checkout(namespace, store_census(config))
    except Exception as exc:
        logger.warning("moved_checkout_readback_not_measured", error=type(exc).__name__, exc_info=True)
        return _not_measured(type(exc).__name__)
    if observation is None:
        return MovedCheckoutDict(status="absent")
    logger.info(
        "moved_checkout_observed",
        current=observation.current_namespace,
        candidates=len(observation.candidates),
    )
    return MovedCheckoutDict(
        status="measured",
        current_namespace=observation.current_namespace,
        current_rows=observation.current_rows,
        candidates=[
            MovedCheckoutCandidateDict(namespace=candidate.namespace, rows=candidate.rows)
            for candidate in observation.candidates
        ],
        repair_command=observation.repair_command,
    )
