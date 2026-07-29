"""Assertion verification helpers — extracted from _recall_impl.py for module-size compliance.

Belongs to the ``_recall_impl.py`` facade. Re-exported there for backward
compatibility with tests and callers that import via the parent module.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring._recall import RecallContext

logger = structlog.get_logger(__name__)

# The per-result normaliser moved to _verification_pass (PRD-CORE-231, shared
# with the maintain-verify sweep). Re-exported so the _recall_impl facade — and
# any caller importing it from there — keeps working.
from trw_mcp.tools._verification_pass import (  # noqa: E402
    _assertion_result_detail as _assertion_result_detail,
)


def _raw_list(learning: dict[str, object], key: str) -> list[object]:
    """Return a learning's serialized ``assertions``/``anchors`` list, or ``[]``."""
    raw = learning.get(key)
    return raw if isinstance(raw, list) else []


def _verify_assertions(
    ranked_learnings: list[dict[str, object]],
    query_tokens: list[str],
    config: TRWConfig,
    rank_fn: Callable[..., list[dict[str, object]]],
    context: RecallContext | None = None,
) -> list[dict[str, object]]:
    """Run assertion + anchor verification on ranked learnings.

    PRD-CORE-086 FR06/FR08 (assertion results, auto-stale) plus
    PRD-CORE-231 FR02/FR03: the computed ``verification_status`` and the
    freshly recomputed ``anchor_validity`` are PERSISTED in the same batched
    ``backend.update()`` that already wrote ``assertions``, instead of living
    only on the response payload.
    """
    from trw_mcp.tools._verification_pass import (
        persist_verification_outcome,
        run_verification_pass,
    )

    assertion_penalties: dict[str, float] = {}
    project_root_path: Path | None = None
    try:
        from trw_mcp.state._paths import resolve_project_root

        project_root_path = resolve_project_root()
    except Exception:  # justified: fail-open
        logger.debug("assertion_project_root_resolve_failed", exc_info=True)

    try:
        backend = _resolve_backend()

        for learning in ranked_learnings:
            raw_assertions = _raw_list(learning, "assertions")
            raw_anchors = _raw_list(learning, "anchors")
            # FR03: an entry with anchors but no assertions still needs anchor
            # re-verification; only a fully unanchored, unasserted entry is skipped.
            if not raw_assertions and not raw_anchors:
                continue
            entry_id = str(learning.get("id", ""))
            try:
                outcome = run_verification_pass(
                    entry_id,
                    raw_assertions,
                    raw_anchors,
                    assertion_failure_penalty=config.assertion_failure_penalty,
                    assertion_stale_threshold_days=config.assertion_stale_threshold_days,
                    project_root=project_root_path,
                )

                if outcome.assertion_status:
                    learning["assertion_status"] = outcome.assertion_status
                if outcome.penalty:
                    assertion_penalties[entry_id] = outcome.penalty
                if outcome.anchor_validity is not None:
                    learning["anchor_validity"] = outcome.anchor_validity
                if not outcome.verifiable:
                    # Nothing was actually checked — leave whatever verdict is
                    # already persisted on the payload rather than inventing or
                    # erasing one.
                    pass
                elif outcome.verification_status == "stale":
                    logger.info(
                        "learning_auto_stale",
                        entry_id=entry_id,
                        threshold_days=config.assertion_stale_threshold_days,
                    )
                    learning["verification_status"] = "stale"
                else:
                    # A previously-stale entry that re-passes must not keep
                    # advertising the old verdict on this response either.
                    learning.pop("verification_status", None)

                if backend is not None:
                    persist_verification_outcome(backend, outcome)

            except Exception:  # justified: scan-resilience
                logger.debug(
                    "assertion_verification_error",
                    entry_id=entry_id,
                    exc_info=True,
                )

        if assertion_penalties:
            ranked_learnings = rank_fn(
                ranked_learnings,
                query_tokens,
                config.recall_utility_lambda,
                assertion_penalties=assertion_penalties,
                context=context,
            )
    except (ImportError, OSError):
        logger.debug("assertion_verification_unavailable", exc_info=True)

    return ranked_learnings


def _resolve_backend() -> Any | None:
    """Resolve the memory backend for verification write-back, or ``None``."""
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.memory_adapter import get_backend

        return get_backend(resolve_trw_dir())
    except Exception:  # justified: persist is best-effort
        logger.debug("verification_backend_unavailable", exc_info=True)
        return None
