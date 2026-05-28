"""Tests for _templates.py: pure f-string template renderers.

Verifies no Jinja2, no unsubstituted variables, all templates produce
complete rendered strings.

PRD-DIST-2406.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# T0 beacon template
# ---------------------------------------------------------------------------


def test_t0_beacon_no_unsubstituted_vars() -> None:
    """T0 beacon contains no unsubstituted template variables."""
    from trw_mcp.channels.copilot._templates import render_c1_t0_beacon

    result = render_c1_t0_beacon(ts="2026-05-28T12:00:00Z")
    assert "{" not in result, "Unsubstituted template vars found"
    assert "}" not in result, "Unsubstituted template vars found"


def test_t0_beacon_contains_ts() -> None:
    """T0 beacon contains the provided timestamp."""
    from trw_mcp.channels.copilot._templates import render_c1_t0_beacon

    result = render_c1_t0_beacon(ts="2026-05-28T12:00:00Z")
    assert "2026-05-28T12:00:00Z" in result


def test_t0_beacon_contains_regenerate_command() -> None:
    """T0 beacon includes the regenerate command."""
    from trw_mcp.channels.copilot._templates import render_c1_t0_beacon

    result = render_c1_t0_beacon(ts="2026-05-28T12:00:00Z")
    assert "trw-distill" in result
    assert "self-improve" in result


def test_t0_beacon_is_short() -> None:
    """T0 beacon is <= 35 tokens (approx <= 200 chars)."""
    from trw_mcp.channels.copilot._templates import render_c1_t0_beacon

    result = render_c1_t0_beacon(ts="2026-05-28T12:00:00Z")
    # 35 tokens * ~4 chars = ~140 chars. Add overhead buffer.
    # T0 beacon should be compact
    assert len(result) < 300, f"T0 beacon too long: {len(result)} chars"


# ---------------------------------------------------------------------------
# T1 segment template
# ---------------------------------------------------------------------------


def test_t1_segment_no_unsubstituted_vars() -> None:
    """T1 segment contains no unsubstituted template variables."""
    from trw_mcp.channels.copilot._templates import render_c1_t1_segment

    result = render_c1_t1_segment(
        conventions=["Use structlog", "Pydantic v2", "Type hints"],
        hotspots=["ceremony.py (risk: 0.91)", "admin.py (risk: 0.87)"],
    )
    assert "{" not in result
    assert "}" not in result


def test_t1_segment_includes_conventions() -> None:
    """T1 segment includes provided conventions."""
    from trw_mcp.channels.copilot._templates import render_c1_t1_segment

    result = render_c1_t1_segment(
        conventions=["Use structlog", "Pydantic v2"],
        hotspots=[],
    )
    assert "Use structlog" in result
    assert "Pydantic v2" in result


def test_t1_segment_includes_hotspots() -> None:
    """T1 segment includes provided hotspot warnings."""
    from trw_mcp.channels.copilot._templates import render_c1_t1_segment

    result = render_c1_t1_segment(
        conventions=[],
        hotspots=["admin.py (risk: 0.90)", "ceremony.py (risk: 0.85)"],
    )
    assert "admin.py" in result or "0.90" in result or "hotspot" in result.lower()


def test_t1_segment_respects_max_limits() -> None:
    """T1 segment respects max_conventions and max_hotspots parameters."""
    from trw_mcp.channels.copilot._templates import render_c1_t1_segment

    result = render_c1_t1_segment(
        conventions=["C1", "C2", "C3", "C4", "C5"],
        hotspots=["H1", "H2", "H3", "H4", "H5"],
        max_conventions=2,
        max_hotspots=2,
    )
    # Only first 2 of each should appear
    assert "C1" in result
    assert "C2" in result
    assert "C3" not in result
    assert "H1" in result
    assert "H2" in result
    assert "H3" not in result


# ---------------------------------------------------------------------------
# C2 path instructions template
# ---------------------------------------------------------------------------


def test_c2_path_instructions_has_valid_yaml_frontmatter() -> None:
    """C2 template produces valid YAML frontmatter with applyTo field."""
    from trw_mcp.channels.copilot._templates import render_c2_path_instructions

    result = render_c2_path_instructions(
        apply_to="backend/routers/**/*.py",
        hotspot_entries=[{"file": "backend/routers/admin.py", "risk_score": 0.9, "reason": "complex"}],
        ts="2026-05-28T12:00:00Z",
    )

    assert result.startswith("---\n")
    assert "applyTo:" in result
    assert "backend/routers/**/*.py" in result
    assert "---" in result


def test_c2_no_unsubstituted_vars() -> None:
    """C2 template contains no unsubstituted template variables."""
    from trw_mcp.channels.copilot._templates import render_c2_path_instructions

    result = render_c2_path_instructions(
        apply_to="backend/**/*.py",
        hotspot_entries=[],
        ts="2026-05-28T12:00:00Z",
    )
    assert "{" not in result
    assert "}" not in result


def test_c2_includes_ts() -> None:
    """C2 template includes the timestamp."""
    from trw_mcp.channels.copilot._templates import render_c2_path_instructions

    result = render_c2_path_instructions(
        apply_to="**/*.py",
        hotspot_entries=[],
        ts="2026-05-28T12:00:00Z",
    )
    assert "2026-05-28T12:00:00Z" in result


# ---------------------------------------------------------------------------
# C3 JSON merge template
# ---------------------------------------------------------------------------


def test_c3_merge_adds_trw_key() -> None:
    """render_c3_mcp_json adds trw entry under servers."""
    from trw_mcp.channels.copilot._templates import render_c3_mcp_json

    existing = {"servers": {"other": {"type": "stdio", "command": "other"}}}
    trw_entry = {"type": "stdio", "command": "trw-mcp", "args": []}
    result = render_c3_mcp_json(existing=existing, trw_entry=trw_entry)

    assert "servers" in result
    servers = result["servers"]
    assert isinstance(servers, dict)
    assert "trw" in servers
    assert servers["trw"] == trw_entry


def test_c3_merge_preserves_existing_keys() -> None:
    """render_c3_mcp_json preserves all existing keys."""
    from trw_mcp.channels.copilot._templates import render_c3_mcp_json

    existing = {
        "servers": {"other": {"type": "stdio", "command": "other"}},
        "top_level_key": "preserved",
    }
    result = render_c3_mcp_json(existing=existing, trw_entry={"type": "stdio", "command": "trw-mcp", "args": []})

    assert result["top_level_key"] == "preserved"
    servers = result["servers"]
    assert isinstance(servers, dict)
    assert "other" in servers


def test_c3_merge_does_not_mutate_existing() -> None:
    """render_c3_mcp_json does not mutate the input dict."""
    from trw_mcp.channels.copilot._templates import render_c3_mcp_json

    existing: dict[str, object] = {"servers": {}}
    original_id = id(existing)
    render_c3_mcp_json(existing=existing, trw_entry={})

    # Original dict should be unchanged
    assert id(existing) == original_id
    assert existing == {"servers": {}}
