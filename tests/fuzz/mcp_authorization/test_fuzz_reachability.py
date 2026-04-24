"""Fuzz tests for MCP authorization middleware (PRD-INFRA-SEC-001 §7.3 / FR-9).

Bounded Hypothesis-driven fuzzers covering the three reachability paths
(stdio / HTTP / SSE) × three middleware layers (registry, capability_scope,
anomaly_detector). ``max_examples=50`` fits CI budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trw_mcp.middleware.mcp_security import (
    CLAUDE_CODE_PREFIX,
    TRANSPORTS,
    AdvertisedTool,
    MCPSecurityMiddleware,
)
from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig
from trw_mcp.security.mcp_registry import MCPAllowlist, MCPServer

pytestmark = pytest.mark.integration


def _mw(tmp_path: Path) -> MCPSecurityMiddleware:
    allowlist = MCPAllowlist(
        servers=[
            MCPServer(
                name="trw",
                url_or_command="trw-mcp",
                signer="trw-maintainer",
                signature="stub:v1",
                trust_level="verified",
                capabilities=["trw_recall", "trw_learn"],
            ),
            MCPServer(
                name="filesystem",
                url_or_command="npx fs",
                signer="trw-maintainer",
                signature="stub:v1",
                trust_level="verified",
                capabilities=["read_file", "write_file"],
            ),
        ]
    )
    det = AnomalyDetector(
        config=AnomalyDetectorConfig(shadow_clock_path=tmp_path / "sec" / "c.yaml"),
        run_dir=None,
        fallback_dir=tmp_path,
    )
    return MCPSecurityMiddleware(
        allowlist=allowlist,
        scopes={},
        anomaly_detector=det,
        run_dir=None,
        fallback_dir=tmp_path,
    )


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    raw_name=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), min_codepoint=32, max_codepoint=126),
        min_size=0,
        max_size=60,
    ),
    prefix=st.sampled_from(["", CLAUDE_CODE_PREFIX, "random_prefix__"]),
    server=st.sampled_from(["trw", "filesystem", "evil", "ghost", ""]),
)
def test_fuzz_stdio_never_raises(
    tmp_path: Path, raw_name: str, prefix: str, server: str
) -> None:
    """stdio transport: fuzz random tool names; middleware must never raise."""
    mw = _mw(tmp_path)
    tool = f"{prefix}{raw_name}" if raw_name else "placeholder"
    mw.on_tool_call(transport="stdio", server=server, tool=tool, args={})


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    transport=st.sampled_from(TRANSPORTS),
    tool=st.text(min_size=1, max_size=40),
    server=st.sampled_from(["trw", "filesystem", "evil"]),
)
def test_fuzz_all_transports_fire_three_layers(
    tmp_path: Path, transport: str, tool: str, server: str
) -> None:
    """FR-9: every (transport × input) triple hits all three layers."""
    mw = _mw(tmp_path)
    decision = mw.on_tool_call(
        transport=transport,  # type: ignore[arg-type]
        server=server,
        tool=tool,
        args={},
    )
    assert set(decision.layers_fired) == {"registry", "capability_scope", "anomaly_detector"}


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    mutated_signature=st.text(min_size=0, max_size=120),
    mutated_signer=st.text(min_size=1, max_size=40),
)
def test_fuzz_signature_drift_never_bypasses(
    tmp_path: Path, mutated_signature: str, mutated_signer: str
) -> None:
    """Signature drift on an allowlist entry does NOT bypass observe-mode.

    Because v1 verify_signature is a stub, signature drift is logged but not
    enforced — the middleware still emits a decision. This test confirms the
    middleware never crashes on mutated signature material.
    """
    allowlist = MCPAllowlist(
        servers=[
            MCPServer(
                name="trw",
                url_or_command="trw-mcp",
                signer=mutated_signer,
                signature=mutated_signature,
                trust_level="verified",
                capabilities=["trw_recall"],
            ),
        ]
    )
    det = AnomalyDetector(
        config=AnomalyDetectorConfig(shadow_clock_path=tmp_path / "sec" / "c.yaml"),
        run_dir=None,
        fallback_dir=tmp_path,
    )
    mw = MCPSecurityMiddleware(
        allowlist=allowlist,
        scopes={},
        anomaly_detector=det,
        run_dir=None,
        fallback_dir=tmp_path,
    )
    decision = mw.on_tool_call(
        transport="sse", server="trw", tool="trw_recall", args={}
    )
    assert decision.allowed is True


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    count=st.integers(min_value=1, max_value=20),
    server=st.sampled_from(["trw", "filesystem"]),
)
def test_fuzz_advertise_filter_handles_random_batches(
    tmp_path: Path, count: int, server: str
) -> None:
    """Advertise-time filter never raises on random batches of tool names."""
    mw = _mw(tmp_path)
    ads = [
        AdvertisedTool(server=server, name=f"{CLAUDE_CODE_PREFIX}tool_{i}")
        for i in range(count)
    ]
    out = mw.filter_advertised_tools(transport="http", advertisements=ads)
    # All outputs must be short-name normalized
    for ad in out:
        assert not ad.name.startswith(CLAUDE_CODE_PREFIX)
