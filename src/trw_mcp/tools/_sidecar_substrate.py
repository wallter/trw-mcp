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

SidecarEnvelopeStatus = Literal[
    "ok",
    "sidecar_missing",
    "sidecar_malformed",
    "schema_mismatch",
    "stale_sha",
    "tier_required",
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
    file_path_hint: str,
    cli_remediation: str,
) -> SidecarLoadResult:
    """Load sidecar envelope + validate schema_version + SHA match.

    Returns ``SidecarLoadResult`` with payload populated only when all
    checks pass. NEVER raises.

    Args:
        sidecar_path: Path to the sidecar JSON.
        expected_sha: Current git HEAD SHA (caller verifies it matched).
        file_path_hint: Operator-visible file path for remediation strings.
        cli_remediation: Exact CLI to run to regenerate the sidecar.
    """
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


__all__ = [
    "DEFAULT_CACHE_DIR_REL",
    "SCHEMA_VERSION_ACCEPTED",
    "SidecarEnvelopeStatus",
    "SidecarLoadResult",
    "TierGateResult",
    "check_tier_for_feature",
    "load_envelope",
    "load_sidecar_with_sha_check",
    "resolve_git_sha",
    "resolve_repo_root",
]
