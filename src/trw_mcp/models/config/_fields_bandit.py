"""Bandit-based learning selection configuration fields (PRD-CORE-105)."""

from __future__ import annotations

import os

from pydantic import Field, field_validator


def _detect_model_family_from_env() -> str:
    """Best-effort detection of model family from well-known environment variables.

    Checks common env vars used by Claude Code, OpenAI, and other clients.
    Returns "generic" when no recognisable family is found — a stable non-empty
    fallback that avoids silent empty-string tagging (P1-A fix).
    """
    # Claude / Anthropic
    for var in ("CLAUDE_MODEL", "ANTHROPIC_MODEL", "CLAUDE_CODE_MODEL"):
        val = os.environ.get(var, "").strip()
        if val:
            return val.split("-")[0] if "-" in val else val
    # OpenAI / Codex
    for var in ("OPENAI_MODEL_NAME", "OPENAI_MODEL", "CODEX_MODEL"):
        val = os.environ.get(var, "").strip()
        if val:
            return val.split("-")[0] if "-" in val else val
    # Generic TRW override
    val = os.environ.get("TRW_MODEL_FAMILY_HINT", "").strip()
    if val:
        return val
    return "generic"


class _BanditFields:
    """Bandit selection domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Bandit-based nudge selection (PRD-CORE-105-FR06) --

    phase_transition_withhold_rate: float = Field(
        default=0.10,
        ge=0.0,
        le=0.30,
        description=(
            "Fraction of non-critical learnings withheld at phase boundaries "
            "for micro-randomized causal signal (FR06). Range: [0.0, 0.30]."
        ),
    )

    # -- C-5 Model Generation Preparedness (PRD-CORE-105-FR01) --

    model_family: str = Field(
        default="",
        description=(
            "Model family tag for bandit state envelope and propensity logs (C-5). "
            "When empty, best-effort auto-detection is applied and 'generic' is used "
            "as a stable fallback so production logs never carry empty model_family. "
            "Set via env var TRW_MODEL_FAMILY or config.yaml key model_family."
        ),
    )

    @field_validator("model_family", mode="after")
    @classmethod
    def _resolve_model_family(cls, v: str) -> str:
        """Resolve model_family to a non-empty string (P1-A fix).

        If the configured value is empty, attempt environment-based detection
        and fall back to 'generic'. This ensures bandit state envelopes and
        propensity logs always carry a meaningful model_family tag.
        """
        if v and v.strip():
            return v.strip()
        return _detect_model_family_from_env()
