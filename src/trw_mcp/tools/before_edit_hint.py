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
- Reads the single-file ``before-edit-hint-<sha>.json`` artifact (c743) and
  falls back to ``before-edit-batch-<sha>.json`` when that artifact does not
  describe the requested file. The fallback is not a convenience: the
  single-file name has NO per-file discriminator, so every producer writing it
  overwrites the last, and a commit touching N files can serve at most one of
  them from it. The post-commit refresh accordingly emits the batch artifact.
- Stale-SHA detection compares sidecar SHA literal to current git HEAD.
  No version-range / fuzzy-match fallback.
- Learnings half always returns even when distill_sidecar feature is
  ungated — preserves operator value at free tier.

Repo-root / SHA / envelope resolution lives in ``_sidecar_substrate``
(extracted FROM this module at c747). This tool was the last of the five
sidecar consumers still carrying a hand-rolled copy of that logic, and the
copy had diverged: an unresolvable ``git rev-parse HEAD`` was reported as
``stale_sha`` — a status that asserts a sidecar was read and disagreed —
when no sidecar had been consulted at all. The substrate has a distinct
``no_git_sha`` for exactly that case. Consuming the substrate is what keeps
the two from diverging again; do not reintroduce a local resolver here.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Literal

from fastmcp import Context, FastMCP
from pydantic import BaseModel, ConfigDict, Field

# c749 (PRD-DIST-2002): LearningSummary extracted to shared
# `_learnings_collector` module. Re-exported here for backward
# compatibility with callers that still import from this module.
from trw_mcp.tools import _sidecar_substrate
from trw_mcp.tools._client_detection import resolve_client_profile, resolve_tier_for_client
from trw_mcp.tools._learnings_collector import LearningSummary
from trw_mcp.tools._sidecar_substrate import CurrentSidecarStatus

# Derived from the substrate, never re-spelled: a hand-copied constant is how
# the two drifted in the first place.
_SCHEMA_VERSION_ACCEPTED: str = _sidecar_substrate.SCHEMA_VERSION_ACCEPTED
_ARTIFACT_NAME_SINGLE: str = "before-edit-hint"
_ARTIFACT_NAME_BATCH: str = "before-edit-batch"
_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"

#: Statuses in which the substrate returned BEFORE consulting any artifact, so
#: a second lookup would repeat the same negative at the cost of two more git
#: subprocesses. Every other status means an artifact path was actually
#: examined, and the batch artifact is then worth examining too.
_NO_ARTIFACT_CONSULTED: frozenset[str] = frozenset({"tier_required", "no_repo_root", "no_git_sha"})

#: This tool adds exactly one status to the shared vocabulary: the sidecar
#: loaded cleanly but describes a DIFFERENT file. Everything else is the
#: substrate's closed set, so a new shared status arrives here automatically.
BeforeEditHintStatus = CurrentSidecarStatus | Literal["target_not_in_sidecar"]

#: Statuses in which the entitlement gate never ran, so "was this edit
#: eligible?" has no answer. ``tier_required`` means checked-and-denied;
#: ``no_repo_root`` means the substrate returned before reaching the gate.
#: Emitting ``eligible: True`` for either would write a claim into durable
#: telemetry that no check ever made — the same defect as the ``stale_sha``
#: mislabel this module was migrated to fix.
_ELIGIBILITY_UNDETERMINED: frozenset[str] = frozenset({"tier_required", "no_repo_root"})


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
    distill_status: BeforeEditHintStatus = "sidecar_missing"
    distill_action: str | None = None
    distill_sidecar_path: str | None = None
    distill_sidecar_sha: str | None = None
    learnings: list[LearningSummary] = Field(default_factory=list)
    learnings_count: int = 0


def _cli_remediation(file_path: str) -> str:
    """Exact command that regenerates this file's single-file sidecar."""
    return f"cd <repo> && trw-distill self-improve before-edit --repo . --file {file_path} --persist-sidecar"


def _batch_miss_action(file_path: str) -> str:
    """Remediation when a batch artifact exists but does not cover *file_path*.

    Names the batch artifact explicitly. "Regenerate the sidecar" is not
    actionable when the reader cannot tell which of two artifacts was read, and
    the post-commit refresh only covers the files a commit touched — so a file
    absent from the batch is the expected, not the broken, case.
    """
    return (
        f"Batch sidecar does not cover {file_path!r} (it covers the files the last commit touched) — "
        f"run: {_cli_remediation(file_path)}"
    )


def _select_distill_hint(
    payload: Any,
    file_path: str,
) -> tuple[
    BeforeYouEditHintPayload | None,
    Literal["hint_available", "sidecar_malformed", "target_not_in_sidecar"],
    str | None,
]:
    """Validate an already-loaded sidecar payload against the requested file.

    The substrate has already proved the envelope exists, carries the accepted
    schema_version, and matches HEAD. What is left is this tool's own contract:
    the payload must describe ``file_path`` and must satisfy the mirror model.

    NEVER raises — every failure path returns (None, status, action).
    """
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


def _select_from_batch(
    payload: Any,
    file_path: str,
) -> tuple[BeforeYouEditHintPayload | None, Literal["hint_available", "sidecar_malformed", "target_not_in_sidecar"]]:
    """Pick this file's hint out of an already-loaded ``before-edit-batch`` payload.

    NEVER raises. The two negative statuses are kept apart deliberately: "the
    batch does not mention this file" and "the batch mentions it but the entry
    does not parse" are different operator problems, and collapsing the second
    into the first would report a schema break as an absent target.
    """
    if not isinstance(payload, dict):
        return (None, "sidecar_malformed")
    hints = payload.get("hints")
    if not isinstance(hints, list):
        return (None, "sidecar_malformed")
    for entry in hints:
        if not isinstance(entry, dict) or entry.get("target_path") != file_path:
            continue
        try:
            return (BeforeYouEditHintPayload.model_validate(entry), "hint_available")
        except Exception:
            return (None, "sidecar_malformed")
    return (None, "target_not_in_sidecar")


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
    learnings = _collect_learnings(file_path)

    # Repo root, entitlement gate, HEAD sha, envelope + schema + sha checks all
    # come from the shared substrate. Its status vocabulary distinguishes the
    # three "we never got as far as comparing" cases — `no_repo_root`,
    # `tier_required`, `no_git_sha` — from `stale_sha`, which is a real
    # comparison that disagreed. The tier the substrate reports already folds in
    # the trw-distill-installed unlock (tier="proprietary" without a sentinel).
    sidecar = _sidecar_substrate.resolve_current_sidecar(
        repo_root=repo_root,
        cache_dir=cache_dir,
        feature=_TIER_FEATURE,
        artifact_name=_ARTIFACT_NAME_SINGLE,
        cli_remediation=_cli_remediation(file_path),
    )

    distill_hint: BeforeYouEditHintPayload | None = None
    # No remediation nag on the tier-gated path: when trw-distill is not
    # installed the sidecar feature is simply unavailable, and emitting a
    # paid-tier remediation on every edit would burn caller tokens for a feature
    # not opted into. `resolve_current_sidecar` already returns action=None
    # there. The learnings half below always returns, preserving value at any
    # tier.
    distill_status: BeforeEditHintStatus = sidecar.status
    distill_action: str | None = sidecar.action
    distill_sidecar_path: str | None = sidecar.sidecar_path
    if sidecar.status == "hint_available":
        distill_hint, distill_status, distill_action = _select_distill_hint(sidecar.payload, file_path)

    # The single-file artifact holds ONE hint for the whole repo at a given sha
    # — `before-edit-hint-<sha>.json` carries no per-file discriminator — so on
    # any commit touching more than one file at most one target can be served
    # from it. The post-commit refresh therefore emits the BATCH artifact, which
    # holds one hint per target, and this is where it gets consumed. The module
    # docstring listed batch consumption as deferred v1 scope; without it the
    # producer fix would have written artifacts nothing reads.
    if distill_status != "hint_available" and sidecar.status not in _NO_ARTIFACT_CONSULTED:
        batch = _sidecar_substrate.resolve_current_sidecar(
            repo_root=repo_root,
            cache_dir=cache_dir,
            feature=_TIER_FEATURE,
            artifact_name=_ARTIFACT_NAME_BATCH,
            cli_remediation=_cli_remediation(file_path),
        )
        if batch.status == "hint_available":
            batch_hint, batch_status = _select_from_batch(batch.payload, file_path)
            # Only ADOPT the batch outcome — never let a batch miss overwrite a
            # more specific single-file finding with a vaguer one. A batch that
            # cannot answer leaves the single-file status exactly as it was.
            if batch_hint is not None or distill_status == "sidecar_missing":
                distill_hint = batch_hint
                distill_status = batch_status
                distill_action = None if batch_hint is not None else _batch_miss_action(file_path)
                distill_sidecar_path = batch.sidecar_path

    # PRD-CORE-231-FR01: record every ELIGIBLE edit in durable telemetry.
    # "Eligible" == the entitlement gate ran AND allowed the feature. Misses are
    # recorded too — a gate computed only from hits would be a survivorship
    # statistic — but a status in `_ELIGIBILITY_UNDETERMINED` means the gate
    # never produced an answer, and `eligible: True` there would be a fabricated
    # one. This is the single emission point shared by the CC-03 hook subprocess
    # and the direct MCP-tool path.
    if distill_status not in _ELIGIBILITY_UNDETERMINED:
        from trw_mcp.channels._distill_telemetry import emit_hint_delivered

        emit_hint_delivered(
            tier="T2" if distill_status == "hint_available" else sidecar.tier,
            distill_status=distill_status,
            file_path=file_path,
        )

    return BeforeEditHintResult(
        file_path=file_path,
        tier=sidecar.tier,
        distill_hint=distill_hint,
        distill_status=distill_status,
        distill_action=distill_action,
        # Which artifact answered is visible here — the filename carries
        # `before-edit-hint-` or `before-edit-batch-`. That is the only way an
        # operator can tell a single-file hit from a batch hit, and it costs no
        # extra response field.
        distill_sidecar_path=distill_sidecar_path,
        distill_sidecar_sha=sidecar.sidecar_sha,
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
        """Return sidecar risk hints (paid tiers) + prior learnings (all tiers) for one file.

        Use when: about to edit file_path (trw_before_edit_hint_batch covers
        many files). Never raises; on failure, distill_status explains why.
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
    "BeforeEditHintStatus",
    "BeforeYouEditHintPayload",
    "LearningSummary",
    "compute_before_edit_hint",
    "register_before_edit_hint_tools",
    "resolve_client_profile",
]
