"""Authoritative TRW surface manifest + resolution (PRD-CORE-218 FR01/FR02/FR04).

This module is the single source of truth for the public MCP **tool** surface:
which tools exist, who owns each, which capability pack it belongs to, its
lifecycle and public status, and how a task resolves to a bounded tool set.

It is deliberately *pure* (only stdlib + pydantic, no ``trw_mcp.server`` or
config imports) so both directions can consume it without cycles:
  - ``server/_tools.py`` validates registration parity against it (FR01);
  - ``models/config/_fields_tools.py`` resolves the configured mode against it
    (FR04) via a call-time import.

Scope note: THIS slice populates only ``SurfaceKind.TOOL`` rows — the surface
that ``server/_tools.py`` registers. Skill/hook/resource/prompt rows ride the
existing bundled-data inventory (``data/`` auto-discovery +
``scripts/generate-inventory.py``); the schema is general enough to add them
later without change.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict

# PRD-CORE-218 FR02: pack membership single source of truth. ``surface_packs`` is
# a pure stdlib module (no server/config import), so this registry and
# ``models/config/_defaults`` read the SAME table and cannot diverge. This module
# remains the authoritative surface AUTHORITY (manifest schema, owner mapping,
# lifecycle/public status, versioned kernel digest, and resolution) — it just no
# longer keeps a private copy of the membership data.
from trw_mcp.models.surface_packs import KERNEL_TOOLS, OPERATOR_ONLY_TOOLS
from trw_mcp.models.surface_packs import PACK_TOOLS as PACK_TOOLS
from trw_mcp.models.surface_packs import STANDARD_TASK_PACKS as STANDARD_TASK_PACKS

# Private module alias consumed internally and re-exported to server/_tools.py.
# A module-level assignment (vs. an ``as _KERNEL_TOOLS`` import) makes the name
# an explicitly defined attribute under ``mypy --strict`` for downstream import.
_KERNEL_TOOLS = KERNEL_TOOLS

# =====================================================================
# FR01: manifest schema
# =====================================================================


class SurfaceKind(str, Enum):
    """Kind of framework surface an entry describes."""

    TOOL = "tool"
    SKILL = "skill"
    HOOK = "hook"
    RESOURCE = "resource"
    PROMPT = "prompt"


class SurfaceLifecycle(str, Enum):
    """Lifecycle decision for a manifest entry (reversible before removal)."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class SurfaceManifestEntry(BaseModel):
    """One authoritative surface row.

    Every public tool resolves to exactly one entry. ``owner`` names the
    implementing module (single-writer authority), ``pack`` is the exactly-one
    capability pack it belongs to, ``lifecycle`` and ``public`` are its admission
    decisions, and ``validation_reference`` is the focused proof that guards it.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    kind: SurfaceKind
    owner: str
    pack: str
    lifecycle: SurfaceLifecycle = SurfaceLifecycle.ACTIVE
    public: bool = True
    validation_reference: str


# =====================================================================
# FR02: exact stable minimal kernel + capability packs
# =====================================================================
#
# ``_KERNEL_TOOLS`` (the exact nine-tool kernel), ``PACK_TOOLS`` (the 12-pack /
# 50-tool membership), and ``STANDARD_TASK_PACKS`` are imported at the top of
# this module from ``trw_mcp.models.surface_packs`` — the single source of truth
# both this registry and ``models/config/_defaults`` read. Kernel changes are a
# versioned event enforced by the pinned digest below.

#: Owning module (single-writer authority) per tool, from the live
#: registrar-to-tool mapping in ``server/_tools.py``.
_TOOL_OWNER: dict[str, str] = {
    "trw_session_start": "tools.ceremony",
    "trw_deliver": "tools.ceremony",
    "trw_heartbeat": "tools.ceremony",
    "trw_adopt_run": "tools.ceremony",
    "trw_status": "tools.orchestration",
    "trw_init": "tools.orchestration",
    "trw_checkpoint": "tools.orchestration",
    "trw_recall": "tools.learning",
    "trw_learn": "tools.learning",
    "trw_learn_update": "tools.learning",
    "trw_instructions_sync": "tools.learning",
    "trw_claude_md_sync": "tools.learning",
    "trw_skill_discovery": "tools.skill_discovery",
    "trw_request_tool_access": "tools.phase_overrides",
    "trw_profile_explain": "tools.trw_profile_explain",
    "trw_build_check": "tools.build",
    "trw_review": "tools.review",
    "trw_prd_create": "tools.requirements",
    "trw_prd_validate": "tools.requirements",
    "trw_prd_diff": "tools.query_tools",
    "trw_query_events": "tools.query_tools",
    "trw_surface_diff": "tools.query_tools",
    "trw_code_search": "tools.code_search",
    "trw_code_symbol": "tools.code_search",
    "trw_before_edit_hint": "tools.before_edit_hint",
    "trw_before_edit_hint_batch": "tools.before_edit_hint_batch",
    "trw_code_index_update": "tools.code_index",
    "trw_codebase_risk_report": "tools.codebase_risk_report",
    "trw_entity_risk_map": "tools.entity_risk_map",
    "trw_ordering_compare": "tools.ordering_compare",
    "trw_cross_repo_ordering": "tools.cross_repo_ordering",
    "trw_delivery_status": "tools.delivery_ops",
    "trw_delivery_recover": "tools.delivery_ops",
    "trw_pre_compact_checkpoint": "tools.checkpoint",
    "trw_replay_outcomes": "tools.replay",
    "trw_dispatch": "tools.dispatch",
    "trw_dispatch_status": "tools.dispatch",
    "trw_agent_work_evidence": "tools.agent_work_evidence",
    "trw_validate_agent_work_evidence": "tools.agent_work_evidence",
    "trw_probe": "tools.trw_probe",
    "trw_probe_budget_status": "tools.trw_probe",
    "trw_meta_tune_rollback": "tools.meta_tune_ops",
    "trw_meta_tune_propose": "tools.meta_tune_ops",
    "trw_surface_classify": "tools.meta_tune_ops",
    "trw_mcp_security_status": "tools.mcp_security_status",
    "trw_pipeline_health": "tools._pipeline_health_tool",
    "trw_channel_render": "tools.channel_render",
    "trw_channel_stats": "tools.channel_stats",
    "trw_graph_related": "tools.knowledge",
    "trw_submit_feedback": "tools.submit_feedback",
}

#: Deprecated tools (still registered, in the removal queue). ``trw_claude_md_sync``
#: is the deprecated alias of ``trw_instructions_sync`` (PRD-CORE-218 §4).
_DEPRECATED_TOOLS: frozenset[str] = frozenset({"trw_claude_md_sync"})

#: Operator-only / internal surfaces: registered but not part of the advertised
#: agent surface. Excluded from ``standard`` and ``all`` resolution; reached only
#: via explicit operator grant. Sourced from the pure ``surface_packs``
#: single-source-of-truth (the former ``_defaults.INTENTIONALLY_UNBRIDGED_TOOLS``
#: mirror was removed with the CORE-125 preset vocabulary — this is now the only
#: operator-only authority; the FR01 test asserts manifest non-public == this).
_OPERATOR_ONLY_TOOLS = OPERATOR_ONLY_TOOLS

_MANIFEST_VALIDATION_REF = "trw-mcp/tests/test_tool_presets.py::test_prd_core_218_fr01"


def _build_tool_manifest() -> tuple[SurfaceManifestEntry, ...]:
    return tuple(
        SurfaceManifestEntry(
            name=name,
            kind=SurfaceKind.TOOL,
            owner=_TOOL_OWNER[name],
            pack=pack,
            lifecycle=(SurfaceLifecycle.DEPRECATED if name in _DEPRECATED_TOOLS else SurfaceLifecycle.ACTIVE),
            public=name not in _OPERATOR_ONLY_TOOLS,
            validation_reference=_MANIFEST_VALIDATION_REF,
        )
        for pack, tools in PACK_TOOLS.items()
        for name in tools
    )


#: The authoritative tool manifest: exactly one entry per registered tool.
TOOL_MANIFEST: tuple[SurfaceManifestEntry, ...] = _build_tool_manifest()
#: Name -> entry lookup for O(1) parity checks.
MANIFEST_BY_NAME: dict[str, SurfaceManifestEntry] = {e.name: e for e in TOOL_MANIFEST}

# =====================================================================
# FR02: versioned kernel digest
# =====================================================================

#: Current kernel manifest version. A kernel-membership change REQUIRES a bump
#: here plus a new pinned digest in ``KERNEL_VERSION_DIGESTS`` — otherwise the
#: FR02 acceptance test fails, forcing the versioned manifest diff the PRD
#: mandates (task-corpus regression + security review happen out of band).
KERNEL_VERSION: int = 1


def kernel_digest() -> str:
    """Deterministic digest of the current kernel membership (order-insensitive)."""
    return hashlib.sha256("\n".join(sorted(_KERNEL_TOOLS)).encode("utf-8")).hexdigest()


#: Pinned digest per kernel version. The pin for the CURRENT version is a
#: hardcoded literal (not a live call), so any membership mutation diverges from
#: the pin and fails the FR02 test until the version is bumped and re-pinned.
KERNEL_VERSION_DIGESTS: dict[int, str] = {
    1: "9997a48f81a04594b2bca455a92cdc38a2c9b7cfc9901e239c4152371d0becf7",
}

# =====================================================================
# FR04: standard default / explicit all resolution
# =====================================================================
# ``STANDARD_TASK_PACKS`` is imported from ``surface_packs`` (single source of
# truth). ``kernel`` is always implied; a task absent from the mapping — or
# mapped to no packs (``unknown``) — resolves to kernel only.


class ToolResolution(BaseModel):
    """Typed, explainable outcome of a tool-surface resolution (FR04)."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["standard", "all"]
    task_type: str | None
    packs: tuple[str, ...]
    tools: tuple[str, ...]
    decision: str
    explanation: tuple[str, ...]


def eligible_tool_names() -> tuple[str, ...]:
    """The full eligible (public) tool surface — what ``all`` mode exposes."""
    return tuple(e.name for e in TOOL_MANIFEST if e.public)


def resolve_tool_surface(task_type: str | None, mode: str = "standard") -> ToolResolution:
    """Resolve the tool surface for a task under a resolution mode (FR04).

    ``standard`` is the default and is bounded: a mapped task gets kernel plus
    its standard packs; a missing/unknown task gets kernel only (discovery is
    already kernel). Only an EXPLICIT ``all`` mode returns the full eligible
    surface, and the decision is recorded so the choice is visible. Any other
    mode value degrades to ``standard`` (never silently widens to full).
    """
    if mode == "all":
        tools = eligible_tool_names()
        packs = tuple(PACK_TOOLS.keys())
        return ToolResolution(
            mode="all",
            task_type=task_type,
            packs=packs,
            tools=tools,
            decision=(
                "explicit_all: operator selected the full eligible public "
                f"surface ({len(tools)} tools), subject to policy"
            ),
            explanation=(
                f"mode=all exposes every public tool ({len(tools)}); "
                "operator-only surfaces still require explicit grants",
            ),
        )

    selected = STANDARD_TASK_PACKS.get(task_type or "", ())
    packs = ("kernel", *selected)
    tools_list = [tool for pack in packs for tool in PACK_TOOLS[pack]]
    if selected:
        decision = f"standard: task '{task_type}' -> kernel + {', '.join(selected)}"
    else:
        decision = f"standard: task '{task_type}' unmapped -> kernel only"
    return ToolResolution(
        mode="standard",
        task_type=task_type,
        packs=packs,
        tools=tuple(tools_list),
        decision=decision,
        explanation=tuple(f"{pack}={len(PACK_TOOLS[pack])} tools" for pack in packs),
    )


# =====================================================================
# NFR04: measured reduction targets + per-miss EXPIRING exception records
# =====================================================================
#
# PRD-CORE-218 NFR04 sets numeric reduction targets for the public surface.
# The current census still exceeds every target, so completion is honest only if
# EACH missed metric carries a distinct operator-approved, UNEXPIRED exception
# (owner, rationale, expiry, reduction-plan pointer). We do NOT fake meeting the
# targets — the census reports the real overage and the active exception per
# miss so a reviewer sees the truth.

#: metric -> reduction target (PRD-CORE-218 NFR04). ``tools`` is the registered
#: public MCP tool surface (``len(TOOL_MANIFEST)``); ``skills`` is bundled skill
#: dirs; ``config_fields`` is TRWConfig top-level fields.
SURFACE_REDUCTION_TARGETS: dict[str, int] = {
    "tools": 36,
    "skills": 23,
    "config_fields": 370,
}


class SurfaceReductionException(BaseModel):
    """An operator-approved, EXPIRING exception for an unmet reduction target.

    NFR04 permits shipping above a target ONLY while a distinct exception like
    this is active per missed metric. It records the baseline census, the target,
    the accountable owner, the rationale, the expiry after which the miss blocks
    completion, and the pointer to the reduction plan.
    """

    model_config = ConfigDict(frozen=True)

    metric: str
    baseline: int
    target: int
    measured: int
    owner: str
    rationale: str
    expiry_iso: str
    reduction_plan_ref: str


#: One distinct exception per missed metric (PRD-CORE-218 NFR04). Baselines are
#: the PRD §5 committed receipt (45 tools / 29 skills / 436 fields); ``measured``
#: is the census at approval. Expiry is the PRD target completion — after it the
#: miss blocks completion (the NFR04 test enforces "unexpired").
SURFACE_REDUCTION_EXCEPTIONS: dict[str, SurfaceReductionException] = {
    "tools": SurfaceReductionException(
        metric="tools",
        baseline=45,
        target=36,
        measured=50,
        owner="framework-consolidation",
        rationale=(
            "Kernel + pack classification landed, but pack tools are still fully "
            "registered; net removal of ~14 tools is staged behind the FR07 "
            "skill/tool retirement queue and the trw_claude_md_sync alias removal."
        ),
        expiry_iso="2026-08-28",
        reduction_plan_ref="docs/requirements-aare-f/prds/PRD-CORE-218.md#8-rollout-plan",
    ),
    "skills": SurfaceReductionException(
        metric="skills",
        baseline=29,
        target=23,
        measured=27,
        owner="framework-consolidation",
        rationale=(
            "Duplicate-skill consolidation (FR07) flags near-duplicates but does "
            "not auto-merge; retiring the flagged skills to reach <=23 is a "
            "reversible lifecycle transition scheduled in the same wave."
        ),
        expiry_iso="2026-08-28",
        reduction_plan_ref="docs/requirements-aare-f/prds/PRD-CORE-218.md#8-rollout-plan",
    ),
    "config_fields": SurfaceReductionException(
        metric="config_fields",
        baseline=436,
        target=370,
        measured=434,
        owner="framework-consolidation",
        rationale=(
            "FR05 admission budget is enforced for NEW fields; collapsing ~64 "
            "existing top-level fields into nested policy/derived values is the "
            "consolidation task tracked by the admission-budget migration. The "
            "CORE-125 tool_exposure_mode/list removal at FR03/FR04 activation "
            "trimmed 2 (436->434)."
        ),
        expiry_iso="2026-08-28",
        reduction_plan_ref="docs/requirements-aare-f/prds/PRD-CORE-218.md#8-rollout-plan",
    ),
}


class SurfaceMetricStatus(BaseModel):
    """Honest per-metric census: is the target met, and if not, is a miss covered?"""

    model_config = ConfigDict(frozen=True)

    metric: str
    baseline: int
    target: int
    current: int
    met: bool
    exception_active: bool
    #: True iff the metric is met OR a currently-active exception covers the miss.
    #: A missed metric with no active exception is NOT reported honestly and must
    #: block completion (never a silent pass).
    reported_honestly: bool


def reduction_exception_active(metric: str, *, now: datetime | None = None) -> bool:
    """True when ``metric`` has a distinct, UNEXPIRED reduction exception."""
    exc = SURFACE_REDUCTION_EXCEPTIONS.get(metric)
    if exc is None:
        return False
    raw = exc.expiry_iso.strip()
    if not raw:
        return False
    try:
        expiry = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    reference = now if now is not None else datetime.now(tz=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference < expiry


def surface_reduction_census(
    *,
    tool_count: int | None = None,
    skill_count: int,
    config_field_count: int,
    now: datetime | None = None,
) -> dict[str, SurfaceMetricStatus]:
    """Report the honest reduction census for every NFR04 metric.

    ``tool_count`` defaults to the registered public MCP surface
    (``len(TOOL_MANIFEST)``). ``skill_count``/``config_field_count`` are supplied
    by the caller (the registry stays free of a config/data import). Each metric
    is met when ``current <= target``; a miss is reported honestly only when a
    currently-active exception covers it.
    """
    currents: dict[str, int] = {
        "tools": len(TOOL_MANIFEST) if tool_count is None else tool_count,
        "skills": skill_count,
        "config_fields": config_field_count,
    }
    census: dict[str, SurfaceMetricStatus] = {}
    for metric, target in SURFACE_REDUCTION_TARGETS.items():
        current = currents[metric]
        met = current <= target
        active = reduction_exception_active(metric, now=now)
        exc = SURFACE_REDUCTION_EXCEPTIONS.get(metric)
        census[metric] = SurfaceMetricStatus(
            metric=metric,
            baseline=exc.baseline if exc is not None else target,
            target=target,
            current=current,
            met=met,
            exception_active=active,
            reported_honestly=met or active,
        )
    return census
