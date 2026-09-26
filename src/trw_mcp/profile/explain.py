"""Per-field profile explanation — PRD-HPO-PROF-001 FR-11 / NFR-5.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

``build_explanation`` renders the per-field payload: for every field of the
resolved profile it reports ``{field, value, origin_layer, override_chain[]}``
plus the top-level resolution metadata (layers applied, snapshot ids).
``explain_surface`` is the one service behind ``trw_status(detail="surface")``
and the ``trw-mcp profile explain`` CLI (they replaced the profile-explain MCP
tool, PRD-CORE-300 S11b): the profile explanation plus the tool surface the
session's config resolves to. Payloads are plain JSON-serializable dicts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.profile.model import ResolvedProfile


def resolution_basis(resolved: ResolvedProfile, *, run_dir: Path | None) -> dict[str, object]:
    """Describe WHAT a resolved profile was resolved from.

    PRD-FIX-141-FR06. ``trw_session_start`` reported
    ``resolved_profile.ceremony_tier: COMPREHENSIVE`` and the profile explanation
    reported ``STANDARD`` on the same machine in the same run (learning L-Rikf).
    Both surfaces already call the SAME :func:`resolve_session_profile`; the
    divergence is an ORDERING effect. ``trw_session_start`` runs before
    ``trw_init``, so no run directory — and therefore no Scout-written
    ``meta/session_profile.yaml`` session layer — existed yet, and the tier came
    from the defaults layer. The explanation, requested afterwards, read the
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


def tool_surface_summary(config: TRWConfig) -> dict[str, object]:
    """The tool surface ``config`` resolves to: role, mode, packs and tools.

    Reads the pure membership module, so it needs no server import. A reviewer
    role reports ``REVIEWER_TOOLS``; any other session reports the kernel plus
    every pack whose flag is on (PRD-CORE-300 S11b), and names each pack that is
    off with the flag that would turn it on.
    """
    from trw_mcp.models.surface_packs import FLAG_GATED_PACKS, PACK_TOOLS, REVIEWER_TOOLS, enabled_packs
    from trw_mcp.state._surface_role import reviewer_role_active

    if reviewer_role_active():
        return {"role": "reviewer", "tools": sorted(REVIEWER_TOOLS), "packs": [], "off": {}}
    mode = str(getattr(config, "tool_resolution_mode", "standard"))
    packs = enabled_packs(
        mode,
        comms_enabled=getattr(config, "comms_enabled", False) is True,
        dispatch_enabled=getattr(config, "dispatch_tools_exposed", False) is True,
        assess_enabled=getattr(config, "assess_enabled", False) is True,
    )
    return {
        "role": "agent",
        "mode": mode,
        "packs": list(packs),
        "tools": sorted(tool for pack in packs for tool in PACK_TOOLS[pack]),
        "off": {pack: FLAG_GATED_PACKS[pack] for pack in PACK_TOOLS if pack not in packs},
    }


def explain_surface(
    config: TRWConfig,
    *,
    run_dir: Path | None,
    trw_dir: Path | None,
    domain: str | None = None,
    task_type: str | None = None,
    prd_path: str | None = None,
    task_name: str | None = None,
) -> dict[str, object]:
    """The profile explanation plus the resolved tool surface (one service).

    Resolves the full 6-layer profile chain (defaults -> org -> domain ->
    task-type -> session -> client) exactly as session start does, then adds
    ``tool_surface`` from :func:`tool_surface_summary`. Works with no pinned run
    (``run_dir=None``): the session layer is then simply absent.
    """
    from trw_mcp.profile.session_resolve import resolve_session_profile

    resolved = resolve_session_profile(
        config,
        run_dir=run_dir,
        domain=domain,
        task_type=task_type,
        prd_path=prd_path,
        task_name=task_name,
        trw_dir=trw_dir,
    )
    payload = build_explanation(resolved, run_dir=run_dir)
    payload["tool_surface"] = tool_surface_summary(config)
    return payload


__all__ = ["build_explanation", "explain_surface", "resolution_basis", "tool_surface_summary"]
