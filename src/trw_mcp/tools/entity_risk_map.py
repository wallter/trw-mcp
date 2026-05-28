"""trw_entity_risk_map MCP tool (PRD-CORE-167).

Public sidecar consumer for entity/symbol-level structural risk. This
module intentionally mirrors the sidecar contract locally and does not
import producer packages; the producer may live in trw-distill or another
separately governed package.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trw_mcp.tools._sidecar_substrate import (
    DEFAULT_CACHE_DIR_REL,
    check_tier_for_feature,
    load_sidecar_with_sha_check,
    resolve_git_sha,
    resolve_repo_root,
)

_ARTIFACT_NAME: str = "entity-risk-map"
_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"
_COMPONENT: str = "trw_entity_risk_map"
_LOGGER = logging.getLogger(__name__)

EntityRiskStatus = Literal[
    "hint_available",
    "tier_required",
    "sidecar_missing",
    "sidecar_malformed",
    "schema_mismatch",
    "stale_sha",
    "no_repo_root",
    "no_git_sha",
]
EntityKind = Literal["module", "class", "function", "method", "endpoint", "symbol"]
EntityExposure = Literal["private", "internal", "public", "external"]


class EntityRiskScorePayload(BaseModel):
    """Strict public row contract for entity structural risk sidecars."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    qualified_name: str = Field(min_length=1)
    entity_kind: EntityKind
    file_path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    exposure: EntityExposure
    risk_score: float = Field(ge=0.0, le=1.0)
    blast_radius_count: int = Field(ge=0)
    changed: bool = False
    reasons: list[str] = Field(default_factory=list)
    dependency_path_samples: list[list[str]] = Field(default_factory=list)


class EntityRiskMapResult(BaseModel):
    """Top-level result for entity-risk sidecar consumption."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    entity_risk: list[EntityRiskScorePayload] = Field(default_factory=list)
    distill_status: EntityRiskStatus = "sidecar_missing"
    distill_action: str | None = None
    distill_sidecar_path: str | None = None
    distill_sidecar_sha: str | None = None
    entity_count: int = 0


def _producer_command() -> str:
    return "trw-distill self-improve entity-risk-map --repo . --persist-sidecar"


def _status_result(
    *,
    tier: str,
    status: EntityRiskStatus,
    action: str | None,
    sidecar_path: str | None = None,
    sidecar_sha: str | None = None,
) -> EntityRiskMapResult:
    result = EntityRiskMapResult(
        tier=tier,
        distill_status=status,
        distill_action=action,
        distill_sidecar_path=sidecar_path,
        distill_sidecar_sha=sidecar_sha,
    )
    _log_result(result, elapsed_ms=0.0)
    return result


def _is_repo_relative_path(path: str) -> bool:
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def _validate_entity_rows(payload: object) -> tuple[list[EntityRiskScorePayload] | None, str | None]:
    if not isinstance(payload, list):
        return None, "entity-risk-map sidecar payload is not an array; re-run with --persist-sidecar"

    rows: list[EntityRiskScorePayload] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            return None, f"entity-risk-map payload row {index} is not an object"
        try:
            row = EntityRiskScorePayload.model_validate(entry)
        except ValidationError as exc:
            return None, f"entity-risk-map payload row {index} failed schema validation: {exc.errors()[0]['type']}"
        if row.end_line < row.start_line:
            return None, f"entity-risk-map payload row {index} has end_line before start_line"
        if not _is_repo_relative_path(row.file_path):
            return None, f"entity-risk-map payload row {index} has non repo-relative file_path"
        rows.append(row)
    return rows, None


def _sort_entities(rows: list[EntityRiskScorePayload]) -> list[EntityRiskScorePayload]:
    return sorted(
        rows,
        key=lambda row: (-row.risk_score, -row.blast_radius_count, row.qualified_name),
    )


def _filter_entities(
    rows: list[EntityRiskScorePayload],
    *,
    changed_only: bool,
    top_n: int,
) -> list[EntityRiskScorePayload]:
    selected = [row for row in rows if row.changed] if changed_only else list(rows)
    sorted_rows = _sort_entities(selected)
    if top_n > 0:
        return sorted_rows[:top_n]
    return sorted_rows


def _log_result(result: EntityRiskMapResult, *, elapsed_ms: float) -> None:
    record = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "level": "info",
        "component": _COMPONENT,
        "op": "compute_entity_risk_map",
        "outcome": result.distill_status,
        "entity_count": result.entity_count,
        "returned_count": len(result.entity_risk),
        "elapsed_ms": round(elapsed_ms, 3),
    }
    _LOGGER.info(json.dumps(record, sort_keys=True))


def compute_entity_risk_map(
    *,
    repo_root: str | None = None,
    cache_dir: str | None = None,
    top_n: int = 20,
    changed_only: bool = False,
) -> EntityRiskMapResult:
    """Load, validate, sort, and filter the current entity-risk sidecar.

    ``top_n=0`` returns all matching rows. The function never imports or
    executes producer code and returns structured statuses instead of raising
    for expected sidecar/tier/schema failures.
    """

    started = time.perf_counter()
    resolved_repo_root = resolve_repo_root(repo_root)
    if resolved_repo_root is None:
        return _status_result(
            tier="free",
            status="no_repo_root",
            action="Pass --repo or run from inside a git checkout",
        )

    gate = check_tier_for_feature(resolved_repo_root, _TIER_FEATURE)
    if not gate.allowed:
        return _status_result(
            tier=gate.tier,
            status="tier_required",
            action=(
                "Acquire team/pro/enterprise tier to enable trw-distill "
                "sidecar consumption (see https://trwframework.com/tier)"
            ),
        )

    git_sha = resolve_git_sha(resolved_repo_root)
    if git_sha is None:
        return _status_result(
            tier=gate.tier,
            status="no_git_sha",
            action="Could not run `git rev-parse HEAD` — verify .git/ present",
        )

    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else resolved_repo_root / DEFAULT_CACHE_DIR_REL
    sidecar_path = resolved_cache_dir / f"{_ARTIFACT_NAME}-{git_sha}.json"
    existed_before_load = sidecar_path.exists()

    load = load_sidecar_with_sha_check(
        sidecar_path,
        expected_sha=git_sha,
        file_path_hint="<entity-risk-map>",
        cli_remediation=_producer_command(),
    )
    if load.status != "ok" or load.payload is None:
        status: EntityRiskStatus
        action: str | None
        if load.status == "sidecar_missing" and existed_before_load:
            status = "sidecar_malformed"
            action = f"Sidecar {sidecar_path} is malformed JSON; re-run: {_producer_command()}"
        else:
            status = cast("EntityRiskStatus", load.status)
            action = load.action
        return _status_result(
            tier=gate.tier,
            status=status,
            action=action,
            sidecar_path=load.sidecar_path,
            sidecar_sha=load.sidecar_sha,
        )

    rows, error = _validate_entity_rows(load.payload)
    if rows is None:
        return _status_result(
            tier=gate.tier,
            status="sidecar_malformed",
            action=f"{error}; check producer/consumer schema compatibility",
            sidecar_path=load.sidecar_path,
            sidecar_sha=load.sidecar_sha,
        )

    filtered_rows = _filter_entities(rows, changed_only=changed_only, top_n=top_n)
    result = EntityRiskMapResult(
        tier=gate.tier,
        entity_risk=filtered_rows,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=load.sidecar_path,
        distill_sidecar_sha=load.sidecar_sha,
        entity_count=len(rows),
    )
    _log_result(result, elapsed_ms=(time.perf_counter() - started) * 1000.0)
    return result


def register_entity_risk_map_tools(server: FastMCP) -> None:
    """Register trw_entity_risk_map on the MCP server."""

    @server.tool()
    def trw_entity_risk_map(
        repo_root: str | None = None,
        cache_dir: str | None = None,
        top_n: int = 20,
        changed_only: bool = False,
    ) -> dict[str, object]:
        """Return entity-level structural risk rows for the current SHA.

        Use when a reviewer needs symbol/function/class/endpoint blast-radius
        triage from a persisted sidecar. Tier-gated. ``top_n=0`` returns all
        matching rows. NEVER raises for sidecar failures.
        """

        result = compute_entity_risk_map(
            repo_root=repo_root,
            cache_dir=cache_dir,
            top_n=top_n,
            changed_only=changed_only,
        )
        try:
            from trw_mcp.channels._distill_telemetry import emit_tool_call

            sidecar_sha = result.distill_sidecar_sha or ""
            record_ids = (
                [f"entity-risk-map@{sidecar_sha[:8]}"] if sidecar_sha else []
            )
            emit_tool_call(
                tool_name="trw_entity_risk_map",
                tier=result.tier,
                record_ids=record_ids,
            )
        except Exception:  # justified: BLE001 — fail-open telemetry, never break the tool
            pass
        return cast("dict[str, object]", result.model_dump(mode="json"))


__all__ = [
    "EntityRiskMapResult",
    "EntityRiskScorePayload",
    "compute_entity_risk_map",
    "register_entity_risk_map_tools",
]
