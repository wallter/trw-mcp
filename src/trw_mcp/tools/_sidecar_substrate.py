"""Shared substrate for trw-distill sidecar-consuming MCP tools (PRD-DIST-1988, cycle 747).

Extracted from c746 ``before_edit_hint.py`` to support multiple
cross-package consumer tools without duplicating:

- Repo-root + git-SHA resolution
- Sidecar envelope load + schema validation
- Tier-gate decision

Three tools use this substrate as of c747:
- ``trw_before_edit_hint`` (c746) — single-file hint
- ``trw_before_edit_hint_batch`` (c747) — batch hint
- ``trw_codebase_risk_report`` (c747) — risk-report

IP boundary: trw-mcp is PUBLIC; trw-distill is PROPRIETARY. This
module imports neither — the cross-package contract is the c742
sidecar envelope ``risk-report-sidecar/v0``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION_ACCEPTED: str = "risk-report-sidecar/v0"
DEFAULT_CACHE_DIR_REL: str = ".trw/distill/map-cache"

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


def check_tier_for_feature(
    repo_root: Path | None,
    feature: str,
) -> TierGateResult:
    """Resolve entitlement + check if the given feature is enabled.

    NEVER raises. Returns ``allowed=False, tier="free"`` for any error path.
    """
    from trw_mcp.state._entitlements import load_entitlement
    from trw_mcp.state._paths import resolve_trw_dir

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
    cli_remediation: str,
    file_path_hint: str | None = None,
) -> SidecarLoadResult:
    """Load sidecar envelope + validate schema_version + SHA match.

    Returns ``SidecarLoadResult`` with payload populated only when all
    checks pass. NEVER raises.

    Args:
        sidecar_path: Path to the sidecar JSON.
        expected_sha: Current git HEAD SHA (caller verifies it matched).
        cli_remediation: Exact CLI to run to regenerate the sidecar.
        file_path_hint: Deprecated compatibility keyword; no longer needed for remediation.
    """
    _ = file_path_hint  # PRD-DIST-1988 compatibility; callers may still supply it.
    sidecar_path_str = str(sidecar_path)
    envelope = load_envelope(sidecar_path)
    if envelope is None:
        return SidecarLoadResult(
            payload=None,
            status="sidecar_missing",
            action=f"Run: {cli_remediation}",
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
    if not isinstance(sidecar_sha, str) or sidecar_sha != expected_sha:
        return SidecarLoadResult(
            payload=None,
            status="stale_sha",
            action=(f"Sidecar SHA={sidecar_sha!r}; HEAD={expected_sha} — re-run with --persist-sidecar"),
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    payload = envelope.get("payload")
    if payload is None:
        return SidecarLoadResult(
            payload=None,
            status="sidecar_malformed",
            action=f"Sidecar payload missing; re-run: {cli_remediation}",
            sidecar_path=sidecar_path_str,
            sidecar_sha=expected_sha,
        )
    return SidecarLoadResult(
        payload=payload,
        status="ok",
        action=None,
        sidecar_path=sidecar_path_str,
        sidecar_sha=expected_sha,
    )


def resolve_current_sidecar(
    *,
    repo_root: str | None,
    cache_dir: str | None,
    feature: str,
    artifact_name: str,
    cli_remediation: str,
) -> CurrentSidecarResult:
    """Resolve and load one tier-gated, SHA-pinned distill sidecar."""
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
        return CurrentSidecarResult(
            tier=gate.tier,
            payload=None,
            status="tier_required",
            action=tier_required_action(),
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
    )


__all__ = [
    "DEFAULT_CACHE_DIR_REL",
    "SCHEMA_VERSION_ACCEPTED",
    "TIER_REMEDIATION_URL",
    "CurrentSidecarResult",
    "CurrentSidecarStatus",
    "SidecarEnvelopeStatus",
    "SidecarLoadResult",
    "TierGateResult",
    "check_tier_for_feature",
    "load_envelope",
    "load_sidecar_with_sha_check",
    "resolve_current_sidecar",
    "resolve_git_sha",
    "resolve_repo_root",
    "tier_required_action",
]
