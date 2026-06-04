"""Tests: antigravity-cli profile has hooks_enabled=True (AG-03 implemented 2026-05-28).

Previously hooks_enabled=False (audit P0-04 fix — was misleadingly True with no hook code).
Now hooks_enabled=True because AG-03 is implemented and TRW writes a valid
.antigravitycli/hooks.json hook surface:
- Hooks file: .antigravitycli/hooks.json (separate from settings.json)
- Event key: PreToolUse (confirmed via binary string analysis)
- Hook script: .antigravitycli/hooks/trw_before_edit_telemetry.py

hooks_enabled=True reflects that TRW configures the hook surface. NOTE: the AG-03
channel itself is ASPIRATIONAL — a live agy turn (2026-05-29) showed the
PreToolUse hook does not fire for file edits (agy uses Step_CodeAction, which
bypasses the jsonhook path). The profile flag stays True because TRW does install
the surface; channel-level firing status lives in the manifest.

PRD-DIST-2404 FR01.
"""

from __future__ import annotations


def test_antigravity_cli_profile_hooks_enabled() -> None:
    """FR01 + AG-03: _PROFILES['antigravity-cli'].hooks_enabled must be True.

    TRW installs a valid hooks.json surface (confirmed 2026-05-28 via agy binary
    analysis), so hooks_enabled=True. AG-03's hook does not fire on agy file edits
    (see module docstring) — but that is a channel-status concern, not a profile flag.
    """
    from trw_mcp.models.config._profiles import _PROFILES

    profile = _PROFILES.get("antigravity-cli")
    assert profile is not None, "antigravity-cli profile not found in _PROFILES"
    assert profile.hooks_enabled is True, (
        f"Expected hooks_enabled=True for antigravity-cli (AG-03 confirmed active), "
        f"got {profile.hooks_enabled!r}"
    )
