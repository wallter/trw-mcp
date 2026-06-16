"""F-03 — allowed_tools_by_phase single-source-of-truth GREP contract (FR-14).

PROF-001's seam declares ``allowed_tools_by_phase`` as a POLICY surface with a
SINGLE source of truth: the resolved profile. INTENT-002's phase-exposure
middleware (and its ``phase_policy`` model) DERIVE their per-phase visibility
from that one field — they MUST NOT declare a parallel allowlist surface.

This is the grep-style assertion the seam's ``wiring_test`` claim depends on:
it scans the production source tree and proves that the ONLY place
``allowed_tools_by_phase`` is *declared as a profile override surface field* is
``profile/model.py``. Every other occurrence is a derived consumer reading that
single surface, not a competing declaration.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.profile import PROFILE_SURFACE_KEYS, Profile, ProfileLayer, compose

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "trw_mcp"


def _python_sources() -> list[Path]:
    return [p for p in _SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def test_allowlist_declared_only_on_profile_model() -> None:
    """FR-14 grep contract: the override-surface allowlist is declared ONCE.

    The surface key ``allowed_tools_by_phase`` is a typed *override field* on
    exactly one model — ``Profile`` in ``profile/model.py``. We grep for the
    field-declaration shape (``allowed_tools_by_phase: dict[...]``) across the
    tree; only the Profile declaration may carry the optional ``| None = None``
    override-surface form. Other modules (phase_policy / phase_exposure) read
    the field but declare their own derived ``dict[str, list[str]]`` mapping,
    which is the CONSUMER shape, not the override-surface shape.
    """
    override_decls: list[str] = []
    for src in _python_sources():
        text = src.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            # The override-surface declaration is optional+inheritable:
            # ``allowed_tools_by_phase: dict[PhaseName, list[str]] | None = None``
            if stripped.startswith("allowed_tools_by_phase:") and "| None = None" in stripped:
                override_decls.append(f"{src.relative_to(_SRC_ROOT)}: {stripped}")

    assert len(override_decls) == 1, (
        "allowed_tools_by_phase override surface must be declared exactly once "
        f"(the Profile policy field); found: {override_decls}"
    )
    assert override_decls[0].startswith("profile/model.py")


def test_allowlist_single_source_of_truth_contract() -> None:
    """FR-14: the allowlist resolves through the profile chain (model contract).

    Mirrors the unit-tier contract test the PROF-001 seam references — proving
    the resolved profile is the single surface a consumer reads, with the
    later layer winning and attribution tracing the origin layer.
    """
    resolved = compose(
        [
            ProfileLayer(
                name="defaults",
                overrides=Profile(allowed_tools_by_phase={"RESEARCH": ["trw_recall"]}),
            ),
            ProfileLayer(
                name="task-type",
                overrides=Profile(allowed_tools_by_phase={"IMPLEMENT": ["trw_learn", "trw_checkpoint"]}),
            ),
        ]
    )
    allow = resolved.profile.allowed_tools_by_phase
    assert allow == {"IMPLEMENT": ["trw_learn", "trw_checkpoint"]}
    assert resolved.attribution["allowed_tools_by_phase"].origin_layer == "task-type"
    # And the key is a first-class member of the published surface.
    assert "allowed_tools_by_phase" in PROFILE_SURFACE_KEYS
