"""Tests: antigravity-cli profile has hooks_enabled=True (AG-03 activated 2026-05-28).

Previously hooks_enabled=False (audit P0-04 fix — was misleadingly True with no hook code).
Now hooks_enabled=True because AG-03 is implemented after empirical confirmation of
the agy v1.0.2 hook surface:
- Hooks file: .antigravitycli/hooks.json (separate from settings.json)
- Event key: PreToolUse (confirmed via binary string analysis)
- Hook script: .antigravitycli/hooks/trw_before_edit_telemetry.py

PRD-DIST-2404 FR01, OQ-01 resolved, AG-03 active.
"""

from __future__ import annotations


def test_antigravity_cli_profile_hooks_enabled() -> None:
    """FR01 + AG-03: _PROFILES['antigravity-cli'].hooks_enabled must be True.

    AG-03 is now active (hooks.json confirmed 2026-05-28 via agy v1.0.2 binary analysis).
    hooks_enabled=True reflects the real implementation state.
    """
    from trw_mcp.models.config._profiles import _PROFILES

    profile = _PROFILES.get("antigravity-cli")
    assert profile is not None, "antigravity-cli profile not found in _PROFILES"
    assert profile.hooks_enabled is True, (
        f"Expected hooks_enabled=True for antigravity-cli (AG-03 confirmed active), "
        f"got {profile.hooks_enabled!r}"
    )
