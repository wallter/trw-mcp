"""Surface classification for meta-tune candidates.

PRD-HPO-SAFE-001 FR-1 + FR-8. Classifies a candidate edit into one or more
of five domains — ``model``, ``prompt``, ``config``, ``policy``, ``weights`` —
and whether the surface is **control** (never editable by meta-tune) or
**advisory** (candidate-editable).

Default posture is fail-safe closed: any path not matched by an advisory
rule classifies as control. See FR-8 rationale.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.models.meta_tune import CandidateEdit
from trw_mcp.telemetry.event_base import MetaTuneEvent

if TYPE_CHECKING:
    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)


class Surface(str, Enum):
    """Canonical meta-tune surface domains (FR-1)."""

    MODEL = "model"
    PROMPT = "prompt"
    CONFIG = "config"
    POLICY = "policy"
    WEIGHTS = "weights"


class SurfaceClassification(BaseModel):
    """Result of classifying a path or candidate."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    is_control: bool = Field(..., description="True when surface is control (FR-1/FR-8).")
    surfaces: tuple[Surface, ...] = Field(
        default=(), description="Surface domains touched by the candidate."
    )
    rationale: str | None = Field(
        default=None, description="Short human-readable reason for the classification."
    )
    disabled: bool = Field(
        default=False,
        description="True when meta_tune.enabled=False caused a fail-safe no-op.",
    )


# --- Classification rules -----------------------------------------------------

# Control-surface paths (FR-1, seeded from CONSTITUTION §Hard Boundaries).
_CONTROL_PATTERNS: tuple[tuple[re.Pattern[str], tuple[Surface, ...], str], ...] = (
    (re.compile(r"(^|/)docs/CONSTITUTION\.md$"), (Surface.POLICY,), "constitution"),
    (re.compile(r"(^|/)docs/VISION\.md$"), (Surface.POLICY,), "vision"),
    (re.compile(r"(^|/)\.trw/config\.ya?ml$"), (Surface.CONFIG,), "hard_boundary_config"),
    (re.compile(r"(^|/)data/hooks/.+\.sh$"), (Surface.POLICY,), "hook_policy"),
    (re.compile(r"/hooks/.+\.sh$"), (Surface.POLICY,), "hook_policy"),
    (re.compile(r"\.github/workflows/"), (Surface.POLICY,), "ci_policy"),
    (re.compile(r"(^|/)phase_gates?\.ya?ml$"), (Surface.POLICY,), "phase_gate"),
)

# Advisory surfaces — patterns + domains.
_ADVISORY_PATTERNS: tuple[tuple[re.Pattern[str], tuple[Surface, ...], str], ...] = (
    (re.compile(r"(^|/)CLAUDE\.md$"), (Surface.PROMPT,), "claude_md"),
    (re.compile(r"(^|/)AGENTS?\.md$"), (Surface.PROMPT,), "agents_md"),
    (re.compile(r"/data/agents/.+\.md$"), (Surface.PROMPT,), "agent_prompt"),
    (re.compile(r"/data/skills/.+\.md$"), (Surface.PROMPT,), "skill_prompt"),
    (re.compile(r"/data/pricing\.ya?ml$"), (Surface.CONFIG,), "pricing_config"),
    (re.compile(r"\.safetensors$|\.pt$|\.bin$|/weights/"), (Surface.WEIGHTS,), "weights_blob"),
    (re.compile(r"/models/.+\.(safetensors|pt|bin|gguf)$"), (Surface.WEIGHTS, Surface.MODEL), "model_weights"),
)


def _normalize(path: Path) -> str:
    return str(PurePosixPath(*path.parts))


def classify_path(path: Path) -> SurfaceClassification:
    """Classify a single path. Fail-safe closed (FR-8)."""
    norm = _normalize(path)
    # Control first — if any control rule matches, classify as control.
    for pattern, surfaces, reason in _CONTROL_PATTERNS:
        if pattern.search(norm):
            return SurfaceClassification(
                is_control=True, surfaces=surfaces, rationale=f"control:{reason}"
            )
    # Advisory rules.
    matched: list[Surface] = []
    reasons: list[str] = []
    for pattern, surfaces, reason in _ADVISORY_PATTERNS:
        if pattern.search(norm):
            for s in surfaces:
                if s not in matched:
                    matched.append(s)
            reasons.append(reason)
    if matched:
        return SurfaceClassification(
            is_control=False,
            surfaces=tuple(matched),
            rationale=f"advisory:{','.join(reasons)}",
        )
    # FR-8: fail-safe closed default.
    return SurfaceClassification(
        is_control=True, surfaces=(), rationale="untagged_default_control"
    )


def _iter_diff_paths(diff: str) -> list[str]:
    """Extract paths referenced in a unified diff's ``+++``/``---`` lines."""
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            frag = line[4:].strip()
            if frag in ("/dev/null", ""):
                continue
            if frag.startswith("a/") or frag.startswith("b/"):
                frag = frag[2:]
            out.append(frag)
    return out


def classify_candidate(
    candidate: CandidateEdit,
    *,
    _config: TRWConfig | None = None,
) -> SurfaceClassification:
    """Classify a candidate edit's surface touch-set.

    FR-7/FR-13 kill-switch: returns a fail-safe ``disabled=True`` control
    classification when ``config.meta_tune.enabled`` is False.
    """
    cfg = _config
    if cfg is None:
        from trw_mcp.models.config._main import TRWConfig

        cfg = TRWConfig()
    if not cfg.meta_tune.enabled:
        logger.warning(
            "meta_tune_disabled",
            component="meta_tune.surface_registry",
            op="classify_candidate",
            outcome="noop",
            reason="kill_switch_off",
        )
        return SurfaceClassification(
            is_control=True,
            surfaces=(),
            rationale="meta_tune_disabled",
            disabled=True,
        )

    # Classify target_path.
    primary = classify_path(candidate.target_path)
    surfaces: list[Surface] = list(primary.surfaces)
    is_control = primary.is_control
    reasons: list[str] = [primary.rationale or ""]

    # Sniff diff: any control-surface path reference promotes to control.
    for diff_path in _iter_diff_paths(candidate.diff):
        sub = classify_path(Path(diff_path))
        if sub.is_control:
            is_control = True
            reasons.append(f"diff_refs_control:{diff_path}")
        for s in sub.surfaces:
            if s not in surfaces:
                surfaces.append(s)

    cls = SurfaceClassification(
        is_control=is_control,
        surfaces=tuple(surfaces),
        rationale="; ".join(r for r in reasons if r) or None,
    )

    # Emit telemetry via MetaTuneEvent (no ad-hoc jsonl).
    try:
        MetaTuneEvent(
            session_id=candidate.proposer_id,
            payload={
                "action": "surface_classify",
                "edit_id": candidate.edit_id,
                "surface_classification_result": "control" if cls.is_control else "advisory",
                "surfaces": [s.value for s in cls.surfaces],
            },
        )
    except Exception:  # justified: telemetry_best_effort, safety-critical classification must never raise
        logger.warning(
            "surface_classify_telemetry_failed",
            component="meta_tune.surface_registry",
            op="classify_candidate",
            outcome="degraded",
        )
    return cls


__all__ = [
    "Surface",
    "SurfaceClassification",
    "classify_candidate",
    "classify_path",
]
