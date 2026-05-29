"""LLM usage record model — PRD-CORE-020 FR01.

Structured record for a single LLM API call, appended to a JSONL usage log.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LLMUsageRecord(BaseModel):
    """Single LLM API call record persisted to the usage log (JSONL).

    Fields map directly to the JSONL schema defined in PRD-CORE-020 FR02.
    """

    model_config = ConfigDict(strict=True, populate_by_name=True)

    ts: str = Field(description="ISO 8601 timestamp of the LLM call")
    model: str  # provider-specific model ID or capability alias
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0)
    caller: str = "ask"  # calling context identifier
    success: bool = True
