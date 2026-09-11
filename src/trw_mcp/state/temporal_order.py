"""Preserve query-relative eligibility across public recall reranking."""

from __future__ import annotations

TEMPORAL_ELIGIBILITY_FIELD = "_temporal_eligible"


def prioritize_temporal_eligibility(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """Stable partition: confirmed eligible, unknown, confirmed ineligible.

    Unknown remote/injected records are not asserted eligible. This ordering
    does not itself validate their windows; that remains the producer's job.
    """
    if not any(TEMPORAL_ELIGIBILITY_FIELD in entry for entry in entries):
        return entries

    def group(entry: dict[str, object]) -> int:
        value = entry.get(TEMPORAL_ELIGIBILITY_FIELD)
        return 0 if value is True else 2 if value is False else 1

    return sorted(entries, key=group)
