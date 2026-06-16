"""Hierarchical profile system — PRD-HPO-PROF-001 (H2 adaptive surface).

Public facade for the ``trw_mcp.profile`` package. The 6-layer composition
chain (``defaults → org → domain → task-type → session → client``) resolves a
``ResolvedProfile`` on every ``trw_session_start``; the resolved
``surface_snapshot_id`` joins persistent-surface telemetry while
``session_override_hash`` carries per-session deltas.

Single import point — callers do ``from trw_mcp.profile import compose,
ResolvedProfile`` rather than reaching into sibling modules.
"""

from __future__ import annotations

from trw_mcp.profile.explain import build_explanation
from trw_mcp.profile.inference import infer_domain, infer_task_type
from trw_mcp.profile.invariants import (
    InvariantViolation,
    InvariantViolationError,
    enforce_invariants,
    run_invariants,
)
from trw_mcp.profile.loader import (
    LayerLoadError,
    discover_layers,
    load_layer,
    translate_legacy_client_profile,
)
from trw_mcp.profile.model import (
    LAYER_ORDER,
    PERSISTENT_LAYER_NAMES,
    PROFILE_SURFACE_KEYS,
    UNSET_SENTINEL,
    ConfidenceBands,
    LayerAttribution,
    PhaseName,
    Profile,
    ProfileLayer,
    RecallPolicy,
    ResolvedProfile,
)
from trw_mcp.profile.resolver import compose
from trw_mcp.profile.session_resolve import resolve_session_profile
from trw_mcp.profile.snapshot import (
    compute_session_override_hash,
    compute_surface_snapshot_id,
)

__all__ = [
    "LAYER_ORDER",
    "PERSISTENT_LAYER_NAMES",
    "PROFILE_SURFACE_KEYS",
    "UNSET_SENTINEL",
    "ConfidenceBands",
    "InvariantViolation",
    "InvariantViolationError",
    "LayerAttribution",
    "LayerLoadError",
    "PhaseName",
    "Profile",
    "ProfileLayer",
    "RecallPolicy",
    "ResolvedProfile",
    "build_explanation",
    "compose",
    "compute_session_override_hash",
    "compute_surface_snapshot_id",
    "discover_layers",
    "enforce_invariants",
    "infer_domain",
    "infer_task_type",
    "load_layer",
    "resolve_session_profile",
    "run_invariants",
    "translate_legacy_client_profile",
]
