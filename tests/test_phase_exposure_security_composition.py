"""Must-not composition: a security-excluded tool is never re-surfaced by a
phase override — PRD-INTENT-002 (round-2 audit I2-F04).

The chain order (FR08) is ``MCPSecurity → ... → PhaseExposure``. A tool dropped
by the security allowlist filter is removed from the catalogue BEFORE the phase
layer sees it, so a phase override grant — which can only re-surface tools that
survived the upstream filter — cannot bring it back. This proves the two layers
COMPOSE (intersection semantics), not override each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.middleware.mcp_security import (
    AdvertisedTool,
    MCPSecurityMiddleware,
    normalize_tool_name,
)
from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware
from trw_mcp.models.phase_policy import DEFAULT_PHASE_POLICY
from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig
from trw_mcp.security.capability_scope import CapabilityScope
from trw_mcp.security.mcp_registry import AllowedTool, MCPAllowlist, MCPServer

pytestmark = pytest.mark.integration

# The tool deliberately left OUT of the security preset. A non-first-party
# name is used so the registry's first-party (``trw_*``) fallback admission does
# NOT re-admit it — the preset exclusion is the only authorization signal, which
# is exactly the boundary the phase override must not be able to cross.
_EXCLUDED_TOOL = "exec_shell"
# A tool inside the preset (sanity that the filter is not nuking everything).
_INCLUDED_TOOL = "trw_recall"


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeContext:
    _session_id: str = "sess-compose"

    @property
    def session_id(self) -> str:
        return self._session_id


@dataclass
class _FakeMiddlewareContext:
    message: Any = None
    fastmcp_context: _FakeContext | None = None


def _security_with_named_preset(tmp_path: Path) -> MCPSecurityMiddleware:
    """A security middleware whose preset admits trw_recall but NOT trw_review."""
    allowlist = MCPAllowlist(
        servers=[
            MCPServer(
                name="trw",
                url_or_command="trw-mcp",
                public_key_fingerprint="sha256:trw",
                allowed_tools=[
                    AllowedTool(name=_INCLUDED_TOOL, allowed_scopes=("read",)),
                ],
            ),
        ]
    )
    cfg = AnomalyDetectorConfig(shadow_clock_path=tmp_path / "sec" / "clock.yaml")
    det = AnomalyDetector(config=cfg, run_dir=None, fallback_dir=tmp_path)
    return MCPSecurityMiddleware(
        allowlist=allowlist,
        scopes={
            _INCLUDED_TOOL: CapabilityScope(
                server_name="trw",
                tool_name=_INCLUDED_TOOL,
                allowed_scopes=("read",),
            ),
        },
        anomaly_detector=det,
        run_dir=None,
        fallback_dir=tmp_path,
    )


@pytest.mark.asyncio
async def test_security_excluded_tool_not_resurfaced_by_phase_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2-F04: a preset-excluded tool stays gone even with an active phase override.

    Construct: preset filtering ON via a named allowlist that omits trw_review;
    grant a phase override for trw_review; run the catalogue through the security
    filter (upstream) then through PhaseExposure (downstream). The composed result
    must still exclude trw_review.
    """
    from trw_mcp.tools import phase_overrides

    security = _security_with_named_preset(tmp_path)
    phase = PhaseExposureMiddleware(enabled=True, policy=DEFAULT_PHASE_POLICY)

    # Force a phase where trw_review would otherwise be masked, and grant an
    # override that — absent composition — would re-surface it.
    monkeypatch.setattr(
        "trw_mcp.middleware.phase_exposure.resolve_active_phase",
        lambda **_: "RESEARCH",
    )
    phase_overrides.reset_overrides()
    phase_overrides.grant_override("sess-compose", _EXCLUDED_TOOL, reason="x" * 25)

    full_catalogue = [_FakeTool(name=_INCLUDED_TOOL), _FakeTool(name=_EXCLUDED_TOOL)]

    # Stage 1: upstream MCPSecurity filter drops trw_review (outside the preset).
    allowed_ads = security.filter_advertised_tools(
        transport="stdio",
        advertisements=[AdvertisedTool(server="trw", name=t.name) for t in full_catalogue],
    )
    allowed_names = {ad.name for ad in allowed_ads}
    security_survivors = [t for t in full_catalogue if normalize_tool_name(t.name) in allowed_names]
    survivor_names = {t.name for t in security_survivors}
    assert _INCLUDED_TOOL in survivor_names
    assert _EXCLUDED_TOOL not in survivor_names, "security preset must drop trw_review"

    # Stage 2: PhaseExposure runs on the SECURITY OUTPUT (chain order), with an
    # active override for the excluded tool.
    async def call_next(_ctx: Any) -> Any:
        return security_survivors

    ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    listed = await phase.on_list_tools(ctx, call_next)  # type: ignore[arg-type]
    listed_names = {t.name for t in listed}

    # The override cannot resurrect a tool the security layer already removed.
    assert _EXCLUDED_TOOL not in listed_names, "phase override must NOT re-surface a security-excluded tool"

    # Positive control (non-vacuity): if the excluded tool DID reach the phase
    # layer's input, the same active override WOULD re-surface it — proving the
    # exclusion above is the security filter's doing, not a no-op override.
    async def call_next_with_excluded(_ctx: Any) -> Any:
        return [_FakeTool(name=_INCLUDED_TOOL), _FakeTool(name=_EXCLUDED_TOOL)]

    control = await phase.on_list_tools(ctx, call_next_with_excluded)  # type: ignore[arg-type]
    assert _EXCLUDED_TOOL in {t.name for t in control}, (
        "control: the override should surface the tool when it is NOT pre-filtered"
    )
    phase_overrides.reset_overrides()
