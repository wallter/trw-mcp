"""trw_before_edit_hint MCP tool (PRD-DIST-1983 + PRD-DIST-1984, cycle 746).

Surfaces cold-start codebase intelligence to any MCP client without
requiring the client to shell out to the trw-distill CLI.

Two-source composition (PRD-DIST-1984):
- **Primary**: c742/c743 trw-distill sidecar at
  ``<cache_dir>/before-edit-hint-<sha>.json`` (tier-gated;
  ``trw_before_edit_hint:distill_sidecar`` feature flag).
- **Secondary**: trw_recall over existing learnings keyed on the
  file path / basename (always available, no tier gate). Returns top-N
  relevant learnings even when the distill sidecar is absent.

IP boundary (trw-distill is PROPRIETARY; trw-mcp is PUBLIC):
- This module MUST NOT import ``trw_distill``. The cross-package
  contract is the sidecar envelope ``risk-report-sidecar/v0`` —
  field-by-field Pydantic mirror via :class:`BeforeYouEditHintPayload`.

Honest scope per CONSTITUTION §1:
- v0 reads only the single-file ``before-edit-hint-<sha>.json`` artifact
  (c743). Batch artifact ``before-edit-batch-<sha>.json`` consumption
  is deferred to v1.
- Stale-SHA detection compares sidecar SHA literal to current git HEAD.
  No version-range / fuzzy-match fallback.
- Learnings half always returns even when distill_sidecar feature is
  ungated — preserves operator value at free tier.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

from fastmcp import Context, FastMCP
from pydantic import BaseModel, ConfigDict, Field

# c749 (PRD-DIST-2002): LearningSummary extracted to shared
# `_learnings_collector` module. Re-exported here for backward
# compatibility with callers that still import from this module.
from trw_mcp.tools import _sidecar_substrate
from trw_mcp.tools._client_detection import resolve_client_profile, resolve_tier_for_client
from trw_mcp.tools._learnings_collector import LearningSummary

_SCHEMA_VERSION_ACCEPTED: str = "risk-report-sidecar/v0"
_ARTIFACT_NAME_SINGLE: str = "before-edit-hint"
_DEFAULT_CACHE_DIR_REL: str = ".trw/distill/map-cache"
_DEFAULT_LEARNINGS_TOP_N: int = 5


class BeforeYouEditHintPayload(BaseModel):
    """Cross-package shape pin against c734 BeforeYouEditHint.

    Field-by-field mirror of the trw-distill model. We CANNOT import
    the source class (IP boundary), so this is hand-maintained against
    the envelope contract. If trw-distill bumps the envelope
    schema_version, the tool returns ``schema_mismatch`` until this
    mirror is updated.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    # Constraints mirror the trw-distill source; parity-checked by
    # scripts/check-schema-mirror-parity.py (PRD-INFRA-134 FR-05).
    target_path: str = Field(min_length=1)
    target_exists_in_map: bool
    importers: list[str] = Field(default_factory=list)
    inferred_tests: list[str] = Field(default_factory=list)
    doc_references: list[str] = Field(default_factory=list)
    co_change_neighbors: list[str] = Field(default_factory=list)
    hotspot_warnings: list[str] = Field(default_factory=list)
    risk_score: float | None = None


class BeforeEditHintResult(BaseModel):
    """Two-source hint result.

    Both halves are independent: ``distill_hint`` may be None (tier
    ungated or sidecar missing) while ``learnings`` is populated, or
    vice versa.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    file_path: str
    tier: str
    distill_hint: BeforeYouEditHintPayload | None = None
    distill_status: Literal[
        "hint_available",
        "tier_required",
        "sidecar_missing",
        "sidecar_malformed",
        "schema_mismatch",
        "target_not_in_sidecar",
        "stale_sha",
        "ok",
    ] = "sidecar_missing"
    distill_action: str | None = None
    distill_sidecar_path: str | None = None
    distill_sidecar_sha: str | None = None
    learnings: list[LearningSummary] = Field(default_factory=list)
    learnings_count: int = 0


def _resolve_repo_root(repo_root: str | None) -> Path | None:
    if repo_root is not None:
        return Path(repo_root)
    git_executable = shutil.which("git")
    if git_executable is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [git_executable, "rev-parse", "--show-toplevel"],
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


def _resolve_git_sha(repo_root: Path) -> str | None:
    git_executable = shutil.which("git")
    if git_executable is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [git_executable, "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            stripped = proc.stdout.strip()
            if stripped and len(stripped) == 40 and all(c in "0123456789abcdef" for c in stripped):
                return stripped
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _load_sidecar_envelope(sidecar_path: Path) -> dict[str, Any] | None:
    if not sidecar_path.exists():
        return None
    try:
        parsed = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _select_distill_hint(
    sidecar_path: Path,
    file_path: str,
    sidecar_sha_expected: str,
) -> tuple[
    BeforeYouEditHintPayload | None,
    Literal[
        "hint_available",
        "sidecar_missing",
        "sidecar_malformed",
        "schema_mismatch",
        "target_not_in_sidecar",
        "stale_sha",
    ],
    str | None,
]:
    """Inspect sidecar envelope; return (payload, status, action).

    NEVER raises — every failure path returns (None, status, action).
    """
    envelope = _load_sidecar_envelope(sidecar_path)
    if envelope is None:
        return (
            None,
            "sidecar_missing",
            f"Run: cd <repo> && trw-distill self-improve before-edit --repo . --file {file_path} --persist-sidecar",
        )
    schema = envelope.get("schema_version")
    if schema != _SCHEMA_VERSION_ACCEPTED:
        return (
            None,
            "schema_mismatch",
            f"Sidecar schema_version={schema!r}; expected "
            f"{_SCHEMA_VERSION_ACCEPTED!r} — upgrade trw-distill or trw-mcp",
        )
    sidecar_sha = envelope.get("sha")
    if not isinstance(sidecar_sha, str) or sidecar_sha != sidecar_sha_expected:
        return (
            None,
            "stale_sha",
            f"Sidecar SHA={sidecar_sha!r}; HEAD={sidecar_sha_expected} — re-run with --persist-sidecar",
        )
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return (
            None,
            "sidecar_malformed",
            "Sidecar payload is not a dict; re-run --persist-sidecar to regenerate",
        )
    payload_target = payload.get("target_path")
    if payload_target != file_path:
        return (
            None,
            "target_not_in_sidecar",
            f"Sidecar for target_path={payload_target!r}; requested "
            f"{file_path!r} — run: trw-distill self-improve before-edit "
            f"--repo . --file {file_path} --persist-sidecar",
        )
    try:
        hint = BeforeYouEditHintPayload.model_validate(payload)
    except Exception:
        return (
            None,
            "sidecar_malformed",
            "Sidecar payload does not match BeforeYouEditHintPayload schema; check trw-distill version compatibility",
        )
    return (hint, "hint_available", None)


def _collect_learnings(file_path: str) -> list[LearningSummary]:
    """c749 (PRD-DIST-2002): delegate to shared collector.

    Preserves c746 backward compat (same signature, same semantics) by
    wrapping the shared `_learnings_collector.collect_learnings` with
    `build_file_queries`.
    """
    from trw_mcp.tools._learnings_collector import (
        build_file_queries,
        collect_learnings,
    )

    return collect_learnings(build_file_queries(file_path))


def compute_before_edit_hint(
    *,
    file_path: str,
    repo_root: str | None = None,
    cache_dir: str | None = None,
) -> BeforeEditHintResult:
    """Pure-Python entry point used by the MCP tool registrar + tests."""
    from trw_mcp.state._entitlements import load_entitlement
    from trw_mcp.state._paths import resolve_trw_dir

    resolved_repo_root = _resolve_repo_root(repo_root)
    learnings = _collect_learnings(file_path)

    trw_dir = (resolved_repo_root / ".trw") if resolved_repo_root is not None else resolve_trw_dir()
    entitlement = load_entitlement(trw_dir)

    # Entitlement resolves via EITHER an installed trw-distill package (proof
    # of a paid entitlement — the installer historically never wrote the
    # `.trw/entitlements.yaml` sentinel, so an entitled install otherwise
    # resolved tier="free") OR a valid entitlement sentinel.
    distill_present = _sidecar_substrate.distill_installed()
    feature_allowed = distill_present or entitlement.has_feature("trw_before_edit_hint:distill_sidecar")

    # Display tier: reflect the package-presence unlock so a working hint is
    # never labelled tier="free".
    display_tier: str = entitlement.tier
    if distill_present and entitlement.tier == "free":
        display_tier = "proprietary"

    distill_hint: BeforeYouEditHintPayload | None = None
    distill_status: Literal[
        "hint_available",
        "tier_required",
        "sidecar_missing",
        "sidecar_malformed",
        "schema_mismatch",
        "target_not_in_sidecar",
        "stale_sha",
        "ok",
    ] = "tier_required"
    # No remediation nag by default: when trw-distill is not installed the
    # sidecar feature is simply unavailable, and emitting a paid-tier remediation
    # on every edit would burn caller tokens for a feature not opted into. The
    # learnings half below always returns, preserving value at any tier.
    distill_action: str | None = None
    distill_sidecar_path: str | None = None
    distill_sidecar_sha: str | None = None

    if feature_allowed:
        if resolved_repo_root is None:
            distill_status = "sidecar_missing"
            distill_action = "Could not resolve git repo root — pass --repo or run from inside a git checkout"
        else:
            resolved_cache_dir = (
                Path(cache_dir) if cache_dir is not None else resolved_repo_root / _DEFAULT_CACHE_DIR_REL
            )
            git_sha = _resolve_git_sha(resolved_repo_root)
            if git_sha is None:
                distill_status = "stale_sha"
                distill_action = "Could not run `git rev-parse HEAD` — verify .git/ present + git CLI installed"
            else:
                distill_sidecar_sha = git_sha
                sidecar_path = resolved_cache_dir / f"{_ARTIFACT_NAME_SINGLE}-{git_sha}.json"
                distill_sidecar_path = str(sidecar_path)
                distill_hint, distill_status, distill_action = _select_distill_hint(
                    sidecar_path,
                    file_path,
                    git_sha,
                )

    return BeforeEditHintResult(
        file_path=file_path,
        tier=display_tier,
        distill_hint=distill_hint,
        distill_status=distill_status,
        distill_action=distill_action,
        distill_sidecar_path=distill_sidecar_path,
        distill_sidecar_sha=distill_sidecar_sha,
        learnings=learnings,
        learnings_count=len(learnings),
    )


def register_before_edit_hint_tools(server: FastMCP) -> None:
    """Register trw_before_edit_hint on the MCP server."""

    @server.tool()
    def trw_before_edit_hint(
        file_path: str,
        repo_root: str | None = None,
        cache_dir: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return cold-start codebase intelligence for ``file_path``.

        Use when an agent is about to edit a file and needs sidecar-backed
        risk context plus relevant prior learnings before reading broadly.

        Sources:
        - trw-distill sidecar (tier-gated; requires team/pro/enterprise)
        - existing learnings via trw_recall (always)

        Returns BeforeEditHintResult.model_dump() enriched by client tier.
        NEVER raises — failure paths populate ``distill_status`` +
        ``distill_action`` so the operator gets an actionable next step.
        """
        result = compute_before_edit_hint(
            file_path=file_path,
            repo_root=repo_root,
            cache_dir=cache_dir,
        )
        # --- telemetry (fail-open) ---
        with suppress(Exception):  # justified: fail-open telemetry, never break the tool
            from trw_mcp.channels._distill_telemetry import emit_tool_call

            sidecar_sha = result.distill_sidecar_sha or ""
            record_ids = [f"hotspot:{file_path}@{sidecar_sha[:8]}"] if sidecar_sha else []
            emit_tool_call(
                tool_name="trw_before_edit_hint",
                file_path=file_path,
                tier=result.tier,
                record_ids=record_ids,
            )
        # --- tier-aware response enrichment (fail-open) ---
        base: dict[str, Any] = result.model_dump()
        with suppress(Exception):  # justified: fail-open enrichment never breaks the base response
            from trw_mcp.channels._tool_return_tiers import enrich_response

            client = resolve_client_profile(ctx=ctx)
            client_tier = resolve_tier_for_client(client)
            return enrich_response(base, client_tier=client_tier)
        return base


__all__ = [
    "BeforeEditHintResult",
    "BeforeYouEditHintPayload",
    "LearningSummary",
    "compute_before_edit_hint",
    "register_before_edit_hint_tools",
    "resolve_client_profile",
]
