"""Assertion verification helpers — extracted from _recall_impl.py for module-size compliance.

Belongs to the ``_recall_impl.py`` facade. Re-exported there for backward
compatibility with tests and callers that import via the parent module.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring._recall import RecallContext

logger = structlog.get_logger(__name__)


def _assertion_result_detail(
    entry_id: str,
    index: int,
    assertion: Any,
    result: Any,
) -> dict[str, object]:
    """Normalize verification result payloads to the recall response contract."""
    detail = cast("dict[str, object]", result.model_dump())
    detail["id"] = f"{entry_id}:{index}"
    detail.setdefault("type", getattr(assertion, "type", ""))
    detail.setdefault("pattern", getattr(assertion, "pattern", ""))
    detail.setdefault("target", getattr(assertion, "target", ""))
    return detail


def _verify_assertions(
    ranked_learnings: list[dict[str, object]],
    query_tokens: list[str],
    config: TRWConfig,
    rank_fn: Callable[..., list[dict[str, object]]],
    context: RecallContext | None = None,
) -> list[dict[str, object]]:
    """Run assertion verification on ranked learnings (PRD-CORE-086 FR06).

    Also persists verification results (last_result, last_verified_at,
    first_failed_at) and applies auto-stale detection (FR08).
    """
    from datetime import datetime, timedelta, timezone

    assertion_penalties: dict[str, float] = {}
    project_root_path: Path | None = None
    try:
        from trw_mcp.state._paths import resolve_project_root

        project_root_path = resolve_project_root()
    except Exception:  # justified: fail-open
        logger.debug("assertion_project_root_resolve_failed", exc_info=True)

    try:
        from trw_memory.lifecycle.verification import verify_assertions
        from trw_memory.models.memory import Assertion

        now = datetime.now(timezone.utc)
        stale_threshold = now - timedelta(days=config.assertion_stale_threshold_days)

        for learning in ranked_learnings:
            raw_assertions = learning.get("assertions")
            if not raw_assertions or not isinstance(raw_assertions, list):
                continue
            entry_id = str(learning.get("id", ""))
            try:
                assertions_list = [
                    Assertion.model_validate(a, strict=False) for a in raw_assertions if isinstance(a, dict)
                ]
                results = verify_assertions(assertions_list, project_root_path)

                passing = sum(1 for r in results if r.passed is True)
                failing = sum(1 for r in results if r.passed is False)
                stale = sum(1 for r in results if r.passed is None)

                learning["assertion_status"] = {
                    "passing": passing,
                    "failing": failing,
                    "stale": stale,
                    "details": [
                        _assertion_result_detail(entry_id, index, assertion, result)
                        for index, (assertion, result) in enumerate(
                            zip(assertions_list, results, strict=False),
                            start=1,
                        )
                    ],
                }

                if failing > 0:
                    penalty = config.assertion_failure_penalty * (failing / len(results))
                    assertion_penalties[entry_id] = penalty

                # FR06: Update assertion fields with verification results
                updated_assertions: list[dict[str, object]] = []
                for assertion, result in zip(assertions_list, results, strict=False):
                    a_dict = assertion.model_dump()
                    a_dict["last_result"] = result.passed
                    a_dict["last_verified_at"] = now.isoformat()
                    a_dict["last_evidence"] = result.evidence
                    # FR08: Track first_failed_at transitions
                    if result.passed is False:
                        # Set first_failed_at if not already set (transition to failure)
                        if assertion.first_failed_at is None:
                            a_dict["first_failed_at"] = now.isoformat()
                    elif result.passed is True:
                        # Clear first_failed_at on transition back to passing
                        a_dict["first_failed_at"] = None
                    updated_assertions.append(a_dict)

                # Persist updated assertions via backend
                try:
                    from trw_mcp.state._paths import resolve_trw_dir
                    from trw_mcp.state.memory_adapter import get_backend

                    trw_dir = resolve_trw_dir()
                    backend = get_backend(trw_dir)
                    backend.update(entry_id, assertions=json.dumps(updated_assertions))
                except Exception:  # justified: persist is best-effort
                    logger.debug("assertion_result_persist_failed", entry_id=entry_id, exc_info=True)

                # FR08: Auto-stale detection — if ALL assertions have been
                # failing for longer than the threshold, mark learning stale
                all_persistently_failing = len(updated_assertions) > 0 and all(
                    a.get("first_failed_at") is not None
                    and datetime.fromisoformat(str(a["first_failed_at"])) < stale_threshold
                    for a in updated_assertions
                )
                if all_persistently_failing:
                    logger.info(
                        "learning_auto_stale",
                        entry_id=entry_id,
                        threshold_days=config.assertion_stale_threshold_days,
                    )
                    learning["verification_status"] = "stale"

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
