"""Every client profile is handled -- or explicitly exempted -- in the enumerations that
otherwise miss a new client SILENTLY (lane B grok checklist, board 817).

A client added to ``_PROFILES`` but absent from these tables does not fail anywhere:
it falls back to tier T1, to a raw tier token passed through to its harness, or to no
env-signal detection. Each exemption below states why that fallback is correct for that
client, so adding a client (e.g. grok) forces a decision here instead of a silent default.
"""

from __future__ import annotations

import pytest

from trw_mcp.agents import tier_resolver
from trw_mcp.models.config._profiles import _PROFILES
from trw_mcp.state import source_detection
from trw_mcp.tools._client_detection import _CLIENT_DEFAULT_TIER

pytestmark = pytest.mark.unit

#: Profiles whose harness takes the TRW tier vocabulary (or surfaces it at the
#: destination), so ``resolve_tier`` passes the tier through unchanged.
TIER_PASSTHROUGH_EXEMPT: dict[str, str] = {
    "codex": "passthrough by tier_resolver's KNOWN_CLIENTS rule; no adapter table written yet",
    "copilot": "copilot AgentFormat drops the model key, so no tier reaches its agent files",
    "cursor-cli": "passthrough by tier_resolver's KNOWN_CLIENTS rule; no adapter table written yet",
    "opencode": "passthrough: resolve_tier docstring pins opencode -> tier unchanged",
    "grok": "grok AgentFormat drops the model key until the OQ-3 probe, so no tier reaches its agent files",
}

#: Profiles with no auto-injected environment variable; detection uses filesystem
#: markers (``bootstrap._utils.detect_ide``) instead of ``_CLIENT_SIGNALS``.
ENV_SIGNAL_EXEMPT: dict[str, str] = {
    "antigravity-cli": "no auto-injected env var; detect_ide uses .antigravitycli/ or ANTIGRAVITY.md",
    "copilot": "no auto-injected env var; detect_ide uses .github/copilot-instructions.md or .github/agents",
    "cursor-cli": "uses user-set CURSOR_API_KEY, not auto-injected (source_detection comment)",
    "grok": (
        "no known auto-injected env var; detect_ide requires .grok/config.toml to parse "
        "and carry [mcp_servers.trw] (a bare .grok/ dir is scaffolded by TRW itself)"
    ),
}


def test_every_profile_has_a_default_tool_return_tier() -> None:
    assert set(_PROFILES) - set(_CLIENT_DEFAULT_TIER) == set(), "a missing entry silently falls back to T1"


def test_every_profile_has_a_model_tier_map_or_a_stated_passthrough() -> None:
    handled = set(tier_resolver._CLIENT_MAPS) | set(TIER_PASSTHROUGH_EXEMPT)
    assert set(_PROFILES) - handled == set(), "decide: add a _CLIENT_MAPS table or a stated passthrough"
    assert set(TIER_PASSTHROUGH_EXEMPT) & set(tier_resolver._CLIENT_MAPS) == set(), "an exemption became stale"


def test_every_profile_has_an_env_signal_or_a_stated_marker_route() -> None:
    signalled = {client for client, _keys in source_detection._CLIENT_SIGNALS}
    assert set(_PROFILES) - (signalled | set(ENV_SIGNAL_EXEMPT)) == set(), "decide: add an env signal or exempt"
    assert set(ENV_SIGNAL_EXEMPT) & signalled == set(), "an exemption became stale"


def test_exemptions_name_only_real_profiles() -> None:
    assert set(TIER_PASSTHROUGH_EXEMPT) <= set(_PROFILES)
    assert set(ENV_SIGNAL_EXEMPT) <= set(_PROFILES)
