"""Per-field profile explanation — PRD-HPO-PROF-001 FR-11 / NFR-5.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

``build_explanation`` renders the structured payload that
``trw_profile_explain`` returns: for every field of the resolved profile it
reports ``{field, value, origin_layer, override_chain[]}`` plus the
top-level resolution metadata (layers applied, snapshot ids). The payload is
a plain JSON-serializable dict so the MCP tool can hand it straight back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.profile.model import ResolvedProfile


def build_explanation(resolved: ResolvedProfile) -> dict[str, object]:
    """Build the FR-11 explanation payload for ``resolved``.

    Returns a dict with ``fields`` (a list of per-field attribution records,
    one per surface key, sorted by field name for stable output) and the
    resolution metadata. Fields the profile never set still appear with a
    ``None`` value and empty chain so the operator sees the full surface.
    """
    fields: list[dict[str, object]] = []
    for field_name in sorted(resolved.attribution):
        attr = resolved.attribution[field_name]
        fields.append(
            {
                "field": attr.field,
                "value": attr.value,
                "origin_layer": attr.origin_layer,
                "override_chain": list(attr.override_chain),
            }
        )
    return {
        "fields": fields,
        "layers_applied": list(resolved.layers_applied),
        "surface_snapshot_id": resolved.surface_snapshot_id,
        "session_override_hash": resolved.session_override_hash,
        "resolved_profile": resolved.profile.model_dump(exclude_none=True, mode="json"),
    }


__all__ = ["build_explanation"]
