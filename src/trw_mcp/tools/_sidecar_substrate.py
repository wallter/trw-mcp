"""Shared substrate for trw-distill sidecar-consuming MCP tools (PRD-DIST-1988, cycle 747).

Extracted from c746 ``before_edit_hint.py`` to support multiple
cross-package consumer tools without duplicating:

- Repo-root + git-SHA resolution
- Sidecar envelope load + schema validation
- Tier-gate decision

Three tools use this substrate as of c747:
- ``trw_code`` hint mode (c746) — single-file hint, batch via ``files=[...]``
- the codebase-risk-report engine (c747; ``trw-mcp code risk`` CLI as of
  PRD-CORE-300 slice S4) — risk-report

IP boundary: trw-mcp is PUBLIC; trw-distill is PROPRIETARY. This
module imports neither — the cross-package contract is the c742
sidecar envelope ``risk-report-sidecar/v0``.
"""

from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from trw_mcp.tools._sidecar_envelope import (
    ANCESTOR_ARTIFACT as ANCESTOR_ARTIFACT,
)
from trw_mcp.tools._sidecar_envelope import (
    DEFAULT_CACHE_DIR_REL as DEFAULT_CACHE_DIR_REL,
)
from trw_mcp.tools._sidecar_envelope import (
    SCHEMA_VERSION_ACCEPTED as SCHEMA_VERSION_ACCEPTED,
)
from trw_mcp.tools._sidecar_envelope import (
    SidecarEnvelopeStatus as SidecarEnvelopeStatus,
)
from trw_mcp.tools._sidecar_envelope import (
    SidecarLoadResult as SidecarLoadResult,
)
from trw_mcp.tools._sidecar_envelope import (
    load_envelope as load_envelope,
)
from trw_mcp.tools._sidecar_envelope import (
    load_sidecar_with_sha_check as load_sidecar_with_sha_check,
)
from trw_mcp.tools._sidecar_paths import (
    PROBE_BUDGET_S,
    cache_safety,
)
from trw_mcp.tools._sidecar_paths import (
    cache_is_safe as cache_is_safe,
)
from trw_mcp.tools._sidecar_paths import (
    resolve_git_sha as resolve_git_sha,
)
from trw_mcp.tools._sidecar_paths import (
    resolve_repo_root as resolve_repo_root,
)
from trw_mcp.tools._sidecar_paths import (
    shared_cache_dir as shared_cache_dir,
)

if TYPE_CHECKING:
    from trw_mcp.tools._sidecar_ancestry import AncestorSidecar, GitReader


def distill_installed() -> bool:
    """True when the proprietary ``trw-distill`` package is importable.

    Presence is treated as proof of entitlement: ``trw-distill`` is only
    installable via a valid backend proprietary license (the installer's
    ``--with-proprietary`` path), so if it is on the import path the operator
    is entitled to the distill-sidecar feature. This closes a real gap — the
    installer provisions the proprietary package but NEVER writes the
    ``.trw/entitlements.yaml`` sentinel, so an entitled install otherwise
    resolved ``tier="free"`` and was shown a paid-tier remediation on every
    edit (2026-07-19).

    IP boundary: uses :func:`importlib.util.find_spec` — it never IMPORTS
    ``trw_distill`` (this module must not import the proprietary package).
    """
    try:
        return importlib.util.find_spec("trw_distill") is not None
    except (ImportError, ValueError):
        return False


# Single source of truth for the operator remediation shown when the
# distill-sidecar feature is tier-gated. Shared by all six sidecar-consuming
# tools so the message + URL never drift (production feedback
# sub_Y-f6QQ3Y_Os9b0vM: the old string pointed at a dead /tier URL and was
# duplicated across the tools). The real marketing page is /pricing.
TIER_REMEDIATION_URL: str = "https://trwframework.com/pricing"


def tier_required_action() -> str:
    """Operator remediation for a tier-gated distill-sidecar feature.

    Tier-aware: the default path points paid tiers at ``/pricing``; a single
    trailing sentence tells beta testers they can enable it via the tester
    program (which provisions a ``beta`` entitlement — see
    ``state/_entitlements.py``).
    """
    return (
        "Acquire team/pro/enterprise tier to enable trw-distill sidecar "
        f"consumption (see {TIER_REMEDIATION_URL}). Beta testers can enable "
        "it via the TRW tester program."
    )


CurrentSidecarStatus = Literal[
    "hint_available",
    # hint_sidecar_ancestor_enabled: a proven-ancestor sidecar, served "as of" its sha.
    "hint_available_stale",
    # hint_sidecar_ancestor_enabled: sidecars exist, none a proven ancestor within the bound.
    "sidecar_too_far_behind",
    # hint_sidecar_ancestor_enabled: git could not prove ancestry or list the changes since.
    "sidecar_diff_failed",
    # A git probe ran out of the lookup's budget: nothing is known, so nothing is advised.
    "sidecar_check_timed_out",
    "sidecar_missing",
    "sidecar_malformed",
    "schema_mismatch",
    "stale_sha",
    "tier_required",
    "no_repo_root",
    "no_git_sha",
]


@dataclass(frozen=True)
class TierGateResult:
    """Outcome of a tier check for a specific feature."""

    allowed: bool
    tier: str
    reason: str  # "ok" | "tier_required" | entitlement-reason


@dataclass(frozen=True)
class CurrentSidecarResult:
    """Shared repo, entitlement, SHA, and sidecar-load outcome."""

    tier: str
    payload: Any | None
    status: CurrentSidecarStatus
    action: str | None = None
    sidecar_path: str | None = None
    sidecar_sha: str | None = None
    sidecar_existed: bool = False
    #: Snapshot provenance, including exact-HEAD hints for dirty-path filtering.
    ancestor: AncestorSidecar | None = None
    #: The resolved repository root, for callers that ask git a follow-up question.
    repo_root: Path | None = None
    #: ``time.monotonic`` instant this lookup's one-second git budget ends; later probes share it.
    deadline: float | None = None


def check_tier_for_feature(
    repo_root: Path | None,
    feature: str,
) -> TierGateResult:
    """Resolve entitlement + check if the given feature is enabled.

    NEVER raises. Returns ``allowed=False, tier="free"`` for any error path.

    Entitlement resolves in two ways (either grants the feature):
    1. ``trw-distill`` is installed — proof of a paid entitlement (see
       :func:`distill_installed`). This is the PRIMARY path: it self-heals
       every entitled install whose ``.trw/entitlements.yaml`` the installer
       never wrote.
    2. A valid ``.trw/entitlements.yaml`` sentinel grants ``feature`` — the
       explicit, expiry-bearing path (``trw-mcp tier issue``).
    """
    from trw_mcp.state._entitlements import load_entitlement
    from trw_mcp.state._paths import resolve_trw_dir

    if distill_installed():
        return TierGateResult(allowed=True, tier="proprietary", reason="distill_installed")

    trw_dir = (repo_root / ".trw") if repo_root is not None else resolve_trw_dir()
    entitlement = load_entitlement(trw_dir)
    if entitlement.has_feature(feature):
        return TierGateResult(allowed=True, tier=entitlement.tier, reason="ok")
    return TierGateResult(
        allowed=False,
        tier=entitlement.tier,
        reason=entitlement.reason,
    )


def resolve_current_sidecar(
    *,
    repo_root: str | None,
    cache_dir: str | None,
    feature: str,
    artifact_name: str,
    cli_remediation: str | None,
    ancestor_bound: int | None = None,
    git_reader: GitReader | None = None,
    persist_ancestry: bool = True,
) -> CurrentSidecarResult:
    """Resolve and load one tier-gated, SHA-pinned distill sidecar.

    ``ancestor_bound`` is ``hint_sidecar_max_commits_behind`` when the caller's
    ``hint_sidecar_ancestor_enabled`` is on, else None. When set and no
    exact-HEAD artifact exists, the nearest proven-ancestor batch sidecar
    within that many commits answers as ``hint_available_stale``. None keeps
    the exact-HEAD-only lookup unchanged. ``git_reader`` is the test seam.
    ``persist_ancestry=False`` (the reviewer role) computes ancestry in memory
    and writes no cache file.
    """
    deadline = time.monotonic() + PROBE_BUDGET_S  # one budget, started before the first probe (root discovery)
    resolved_repo_root = resolve_repo_root(repo_root, deadline)
    if resolved_repo_root is None:
        return CurrentSidecarResult(
            tier="free",
            payload=None,
            status="no_repo_root",
            action="Pass --repo or run from inside a git checkout",
        )

    gate = check_tier_for_feature(resolved_repo_root, feature)
    if not gate.allowed:
        # Not allowed now means trw-distill is NOT installed AND no sentinel
        # grants the feature — the sidecar cannot exist, so the feature is
        # simply unavailable. Return quietly (action=None): do NOT emit the
        # tier-remediation nag, which would burn tokens on every call for a
        # proprietary feature the operator has not opted into (2026-07-19).
        return CurrentSidecarResult(
            tier=gate.tier,
            payload=None,
            status="tier_required",
            action=None,
        )

    git_sha = resolve_git_sha(resolved_repo_root, deadline)
    if git_sha is None:
        return CurrentSidecarResult(
            tier=gate.tier,
            payload=None,
            status="no_git_sha",
            action="Could not run `git rev-parse HEAD` — verify .git/ present",
        )

    from trw_mcp.tools._sidecar_ancestor_fallback import _resolve_ancestor
    from trw_mcp.tools._sidecar_ancestry import AncestorSidecar, parse_dirty_paths

    resolved_cache_dir = (
        Path(cache_dir) if cache_dir is not None else shared_cache_dir(resolved_repo_root, DEFAULT_CACHE_DIR_REL)
    )
    safety = cache_safety(resolved_cache_dir, resolved_repo_root, deadline)
    if safety != "safe":
        timed_out = safety == "unknown"
        return CurrentSidecarResult(
            tier=gate.tier,
            payload=None,
            status="sidecar_check_timed_out" if timed_out else "sidecar_missing",
            action="Could not check the sidecar cache (git timed out); learnings only" if timed_out else None,
            repo_root=resolved_repo_root,
        )
    sidecar_path = resolved_cache_dir / f"{artifact_name}-{git_sha}.json"
    sidecar_existed = sidecar_path.exists()
    if ancestor_bound is not None and artifact_name != ANCESTOR_ARTIFACT:
        raise ValueError(f"ancestor_bound applies only to {ANCESTOR_ARTIFACT!r}, not {artifact_name!r}")
    if ancestor_bound is not None and not sidecar_existed:
        search_dir = Path(cache_dir) if cache_dir is not None else None
        return _resolve_ancestor(
            repo_root=resolved_repo_root,
            search_dir=search_dir,
            head=git_sha,
            tier=gate.tier,
            bound=ancestor_bound,
            git_reader=git_reader,
            cli_remediation=cli_remediation,
            persist=persist_ancestry,
            deadline=deadline,
        )
    load = load_sidecar_with_sha_check(
        sidecar_path,
        expected_sha=git_sha,
        cli_remediation=cli_remediation,
    )
    if ancestor_bound is not None and load.status != "ok":
        return _resolve_ancestor(
            repo_root=resolved_repo_root,
            search_dir=resolved_cache_dir,
            head=git_sha,
            tier=gate.tier,
            bound=ancestor_bound,
            git_reader=git_reader,
            cli_remediation=cli_remediation,
            persist=persist_ancestry,
            deadline=deadline,
        )
    ancestor = None
    if load.status == "ok" and artifact_name in (ANCESTOR_ARTIFACT, "before-edit-hint"):
        dirty = parse_dirty_paths(load.dirty_paths)
        if dirty is None:
            return CurrentSidecarResult(
                tier=gate.tier, payload=None, status="sidecar_malformed", repo_root=resolved_repo_root
            )
        ancestor = AncestorSidecar(git_sha, sidecar_path, 0, dirty_paths=dirty)
    return CurrentSidecarResult(
        ancestor=ancestor,
        tier=gate.tier,
        payload=load.payload,
        status="hint_available" if load.status == "ok" else load.status,
        action=load.action,
        sidecar_path=load.sidecar_path,
        sidecar_sha=load.sidecar_sha,
        sidecar_existed=sidecar_existed,
        repo_root=resolved_repo_root,
        deadline=deadline,
    )


__all__ = [
    "ANCESTOR_ARTIFACT",
    "DEFAULT_CACHE_DIR_REL",
    "SCHEMA_VERSION_ACCEPTED",
    "TIER_REMEDIATION_URL",
    "CurrentSidecarResult",
    "CurrentSidecarStatus",
    "SidecarEnvelopeStatus",
    "SidecarLoadResult",
    "TierGateResult",
    "check_tier_for_feature",
    "distill_installed",
    "load_envelope",
    "load_sidecar_with_sha_check",
    "resolve_current_sidecar",
    "resolve_git_sha",
    "resolve_repo_root",
    "shared_cache_dir",
    "tier_required_action",
]
