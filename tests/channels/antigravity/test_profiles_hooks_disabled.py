"""Tests: antigravity-cli profile has hooks_enabled=True (AG-03 implemented 2026-05-28).

Previously hooks_enabled=False (audit P0-04 fix — was misleadingly True with no hook code).
Now hooks_enabled=True because AG-03 is implemented and TRW writes a valid
.agents/hooks.json hook surface (named-hook schema, agy 1.2.x):
- Hooks file: .agents/hooks.json (separate from settings.json)
- Event key: PreToolUse
- Hook script: .agents/hooks/trw_before_edit_telemetry.py

Verified live on agy 1.2.15 (2026-10-03): the hook fires for write_to_file.

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
        f"Expected hooks_enabled=True for antigravity-cli (AG-03 confirmed active), got {profile.hooks_enabled!r}"
    )
