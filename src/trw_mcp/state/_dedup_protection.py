"""Protection-preserving field merge for near-duplicate learnings (PRD-CORE-110).

Belongs to ``trw_mcp.state.dedup``; moved out verbatim to keep that module under
its effective-LOC ceiling. Behaviour is unchanged.
"""

from __future__ import annotations

# PRD-CORE-110: strength ordering for protection-preserving merges. Higher
# index = stronger; merge keeps the stronger of (survivor, incoming).
_PROTECTION_TIER_ORDER = ("low", "normal", "high", "critical", "protected", "permanent")
_CONFIDENCE_ORDER = ("hypothesis", "unverified", "low", "medium", "high", "verified")


def _stronger(existing_val: str, new_val: str, order: tuple[str, ...], default: str) -> str:
    """Return whichever of *existing_val* / *new_val* ranks higher in *order*.

    Unknown values fall back to *default*'s rank so an unrecognised string
    never silently wins over a known stronger tier.
    """

    def rank(v: str) -> int:
        return order.index(v) if v in order else order.index(default)

    return new_val if rank(new_val) > rank(existing_val) else existing_val


def _merge_protection_fields(existing: dict[str, object], new_entry_data: dict[str, object]) -> None:
    """Fold typed protection fields from the new entry into the survivor.

    - ``protection_tier``: keep the stronger tier.
    - ``confidence``: keep the higher confidence.
    - ``type``: upgrade ``pattern`` → ``incident`` when the incoming entry is
      an incident (incidents carry operational weight worth retaining).
    Mutates *existing* in place. Absent fields default to the survivor's value.
    """
    existing_tier = str(existing.get("protection_tier") or "normal")
    new_tier = str(new_entry_data.get("protection_tier") or "normal")
    existing["protection_tier"] = _stronger(existing_tier, new_tier, _PROTECTION_TIER_ORDER, "normal")

    existing_conf = str(existing.get("confidence") or "unverified")
    new_conf = str(new_entry_data.get("confidence") or "unverified")
    existing["confidence"] = _stronger(existing_conf, new_conf, _CONFIDENCE_ORDER, "unverified")

    existing_type = str(existing.get("type") or "pattern")
    new_type = str(new_entry_data.get("type") or "pattern")
    if new_type == "incident" and existing_type != "incident":
        existing["type"] = "incident"
