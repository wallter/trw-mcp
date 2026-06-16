"""Phase-aware tool-exposure config fields — PRD-INTENT-002.

Mixed into ``_TRWConfigFields`` via multiple inheritance. Kept as its own small
mixin (well under the 200-raw-line domain-mixin gate enforced by
``tests/test_config_fields.py``); ``_fields_ceremony.py`` is at its 199/200 cap
so a new mixin is required.

``phase_exposure_enabled`` gates ``PhaseExposureMiddleware`` filtering. Default
``False`` for the v1 rollout (PRD §9 Stage 1 — opt-in for eval runs only); the
per-phase tool policy itself is the resolved profile's
``allowed_tools_by_phase`` (PROF-001 single source of truth), NOT a parallel
config surface.
"""

from __future__ import annotations


class _PhaseExposureFields:
    """Phase-exposure domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Phase-aware tool exposure (PRD-INTENT-002) --

    phase_exposure_enabled: bool = False
