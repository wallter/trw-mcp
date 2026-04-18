"""Pydantic v2 data models for the meta-tune safety pipeline.

PRD-HPO-SAFE-001 §7.3 — Meta-Tune Safety Gates.

These models describe the lifecycle of a candidate edit proposed by the
meta-tune outer loop: `CandidateEdit` (proposed), `SandboxResult` (replayed),
`PromotionDecision` (gated), `AuditEntry` (hash-chained audit log row).

All models are strict and frozen so that:

1. Unknown fields raise at construction (no silent drift between proposer,
   sandbox, gate, and audit writer).
2. Instances are immutable after construction (audit-log integrity: nobody
   can mutate a decision after it has been chained into the log).

These types are the canonical wire-format for the meta-tune pipeline and
are shared across `meta_tune/sandbox.py`, `meta_tune/promotion_gate.py`,
`meta_tune/audit.py`, and `meta_tune/rollback.py`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CandidateEdit(BaseModel):
    """A candidate advisory-surface edit proposed by the meta-tune loop.

    `target_path` MUST classify as "advisory" under `surface_registry`; the
    sandbox rejects any candidate whose target classifies as "control".
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    edit_id: str = Field(..., description="UUID identifying this candidate.")
    proposer_id: str = Field(
        ..., description="Agent/session identity that proposed this edit."
    )
    target_path: Path = Field(
        ..., description="Path to the advisory surface this candidate edits."
    )
    diff: str = Field(..., description="Unified diff representing the candidate edit.")
    created_ts: datetime = Field(
        ..., description="UTC timestamp when the candidate was proposed."
    )


class SandboxResult(BaseModel):
    """Deterministic replay result for a single candidate edit.

    `scores` maps per-task identifiers (from the held-out replay corpus) to
    a scalar outcome; `eval_gaming_flags` is empty when the candidate passes
    the eval-gaming detector.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    edit_id: str = Field(..., description="UUID of the candidate that was replayed.")
    corpus_version: str = Field(
        ..., description="Version tag of the held-out replay corpus."
    )
    seed: int = Field(..., description="Deterministic seed used for this replay.")
    scores: dict[str, float] = Field(
        ..., description="Per-task outcome scores produced by the sandbox."
    )
    eval_gaming_flags: list[str] = Field(
        ..., description="Eval-gaming detector flags (empty when clean)."
    )
    elapsed_ms: int = Field(..., description="Wall-clock replay latency in ms.")


class PromotionDecision(BaseModel):
    """Decision produced by the promotion gate.

    `decision` is the terminal outcome; `reason` is a short machine-readable
    string (e.g. ``control-surface-violation``, ``outcome-correlation-fail``,
    ``goodhart-flag``, ``eval-artifact-modification``, ``promoted``).
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    edit_id: str = Field(..., description="UUID of the candidate under review.")
    outcome_correlation_ok: bool = Field(
        ..., description="Whether the outcome-correlation check passed."
    )
    goodhart_ok: bool = Field(..., description="Whether the Goodhart detector passed.")
    reviewer_id: str | None = Field(
        default=None, description="Reviewer identity when a human signed off."
    )
    approval_ts: datetime | None = Field(
        default=None, description="UTC timestamp of reviewer sign-off."
    )
    decision: Literal["promoted", "rejected"] = Field(
        ..., description="Terminal decision."
    )
    reason: str = Field(..., description="Machine-readable reason tag.")


class AuditEntry(BaseModel):
    """A single hash-chained audit entry.

    Every lifecycle event (propose, sandbox, promote, reject, roll back) is
    appended as one `AuditEntry` into `.trw/meta_tune/meta_tune_audit.jsonl`.
    `entry_hash = sha256(prev_hash || canonical_json(payload))`; the genesis
    entry uses `prev_hash = "0" * 64`.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    edit_id: str = Field(..., description="UUID of the edit this entry records.")
    event: Literal[
        "proposed", "sandboxed", "promoted", "rejected", "rolled_back"
    ] = Field(..., description="Lifecycle event captured by this entry.")
    payload: dict[str, object] = Field(
        ..., description="Event-specific payload (diff, scores, reviewer, etc.)."
    )
    ts: datetime = Field(..., description="UTC timestamp of the event.")
    prev_hash: str = Field(
        ..., description="SHA-256 hex of the previous chained entry."
    )
    entry_hash: str = Field(..., description="SHA-256 hex of this entry.")
