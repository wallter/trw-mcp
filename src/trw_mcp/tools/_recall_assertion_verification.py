"""Stored evidence interpretation at recall; verification belongs to maintenance.

The historical internal name remains the recall facade's call boundary. No repo
scan, cache lookup, persistence, work scheduling or Q settlement occurs here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.tools._stored_claim_evidence import stored_claim_evidence


def _verify_assertions(
    ranked_learnings: list[dict[str, object]],
    query_tokens: list[str],
    config: TRWConfig,
    rank_fn: Callable[..., list[dict[str, object]]],
    context: RecallContext | None = None,
    *,
    rank_always: bool = False,
) -> list[dict[str, object]]:
    """Qualify acquired evidence and rank dated failures before downstream caps."""
    moment = datetime.now(timezone.utc)
    has_failures = False
    qualified: list[dict[str, object]] = []
    for source in ranked_learnings:
        entry = dict(source)
        from trw_mcp.state._recall_signals import current_recall_signals

        signals = current_recall_signals()
        if signals is not None:
            signals.transfer(source, entry)
        evidence, failure_fraction = stored_claim_evidence(
            source, ttl_seconds=config.verification_cache_ttl_seconds, now=moment
        )
        entry["verification_evidence"] = evidence
        state = evidence["observation"]
        entry["verification_status"] = f"last_known_{state}" if state != "unknown" else "unknown"
        # Old inline results are not present-tree proof, even on legacy dicts.
        entry.pop("assertion_status", None)
        has_failures = has_failures or failure_fraction > 0
        qualified.append(entry)
    if has_failures or rank_always:

        def candidate_penalty(entry: dict[str, object]) -> float:
            # Reuse the same UTC read instant. Never alias evidence by bare ID:
            # acquired rows may collide, including after namespace projection.
            _evidence, fraction = stored_claim_evidence(
                entry, ttl_seconds=config.verification_cache_ttl_seconds, now=moment
            )
            return config.assertion_failure_penalty * fraction

        return rank_fn(
            qualified,
            query_tokens,
            config.recall_utility_lambda,
            assertion_penalties=candidate_penalty,
            context=context,
        )
    return qualified
