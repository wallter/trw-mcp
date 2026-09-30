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
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from trw_mcp.tools._sidecar_pair import pair_from_two_builds, pair_stale_action

if TYPE_CHECKING:
    from trw_mcp.tools._sidecar_ancestry import AncestorSidecar, GitReader

SCHEMA_VERSION_ACCEPTED: str = "risk-report-sidecar/v0"
DEFAULT_CACHE_DIR_REL: str = ".trw/distill/map-cache"
#: The only artifact an ancestor may answer for: it carries one hint per target.
ANCESTOR_ARTIFACT: str = "before-edit-batch"


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


SidecarEnvelopeStatus = Literal[
    "ok",
    "sidecar_missing",
    "sidecar_malformed",
    "schema_mismatch",
    "stale_sha",
    "tier_required",
]
CurrentSidecarStatus = Literal[
    "hint_available",
    # hint_sidecar_ancestor_enabled: a proven-ancestor sidecar, served "as of" its sha.
    "hint_available_stale",
    # hint_sidecar_ancestor_enabled: sidecars exist, none a proven ancestor within the bound.
    "sidecar_too_far_behind",
    # hint_sidecar_ancestor_enabled: git could not prove ancestry or list the changes since.
    "sidecar_diff_failed",
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
class SidecarLoadResult:
    """Outcome of a sidecar load + envelope validation."""

    payload: Any | None
    status: SidecarEnvelopeStatus
    action: str | None
    sidecar_path: str | None
    sidecar_sha: str | None
    #: The envelope's ``dirty_paths`` exactly as read (unvalidated), set only on ``ok``.
    dirty_paths: object = None


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
    #: Set only with ``hint_available_stale``: the ancestor that answered.
    ancestor: AncestorSidecar | None = None
    #: The resolved repository root, for callers that ask git a follow-up question.
    repo_root: Path | None = None


def resolve_repo_root(repo_root: str | None) -> Path | None:
    """Best-effort repo-root resolution (caller arg → git rev-parse)."""
    if repo_root is not None:
        return Path(repo_root)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            stripped = proc.stdout.strip()
            if stripped:
                return Path(stripped)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def resolve_git_sha(repo_root: Path) -> str | None:
    """Best-effort ``git rev-parse HEAD`` with validation (40-char hex)."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0:
            return None
        stripped = proc.stdout.strip()
        if len(stripped) == 40 and all(c in "0123456789abcdef" for c in stripped):
            return stripped
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


#: What to say when an artifact has no producer at all. Distinct from a
#: remediation the caller can act on — telling someone to run a command
#: that does not exist is worse than telling them nothing.
_NO_PRODUCER_ACTION = "No producer exists for this sidecar yet, so it cannot be generated (see DEFECT-LEDGER UF-011)."


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


def load_envelope(sidecar_path: Path) -> dict[str, Any] | None:
    """Read + JSON-parse sidecar; return None on missing/malformed."""
    if not sidecar_path.exists():
        return None
    try:
        parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def load_sidecar_with_sha_check(
    sidecar_path: Path,
    *,
    expected_sha: str,
    cli_remediation: str | None,
    file_path_hint: str | None = None,
) -> SidecarLoadResult:
    """Load sidecar envelope + validate schema_version + SHA match.

    Returns ``SidecarLoadResult`` with payload populated only when all
    checks pass. NEVER raises.

    Args:
        sidecar_path: Path to the sidecar JSON.
        expected_sha: Current git HEAD SHA (caller verifies it matched).
        cli_remediation: Exact CLI to run to regenerate the sidecar, or None
            when no producer exists for this artifact. None is not a
            convenience default. `trw_entity_risk_map` (since REMOVED, UF-011) advertised a
            `trw-distill self-improve` subcommand that has never been
            registered, so every caller at every tier was told to run something
            that could not work (DEFECT-LEDGER UF-011). This parameter has to be
            able to say "there is nothing to run", or the only way to satisfy
            its type is to invent a command.

            Note the deliberate omission above: the missing subcommand is NOT
            named here. `wiring/checks/existence.py::_check_sidecar` decides a
            producer exists if any non-consumer source file under the search
            roots contains the contract's `producer_token`, so writing that
            token into this docstring silently cleared the CONSUMER_ORPHAN
            finding for it — prose disabling a truthfulness gate, which is the
            exact defect class the gate exists to catch.
        file_path_hint: Deprecated compatibility keyword; no longer needed for remediation.
    """
    _ = file_path_hint  # PRD-DIST-1988 compatibility; callers may still supply it.
    sidecar_path_str = str(sidecar_path)
    envelope = load_envelope(sidecar_path)
    if envelope is None:
        return SidecarLoadResult(
            payload=None,
            status="sidecar_missing",
            action=(f"Run: {cli_remediation}" if cli_remediation else _NO_PRODUCER_ACTION),
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    schema = envelope.get("schema_version")
    if schema != SCHEMA_VERSION_ACCEPTED:
        return SidecarLoadResult(
            payload=None,
            status="schema_mismatch",
            action=(
                f"Sidecar schema_version={schema!r}; expected "
                f"{SCHEMA_VERSION_ACCEPTED!r} — upgrade trw-distill or trw-mcp"
            ),
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    sidecar_sha = envelope.get("sha")
    stale: str | None = None
    if not isinstance(sidecar_sha, str) or sidecar_sha != expected_sha:
        stale = f"Sidecar SHA={sidecar_sha!r}; HEAD={expected_sha} — re-run with --persist-sidecar"
    elif pair_from_two_builds(sidecar_path, envelope, load_envelope):
        stale = pair_stale_action(expected_sha, cli_remediation, _NO_PRODUCER_ACTION)
    if stale is not None:
        return SidecarLoadResult(
            payload=None,
            status="stale_sha",
            action=stale,
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    payload = envelope.get("payload")
    if payload is None:
        return SidecarLoadResult(
            payload=None,
            status="sidecar_malformed",
            action=(
                f"Sidecar payload missing; re-run: {cli_remediation}"
                if cli_remediation
                else f"Sidecar payload missing. {_NO_PRODUCER_ACTION}"
            ),
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    return SidecarLoadResult(
        payload=payload,
        status="ok",
        action=None,
        sidecar_path=sidecar_path_str,
        sidecar_sha=expected_sha,
        dirty_paths=envelope.get("dirty_paths"),
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
    resolved_repo_root = resolve_repo_root(repo_root)
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

    git_sha = resolve_git_sha(resolved_repo_root)
    if git_sha is None:
        return CurrentSidecarResult(
            tier=gate.tier,
            payload=None,
            status="no_git_sha",
            action="Could not run `git rev-parse HEAD` — verify .git/ present",
        )

    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else resolved_repo_root / DEFAULT_CACHE_DIR_REL
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
        )
    load = load_sidecar_with_sha_check(
        sidecar_path,
        expected_sha=git_sha,
        cli_remediation=cli_remediation,
    )
    return CurrentSidecarResult(
        tier=gate.tier,
        payload=load.payload,
        status="hint_available" if load.status == "ok" else load.status,
        action=load.action,
        sidecar_path=load.sidecar_path,
        sidecar_sha=load.sidecar_sha,
        sidecar_existed=sidecar_existed,
        repo_root=resolved_repo_root,
    )


def _resolve_ancestor(
    *,
    repo_root: Path,
    search_dir: Path | None,
    head: str,
    tier: str,
    bound: int,
    git_reader: GitReader | None,
    cli_remediation: str | None,
    persist: bool,
) -> CurrentSidecarResult:
    """No exact-HEAD artifact: answer from the nearest proven-ancestor batch sidecar, or say why not."""
    from trw_mcp.tools import _sidecar_ancestry as ancestry

    cache_dir = search_dir or ancestry.shared_cache_dir(repo_root, DEFAULT_CACHE_DIR_REL)
    git = git_reader or ancestry.SubprocessGitReader(repo_root)
    run = f"run: {cli_remediation}" if cli_remediation else _NO_PRODUCER_ACTION
    try:
        outcome = ancestry.find_ancestor_sidecar(cache_dir, head, git=git, max_commits_behind=bound, persist=persist)
    except ancestry.GitReadError as err:
        outcome = ancestry.AncestryOutcome(status="git_failed", reason=str(err))
    base = CurrentSidecarResult(
        tier=tier, payload=None, status="sidecar_missing", sidecar_sha=head, repo_root=repo_root
    )
    if outcome.status == "no_candidates":
        return replace(base, action=f"Run: {cli_remediation}" if cli_remediation else _NO_PRODUCER_ACTION)
    if outcome.status == "git_failed":
        return replace(
            base,
            status="sidecar_diff_failed",
            action=f"Could not compare cached sidecars with HEAD ({outcome.reason}); learnings only ({ancestry.FLAG_DISABLE})",
        )
    if outcome.status == "too_far_behind" or outcome.ancestor is None:
        nearest = outcome.nearest_commits_behind
        where = (
            "no cached sidecar is an ancestor of HEAD"
            if nearest is None
            else f"the nearest is {nearest} commits behind"
        )
        return replace(
            base,
            status="sidecar_too_far_behind",
            action=f"No sidecar within hint_sidecar_max_commits_behind={bound} ({where}); {run} ({ancestry.FLAG_DISABLE})",
        )
    return _load_ancestor(base, outcome.ancestor, cli_remediation)


def _load_ancestor(
    base: CurrentSidecarResult, ancestor: AncestorSidecar, cli_remediation: str | None
) -> CurrentSidecarResult:
    """Validate the chosen ancestor's envelope; a corrupt file is ``sidecar_malformed``, never "missing"."""
    from trw_mcp.tools._sidecar_ancestry import parse_dirty_paths

    load = load_sidecar_with_sha_check(ancestor.path, expected_sha=ancestor.sha, cli_remediation=cli_remediation)
    located = replace(base, sidecar_path=str(ancestor.path), sidecar_existed=True)
    if load.status == "ok":
        dirty = parse_dirty_paths(load.dirty_paths)
        if dirty is None:
            action = f"Sidecar dirty_paths is not a list of paths; rebuild {ancestor.path.name}"
            return replace(located, status="sidecar_malformed", action=action)
        fresh = ancestor.commits_behind == 0
        return replace(
            located,
            payload=load.payload,
            status="hint_available" if fresh else "hint_available_stale",
            sidecar_sha=ancestor.sha,
            ancestor=None if fresh else replace(ancestor, dirty_paths=dirty),
        )
    status: CurrentSidecarStatus = "sidecar_malformed" if load.status == "sidecar_missing" else load.status
    return replace(located, status=status, action=load.action)


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
    "tier_required_action",
]
