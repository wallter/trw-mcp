"""Marshal ``store_learning`` parameters into ``memory_store_impl`` arguments.

PRD-CORE-251 FR03. trw-mcp no longer builds the :class:`MemoryEntry` itself --
``memory_store_impl`` does, through the PRD-CORE-245 FR08 chokepoint -- but four
decisions stay on this side because they are TRW knowledge topology rather than
memory concerns, and every one of them has to be expressed as an *argument*:

* the destination **tier** (PRD-CORE-185 FR05), which picks BOTH the namespace
  and, in the caller, which backend the write goes to;
* the ``metadata["tier"]`` stamp and the injection guard that goes with it;
* the source-provenance whitelist, the assertion objects and the anchor
  objects, whose coercion used to live in ``_learning_to_memory_entry``;
* the Q-value pre-seed, which is trw-mcp's own reinforcement signal
  (``scoring/_correlation.py`` is a PRD-CORE-251 section 6 "keep in trw-mcp"
  concern).

This module holds no I/O and no entry construction. It is the construction
half of ``_memory_transforms`` reduced to what it always actually was: argument
preparation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import structlog
from trw_memory.models.memory import Anchor, Assertion, Confidence, MemoryType, ProtectionTier

from trw_mcp.state._constants import DEFAULT_NAMESPACE, VALID_SOURCES
from trw_mcp.state._tier_routing import Tier

logger = structlog.get_logger(__name__)

_SourceType = Literal["human", "agent", "tool", "consolidated", "team_sync"]


@dataclass(frozen=True)
class StoreArguments:
    """The trw-mcp-decided half of one ``memory_store_impl`` call."""

    tier: Tier
    namespace: str
    metadata: dict[str, str]
    source: _SourceType
    assertions: list[Assertion]
    anchors: list[Anchor]
    q_value: float
    type: MemoryType
    confidence: Confidence
    protection_tier: ProtectionTier


def build_store_arguments(
    *,
    summary: str,
    detail: str,
    tags: list[str] | None,
    shard_id: str | None,
    source_type: str,
    assertions: list[dict[str, str]] | None,
    type: str,
    confidence: str,
    domain: list[str] | None,
    phase_affinity: list[str] | None,
    protection_tier: str,
    anchors: list[dict[str, object]] | None,
    impact: float,
    metadata: dict[str, str] | None,
    scope: Literal["auto", "project", "user"],
) -> StoreArguments:
    """Resolve the routing, metadata and typed values for one learning write.

    Raises:
        ValueError: If ``type``, ``confidence`` or ``protection_tier`` is not a
            member of its enum. Raising here (rather than coercing to a default)
            is what makes an invalid enum a deterministic, dead-letterable
            failure instead of a silently mis-typed row.
    """
    from trw_mcp.scoring._correlation import compute_initial_q_value
    from trw_mcp.state._tier_routing import USER_NAMESPACE, route_tier

    tier = route_tier(
        scope=scope,
        source_type=source_type,
        tags=tags,
        domain=domain,
        phase_affinity=phase_affinity,
        summary=summary,
        detail=detail,
    )

    merged_metadata: dict[str, str] = {}
    if shard_id:
        merged_metadata["shard_id"] = shard_id
    if metadata:
        merged_metadata.update(metadata)
    # core185-11 / core185-METADATA-TIER-INJECT-5: the ROUTING decision is
    # authoritative over any caller-supplied ``metadata["tier"]``. User-tier
    # entries are stamped so ``tier_of_entry`` matches the backfill-promotion
    # path; project-tier entries are left UNSTAMPED (back-compat) and a
    # caller-injected key is STRIPPED so it cannot divert a project entry into
    # the user backend.
    if tier == "user":
        merged_metadata["tier"] = "user"
    else:
        merged_metadata.pop("tier", None)

    assertion_objects = [Assertion.model_validate(a, strict=False) for a in assertions or []]

    anchor_objects: list[Anchor] = []
    for anchor in anchors or []:
        try:
            # Anchor rejects absolute paths; convert rather than drop.
            anchor_data = dict(anchor)
            file_val = str(anchor_data.get("file", ""))
            if file_val.startswith("/"):
                anchor_data["file"] = file_val.lstrip("/")
            anchor_objects.append(Anchor.model_validate(anchor_data))
        except Exception:  # justified: fail-open, skip invalid anchors
            logger.debug("invalid_anchor_skipped", anchor=anchor, exc_info=True)

    return StoreArguments(
        tier=tier,
        namespace=USER_NAMESPACE if tier == "user" else DEFAULT_NAMESPACE,
        metadata=merged_metadata,
        source=cast("_SourceType", source_type if source_type in VALID_SOURCES else "agent"),
        assertions=assertion_objects,
        anchors=anchor_objects,
        q_value=compute_initial_q_value(impact),
        type=MemoryType(type) if isinstance(type, str) else type,
        confidence=Confidence(confidence) if isinstance(confidence, str) else confidence,
        protection_tier=(ProtectionTier(protection_tier) if isinstance(protection_tier, str) else protection_tier),
    )
