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
from trw_mcp.state._constants import DEFAULT_NAMESPACE

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

    PRD-CORE-244 FR04: an entry whose assertion FAILED now also earns a durable
    negative Q observation. The contradicted ids are accumulated across the whole
    pass and settled in ONE batched call afterwards, so a 25-result recall costs
    at most one additional write rather than one per contradicted entry.
    """
    from trw_mcp.tools._verification_cache import warm_verified_verdict
    from trw_mcp.tools._verification_pass import (
        persist_verification_outcome,
        run_verification_pass,
    )

    assertion_penalties: dict[str, float] = {}
    contradicted_ids: list[str] = []
    checked = 0
    verified = 0
    stale_count = 0
    cache_hits = 0
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
            namespace = str(learning.get("namespace") or DEFAULT_NAMESPACE)
            try:
                # FR03: a CLEAN verdict reached inside the TTL is reused and
                # costs no filesystem verification for this entry. An adverse or
                # inconclusive verdict is always re-examined so it can clear.
                if warm_verified_verdict(
                    backend,
                    entry_id,
                    namespace=namespace,
                    ttl_seconds=config.verification_cache_ttl_seconds,
                ):
                    cache_hits += 1
                    verified += 1
                    learning["verification_status"] = "verified"
                    continue

                outcome = run_verification_pass(
                    entry_id,
                    raw_assertions,
                    raw_anchors,
                    namespace=namespace,
                    assertion_failure_penalty=config.assertion_failure_penalty,
                    assertion_stale_threshold_days=config.assertion_stale_threshold_days,
                    anchor_validity_verified_floor=config.anchor_validity_verified_floor,
                    project_root=project_root_path,
                )

                if outcome.verifiable:
                    checked += 1
                if outcome.assertion_status:
                    learning["assertion_status"] = outcome.assertion_status
                if outcome.penalty:
                    assertion_penalties[entry_id] = outcome.penalty
                if outcome.verifiable and outcome.failing > 0 and entry_id:
                    contradicted_ids.append(entry_id)
                if outcome.anchor_validity is not None:
                    learning["anchor_validity"] = outcome.anchor_validity
                if not outcome.verifiable:
                    # Nothing was actually checked — leave whatever verdict is
                    # already persisted on the payload rather than inventing or
                    # erasing one.
                    pass
                elif outcome.verification_status == "stale":
                    stale_count += 1
                    logger.info(
                        "learning_auto_stale",
                        entry_id=entry_id,
                        threshold_days=config.assertion_stale_threshold_days,
                    )
                    learning["verification_status"] = "stale"
                elif outcome.verification_status == "verified":
                    # FR03: examined AND clean is now a value, not the absence
                    # of one, so it survives into a later session's payload.
                    verified += 1
                    learning["verification_status"] = "verified"
                else:
                    # Examined but neither clean nor persistently failing — a
                    # failing assertion inside the staleness window, or an
                    # anchor set that drifted below the verified floor. Neither
                    # verdict applies, and a previously-stale entry must not
                    # keep advertising the old one on this response either.
                    learning.pop("verification_status", None)

                if backend is not None:
                    persist_verification_outcome(backend, outcome)

            except Exception:  # justified: scan-resilience
                logger.debug(
                    "assertion_verification_error",
                    entry_id=entry_id,
                    exc_info=True,
                )

        # NFR05: one structured record per pass carrying the counters.
        logger.info(
            "verification_pass_counters",
            checked=checked,
            verified=verified,
            stale=stale_count,
            contradicted=len(contradicted_ids),
            cache_hits=cache_hits,
        )
        _apply_contradiction_penalties(contradicted_ids)

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


def _apply_contradiction_penalties(contradicted_ids: list[str]) -> None:
    """Settle FR04's per-entry negative Q observations in one batched write.

    Fail-open: a scoring write must never fail a recall, matching the
    best-effort contract the rest of this pass already keeps.
    """
    if not contradicted_ids:
        return
    try:
        from trw_mcp.scoring import apply_contradiction_penalty
        from trw_mcp.state._paths import resolve_trw_dir

        apply_contradiction_penalty(contradicted_ids, resolve_trw_dir())
    except Exception:  # justified: fail-open, recall must not fail on a reward write
        logger.debug("contradiction_penalty_skipped", entry_ids=contradicted_ids, exc_info=True)


def _resolve_backend() -> Any | None:
    """Resolve the memory backend for verification write-back, or ``None``."""
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.memory_adapter import get_backend

        return get_backend(resolve_trw_dir())
    except Exception:  # justified: persist is best-effort
        logger.debug("verification_backend_unavailable", exc_info=True)
        return None
