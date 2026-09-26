"""Single source of truth for the TRW capability-pack membership (PRD-CORE-218).

This module is deliberately *pure* — stdlib types only, zero imports — so every
consumer can read it without an import cycle or a side effect, and scripts load
it BY PATH (``scripts/print_reviewer_tools.py``, ``scripts/generate-inventory.py``)
without a ``trw_mcp`` on ``sys.path``:

* ``server/_surface_manifest_registry.py`` builds the authoritative
  ``SurfaceManifestEntry`` manifest + kernel digest pin from ``PACK_TOOLS`` /
  ``KERNEL_TOOLS`` here (FR01/FR02/FR04);
* ``models/config/_defaults.py`` re-exports ``KERNEL_TOOLS`` /
  ``CAPABILITY_PACKS`` (the non-kernel view).

The surface is flat (PRD-CORE-300 S11b): a session sees the kernel plus every
pack whose config flag is on. There is no per-task pack resolution, no
discoverable tier and no grant tool; a pack is either always on or gated by one
config flag (:data:`FLAG_GATED_PACKS`). Every registered tool belongs to exactly
one pack, so the FR01 manifest stays a bijection with the registrar.
"""

from __future__ import annotations

#: The universal kernel (FR02, version 4): always exposed, in every phase. It
#: equals ``surface_v2.POST_CUT_KERNEL`` (PRD-CORE-300 S11b), in the same order;
#: it is restated rather than imported only to keep this module import-free, and
#: ``tests/test_kernel_is_post_cut_kernel.py`` fails if the two ever differ, so
#: the kernel, S12's alwaysLoad floor and the selection eval cannot disagree. A
#: membership change is a versioned event: the registry's digest pin forces a
#: ``KERNEL_VERSION`` bump.
KERNEL_TOOLS: tuple[str, ...] = (
    "trw_session_start",
    "trw_init",
    "trw_status",
    "trw_recall",
    "trw_learn",
    "trw_checkpoint",
    "trw_deliver",
    "trw_build_check",
    "trw_review",
    "trw_prd_validate",
    "trw_code",
)

#: The non-kernel capability packs -> exact ordered tool IDs. This is the
#: complete registered surface beyond the kernel: every registered tool belongs
#: to exactly one pack, so the FR01 manifest stays a bijection with the
#: registrar. Lifecycle is decided by the registry, not here.
CAPABILITY_PACKS: dict[str, tuple[str, ...]] = {
    "dispatch": ("trw_dispatch",),
    # PRD-CORE-274 slice 1: gated by comms_enabled (default on). Kept out of the
    # reviewer profile. PRD-CORE-300 FR10 (S8) folded the peer-enrollment tool's
    # seven actions into trw_inbox's action parameter.
    "peer_comms": ("trw_send", "trw_inbox"),
    # trw-jev slice 1 (PRD-CORE-288): gated by default-off assess_enabled and
    # kept out of the reviewer profile — a reviewer lane must never gain a
    # network egress path the surface it is auditing did not already have.
    "assess_support": ("trw_assess",),
}

#: pack -> the ``TRWConfig`` flag that turns it on. A pack not named here is on
#: for every agent session. Each flag matches ``surface_v2.POST_CUT_FLAGGED`` for
#: the pack's surviving tools (a test ties the two).
FLAG_GATED_PACKS: dict[str, str] = {
    "peer_comms": "comms_enabled",
    "dispatch": "dispatch_tools_exposed",
    "assess_support": "assess_enabled",
}

#: pack -> tool IDs including the kernel modelled as a pack, so the manifest is
#: uniform (every registered tool belongs to exactly one pack). Derived — never
#: hand-maintained.
PACK_TOOLS: dict[str, tuple[str, ...]] = {
    "kernel": KERNEL_TOOLS,
    **CAPABILITY_PACKS,
}


def enabled_packs(
    mode: str = "standard",
    *,
    comms_enabled: bool = False,
    dispatch_enabled: bool = False,
    assess_enabled: bool = False,
) -> tuple[str, ...]:
    """The packs a session sees, in :data:`PACK_TOOLS` order (PRD-CORE-300 S11b).

    Every pack except a flag-gated pack whose flag is off. ``mode="all"`` also
    turns on the comms and assess packs but never the dispatch pack: process
    launching needs ``dispatch_tools_exposed`` in every mode (PRD-CORE-300 FR09).
    The one implementation of the rule; the registry resolver, the surface
    explanation and the middleware all go through it.
    """
    all_mode = mode == "all"
    flags = {
        "comms_enabled": comms_enabled or all_mode,
        "dispatch_tools_exposed": dispatch_enabled,
        "assess_enabled": assess_enabled or all_mode,
    }
    return tuple(pack for pack in PACK_TOOLS if flags.get(FLAG_GATED_PACKS.get(pack, ""), True))


#: Every registered tool no config flag gates: the kernel plus the always-on
#: packs. The surface every agent session sees whatever its flags.
ALWAYS_ON_TOOLS: frozenset[str] = frozenset(
    tool for pack, tools in PACK_TOOLS.items() if pack not in FLAG_GATED_PACKS for tool in tools
)

#: The read-only REVIEWER surface (PRD-SEC-015-FR01) — the single source of
#: truth for what a dispatched second-opinion lane may call. Read-report
#: tools drawn from :data:`KERNEL_TOOLS`. It shrank per slice (PRD-CORE-300
#: NFR02) and now equals ``surface_v2.POST_CUT_REVIEWER_TOOLS``. ``trw_code``'s
#: hint mode writes nothing under the reviewer role (PRD-CORE-300-FR12), so the
#: whole tool belongs here; the graph mode of ``trw_recall`` replaced the
#: standalone graph-traversal tool (FR11).
#:
#: This set REPLACES the resolved surface for a reviewer session; it is never
#: subtracted from the agent surface. That is load-bearing: the agent surface
#: grows by ordinary maintenance, and a subtractive design would silently
#: re-widen every reviewer surface on the next addition.
#:
#: Excluded, with the reason that settles each class:
#:   * ``trw_session_start`` / ``trw_learn`` /
#:     ``trw_checkpoint`` / ``trw_init`` — write the shared learnings store or
#:     run state a stateless reviewer does not own (the measured pollution
#:     path: 24 ``trw_deliver`` + 12 ``trw_build_check`` + 12 ``trw_learn``
#:     across 11 runs, 2026-08-27); the same reasoning excludes the CLI verbs
#:     PRD-CORE-300 S6b folded run-adoption and instructions-sync into
#:     (``run adopt`` transfers run ownership; ``instructions sync`` rewrites
#:     hand-written operating rules, 128 lines / 9 sections truncated
#:     2026-07-23) — both are ``state_changing`` entries the shared CLI guard
#:     already refuses under ``TRW_SURFACE_ROLE=reviewer``;
#:   * ``trw_build_check`` / ``trw_review`` / ``trw_deliver`` — verdict and gate
#:     machinery; a lane that ran no build must not fabricate its evidence;
#:   * the ``dispatch`` pack — recursion (a reviewer spawning reviewers);
#:   * ``trw_status`` — an unpinned child resolves through ``find_active_run``
#:     to ANOTHER agent's run and increments a persisted ceremony counter;
#:   * ``trw_prd_validate`` — read-only in name only: it advances the ACTIVE
#:     run's phase to PLAN, so in an unpinned child it is a cross-session write.
REVIEWER_TOOLS: frozenset[str] = frozenset(
    {
        "trw_recall",
        "trw_code",
    }
)


def reviewer_tools_toml_array() -> str:
    """Render :data:`REVIEWER_TOOLS` as a TOML array literal (PRD-SEC-015-FR01).

    The ONE rendering of the reviewer set for the Codex ``-c
    mcp_servers.trw.enabled_tools=[...]`` allowlist layer. Three consumers read
    it — ``dispatch/_posture.py`` (the ``posture="reviewer"`` argv renderer),
    ``scripts/print_reviewer_tools.py`` (the shell audit lane), and through that
    script ``scripts/audit-external.sh`` — so no layer can disagree with the
    server-side bound the way a generated client config and the server surface
    do. Tool ids are ``[a-z_]`` only, so plain double quotes are exact TOML —
    and exact JSON too, which is what lets the same rendering serve the claude
    ``--mcp-config`` payload. The output round-trips through ``tomllib`` to
    ``sorted(REVIEWER_TOOLS)``.
    """
    return "[" + ", ".join(f'"{name}"' for name in sorted(REVIEWER_TOOLS)) + "]"


__all__ = [
    "ALWAYS_ON_TOOLS",
    "CAPABILITY_PACKS",
    "FLAG_GATED_PACKS",
    "KERNEL_TOOLS",
    "PACK_TOOLS",
    "REVIEWER_TOOLS",
    "enabled_packs",
    "reviewer_tools_toml_array",
]
