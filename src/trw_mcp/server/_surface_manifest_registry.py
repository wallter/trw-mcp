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
from trw_mcp.models.surface_packs import FLAG_GATED_PACKS, KERNEL_TOOLS, enabled_packs
from trw_mcp.models.surface_packs import PACK_TOOLS as PACK_TOOLS

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
    validation_reference: str


# =====================================================================
# FR02: exact stable minimal kernel + capability packs
# =====================================================================
#
# ``_KERNEL_TOOLS``, ``PACK_TOOLS`` and ``FLAG_GATED_PACKS`` are imported at the
# top of this module from ``trw_mcp.models.surface_packs`` — the single source of
# truth both this registry and ``models/config/_defaults`` read. Kernel changes
# are a versioned event enforced by the pinned digest below.

#: Owning module (single-writer authority) per tool, from the live
#: registrar-to-tool mapping in ``server/_tools.py``.
_TOOL_OWNER: dict[str, str] = {
    "trw_send": "tools.swarm_comms",
    "trw_inbox": "tools.swarm_comms",
    "trw_assess": "tools.decision",
    "trw_session_start": "tools.ceremony",
    "trw_deliver": "tools.ceremony",
    "trw_status": "tools.orchestration",
    "trw_init": "tools.orchestration",
    "trw_checkpoint": "tools.orchestration",
    "trw_recall": "tools.learning",
    "trw_learn": "tools.learning",
    "trw_build_check": "tools.build",
    "trw_review": "tools.review",
    "trw_prd_validate": "tools.requirements",
    "trw_code": "tools.code",
    "trw_dispatch": "tools.dispatch",
}

#: Deprecated tools (still registered, in the removal queue). Empty since the
#: former deprecated alias of the instructions-sync tool (PRD-CORE-218 §4) was
#: deleted outright (PRD-CORE-300 S6c) rather than merely marked deprecated.
_DEPRECATED_TOOLS: frozenset[str] = frozenset()

_MANIFEST_VALIDATION_REF = "trw-mcp/tests/test_tool_presets.py::test_prd_core_218_fr01"


def _build_tool_manifest() -> tuple[SurfaceManifestEntry, ...]:
    return tuple(
        SurfaceManifestEntry(
            name=name,
            kind=SurfaceKind.TOOL,
            owner=_TOOL_OWNER[name],
            pack=pack,
            lifecycle=(SurfaceLifecycle.DEPRECATED if name in _DEPRECATED_TOOLS else SurfaceLifecycle.ACTIVE),
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
KERNEL_VERSION: int = 4


def kernel_digest() -> str:
    """Deterministic digest of the current kernel membership (order-insensitive)."""
    return hashlib.sha256("\n".join(sorted(_KERNEL_TOOLS)).encode("utf-8")).hexdigest()


#: Pinned digest per kernel version. The pin for the CURRENT version is a
#: hardcoded literal (not a live call), so any membership mutation diverges from
#: the pin and fails the FR02 test until the version is bumped and re-pinned.
KERNEL_VERSION_DIGESTS: dict[int, str] = {
    1: "9997a48f81a04594b2bca455a92cdc38a2c9b7cfc9901e239c4152371d0becf7",
    2: "769ed2c0b3e39adfd6776781b5ad4d8a850bfa6f090b8d89f955fa0ce08bab23",
    # PRD-CORE-291-FR02: trw_learn_update merged into trw_learn, so the kernel is
    # back to version 1's membership (same digest, new version).
    3: "9997a48f81a04594b2bca455a92cdc38a2c9b7cfc9901e239c4152371d0becf7",
    # PRD-CORE-300 S11b: the kernel becomes surface_v2.POST_CUT_KERNEL. It gains
    # trw_init, trw_build_check, trw_review, trw_prd_validate and trw_code, and
    # loses the three meta tools (skill discovery, the access grant, profile explain).
    # The only re-pin of the 7.0.0 cut; the digest covers trw_code before S10
    # registers it, so S10 does not move the kernel again.
    4: "89ad3b388deaced1502e2d7eed347a4b3f83537d03bf58ce9654df6ae8484dce",
}

# =====================================================================
# FR04: standard default / explicit all resolution
# =====================================================================
# The surface is flat (PRD-CORE-300 S11b): kernel plus every pack whose config
# flag is on. There is no per-task resolution.


class ToolResolution(BaseModel):
    """Typed, explainable outcome of a tool-surface resolution (FR04)."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["standard", "all"]
    packs: tuple[str, ...]
    tools: tuple[str, ...]
    decision: str
    explanation: tuple[str, ...]


def eligible_tool_names() -> tuple[str, ...]:
    """Every registered tool: the manifest, whatever the mode or flags."""
    return tuple(e.name for e in TOOL_MANIFEST)


def resolve_tool_surface(
    mode: str = "standard",
    *,
    comms_enabled: bool = False,
    dispatch_enabled: bool = False,
    assess_enabled: bool = False,
) -> ToolResolution:
    """Resolve the tool surface under a resolution mode (FR04).

    ``standard`` (the default) is every pack except a flag-gated pack whose flag
    is off (:data:`FLAG_GATED_PACKS`: ``comms_enabled`` -> ``peer_comms``,
    ``dispatch_tools_exposed`` -> ``dispatch``, ``assess_enabled`` ->
    ``assess_support``). It does not depend on the task or the phase. An
    EXPLICIT ``all`` also turns on the comms and assess packs, but never the
    dispatch pack: process launching needs ``dispatch_tools_exposed`` in every
    mode (PRD-CORE-300 FR09). Any other mode value degrades to ``standard``.
    """
    resolved_mode: Literal["standard", "all"] = "all" if mode == "all" else "standard"
    packs = enabled_packs(
        resolved_mode,
        comms_enabled=comms_enabled,
        dispatch_enabled=dispatch_enabled,
        assess_enabled=assess_enabled,
    )
    off = tuple(pack for pack in PACK_TOOLS if pack not in packs)
    decision = (
        "explicit_all: every registered tool except packs still off"
        if resolved_mode == "all"
        else "standard: kernel + every pack whose flag is on"
    )
    if off:
        decision += "; off: " + ", ".join(f"{pack} ({FLAG_GATED_PACKS[pack]}=false)" for pack in off)
    return ToolResolution(
        mode=resolved_mode,
        packs=packs,
        tools=tuple(tool for pack in packs for tool in PACK_TOOLS[pack]),
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
#:
#: Renewed 2026-09-03 (feedback-triage-framework-release-2026-09 campaign): the
#: 2026-08-28 expiry lapsed with the phase-3 subtraction (FR07 skill/tool
#: retirement queue) still not scheduled — none of the three metrics regressed
#: because of new *unbounded* growth, so a fresh 90-day exception is the honest
#: record rather than a retroactive one. Owner + review cadence unchanged.
SURFACE_REDUCTION_EXCEPTIONS: dict[str, SurfaceReductionException] = {
    "skills": SurfaceReductionException(
        metric="skills",
        baseline=29,
        target=23,
        measured=26,
        owner="framework-consolidation",
        rationale=(
            "Renewed 2026-09-03: duplicate-skill consolidation (FR07) flags "
            "near-duplicates but does not auto-merge; retiring the flagged "
            "skills to reach <=23 is a reversible lifecycle transition still "
            "scheduled behind the same FR07 wave as the tools metric."
        ),
        expiry_iso="2026-12-02",
        reduction_plan_ref="docs/requirements-aare-f/prds/PRD-CORE-218.md#8-rollout-plan",
    ),
    "config_fields": SurfaceReductionException(
        metric="config_fields",
        baseline=436,
        target=370,
        measured=402,
        owner="framework-consolidation",
        rationale=(
            "Renewed 2026-09-03: FR05 admission budget is enforced for NEW "
            "fields; collapsing existing top-level fields into nested "
            "policy/derived values is the consolidation task tracked by the "
            "admission-budget migration, which has not landed. No new fields "
            "were admitted by the 2026-09-03 feedback-triage campaign outside "
            "the FR05 budget."
        ),
        expiry_iso="2026-12-02",
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
