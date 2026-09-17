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
    from pathlib import Path

    from trw_mcp.profile.model import ResolvedProfile


def resolution_basis(resolved: ResolvedProfile, *, run_dir: Path | None) -> dict[str, object]:
    """Describe WHAT a resolved profile was resolved from.

    PRD-FIX-141-FR06. ``trw_session_start`` reported
    ``resolved_profile.ceremony_tier: COMPREHENSIVE`` and ``trw_profile_explain``
    reported ``STANDARD`` on the same machine in the same run (learning L-Rikf).
    Both surfaces already call the SAME :func:`resolve_session_profile`; the
    divergence is an ORDERING effect. ``trw_session_start`` runs before
    ``trw_init``, so no run directory — and therefore no Scout-written
    ``meta/session_profile.yaml`` session layer — existed yet, and the tier came
    from the defaults layer. ``trw_profile_explain``, called afterwards, read the
    session layer the Scout had since written.

    Neither value was wrong. What was missing is that neither payload said what
    it had been resolved FROM, so two correct answers read as a contradiction.
    Both surfaces emit this block, so a reader can tell "the same inputs
    disagree" (a defect) from "the inputs changed" (a session profile arriving).

    Returns:
        ``{"run_dir": str|None, "session_layer_present": bool,
        "layers_applied": [...], "ceremony_tier": str|None}``.
    """
    layers = list(resolved.layers_applied)
    tier = resolved.profile.ceremony_tier
    return {
        "run_dir": str(run_dir) if run_dir is not None else None,
        "session_layer_present": "session" in layers,
        "layers_applied": layers,
        "ceremony_tier": tier,
    }


def build_explanation(resolved: ResolvedProfile, *, run_dir: Path | None = None) -> dict[str, object]:
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
        # PRD-FIX-141-FR06: the same block session_start emits, so a reader can
        # reconcile two reports instead of choosing between them.
        "profile_resolution_basis": resolution_basis(resolved, run_dir=run_dir),
    }


__all__ = ["build_explanation", "resolution_basis"]
