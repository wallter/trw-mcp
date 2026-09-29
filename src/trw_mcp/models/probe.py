"""Pydantic v2 models for the empirical probe harness (PRD-CORE-144).

Schema pinned here per FR-02 Assertion A1. These types are the typed
contract between :mod:`trw_mcp.probe` (harness / budget / cache / verdict)
and the ``trw-mcp probe`` CLI verbs.

``ProbeEvent`` is a payload-backed variant emitted through the unified
``HPOTelemetryEvent`` envelope (PRD-HPO-MEAS-001) — see
:func:`trw_mcp.probe.telemetry.build_probe_event`; this module only defines
the structured ``ProbeResult`` carried inside that event's ``payload``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["supports", "refutes", "inconclusive"]


def _utc_now() -> datetime:
    """Return current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


class ResourceBudget(BaseModel):
    """Per-probe resource cap (FR-03). Frozen so cache keys stay stable."""

    model_config = ConfigDict(frozen=True)

    memory_mb: int = Field(default=256, ge=16, le=2048)
    cpu_quota_pct: int = Field(default=100, ge=10, le=400)


class ProbeEvidence(BaseModel):
    """Captured execution evidence (FR-02, FR-03).

    ``wall_ms`` and ``resource_use`` are recorded even on timeout/OOM so the
    rubric and the H4 yield metric see partial evidence rather than nothing.
    """

    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    wall_ms: int = Field(default=0, ge=0)
    resource_use: dict[str, float] = Field(default_factory=dict)
    encoding_replaced: bool = False
    timed_out: bool = False
    network_attempted: bool = False
    writes_outside_tmp: list[str] = Field(default_factory=list)


class ProbeResult(BaseModel):
    """Typed output of a single probe (FR-02).

    Round-trip JSON serialization is lossless (FR-02 A2); ``confidence``
    outside ``[0,1]`` raises ``ValidationError`` (FR-02 A3).
    """

    hypothesis: str
    hypothesis_id: str | None = None
    verdict: Verdict
    evidence: ProbeEvidence
    confidence: float = Field(ge=0.0, le=1.0)
    ts: datetime = Field(default_factory=_utc_now)
    run_id: str
    budget_override: bool = False
    cache_hit: bool = False


class ProbeBudgetStatus(BaseModel):
    """Read-only budget snapshot printed by ``trw-mcp probe budget`` (FR-10)."""

    used: int = Field(ge=0)
    remaining: int = Field(ge=0)
    total: int = Field(ge=0)
    by_hypothesis_id: dict[str, int] = Field(default_factory=dict)


__all__ = [
    "ProbeBudgetStatus",
    "ProbeEvidence",
    "ProbeResult",
    "ResourceBudget",
    "Verdict",
]
