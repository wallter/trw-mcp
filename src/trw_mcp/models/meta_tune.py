"""Candidate-edit contract for the meta-tune safety pipeline.

``CandidateEdit`` is the live PRD-HPO-SAFE-001 proposal model shared by the
surface registry and promotion entry point. The evolved runtime contracts for
sandbox results, gate decisions, and audit rows are owned by
``meta_tune.sandbox``, ``meta_tune.promotion_gate``, and ``meta_tune.audit``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class CandidateEdit(BaseModel):
    """A candidate advisory-surface edit proposed by the meta-tune loop.

    `target_path` MUST classify as "advisory" under `surface_registry`; the
    sandbox rejects any candidate whose target classifies as "control".
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    edit_id: str = Field(..., description="UUID identifying this candidate.")
    proposer_id: str = Field(..., description="Agent/session identity that proposed this edit.")
    target_path: Path = Field(..., description="Path to the advisory surface this candidate edits.")
    diff: str = Field(..., description="Unified diff representing the candidate edit.")
    created_ts: datetime = Field(..., description="UTC timestamp when the candidate was proposed.")
