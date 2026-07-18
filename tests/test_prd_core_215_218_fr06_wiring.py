"""On-disk wiring proof for PRD-CORE-215-FR06 + PRD-CORE-218-FR06.

The transport-loss retry protocol and the three-class capability listing are
rendered by ``bootstrap/_client_integrations.py``. These tests prove the
renderers are WIRED into the production instruction-generation path: they run
the real AGENTS.md writer (``execute_claude_md_sync`` — the entrypoint
``trw_instructions_sync`` drives) into ``tmp_path`` and assert the generated
file on disk carries both markers, all four transport-loss boundaries, and the
three capability classes. The sync result must also surface the capability
parity check so lifecycle/count drift fails loudly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

# Four client-observed transport-loss boundary titles (PRD-CORE-215-FR06).
_FOUR_BOUNDARIES = (
    "Connection lost before the server acknowledged acceptance",
    "Connection lost after a durable handle or receipt was returned",
    "A response arrived but was malformed or unparseable",
    "Server restarted",
)

# Three capability classes (PRD-CORE-218-FR06).
_THREE_CLASSES = (
    "Available now (kernel + selected packs)",
    "Discoverable via trw_skill_discovery / trw_request_tool_access",
    "Operator-grant only",
)


def _make_sync_args(tmp_path: Path) -> dict[str, object]:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    (trw_dir / "patterns").mkdir(exist_ok=True)

    llm = MagicMock()
    llm.available = False
    return {
        "scope": "root",
        "target_dir": None,
        "config": TRWConfig(trw_dir=str(trw_dir)),
        "reader": FileStateReader(),
        "llm": llm,
    }


def _run_sync(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    from trw_mcp.state.claude_md._sync import execute_claude_md_sync

    args = _make_sync_args(tmp_path)
    args.update(kwargs)
    with (
        patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path / ".trw"),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state.analytics.update_analytics_sync"),
    ):
        return execute_claude_md_sync(**args)  # type: ignore[arg-type]


def test_generated_agents_md_carries_transport_loss_and_capabilities(tmp_path: Path) -> None:
    """The real AGENTS.md writer emits both FR06 blocks (opencode/generic surface)."""
    result = _run_sync(tmp_path, client="all")

    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.exists(), "AGENTS.md should be generated for client=all"
    content = agents_md.read_text(encoding="utf-8")

    # PRD-CORE-215-FR06: transport-loss marker + all four boundaries.
    assert "<!-- trw:transport-loss:agents -->" in content
    assert "MCP transport-loss retry protocol" in content
    for boundary in _FOUR_BOUNDARIES:
        assert boundary in content, f"missing transport-loss boundary: {boundary}"
    # The retry-once-then-record-gap safeguard rides along.
    assert "record the gap" in content

    # PRD-CORE-218-FR06: capabilities marker + all three classes.
    assert "<!-- trw:capabilities:agents -->" in content
    for label in _THREE_CLASSES:
        assert label in content, f"missing capability class: {label}"
    # Derived from the LIVE surface manifest: a kernel tool appears "available".
    assert "trw_session_start" in content


def test_generated_codex_agents_md_carries_both_blocks(tmp_path: Path) -> None:
    """The Codex AGENTS.md surface carries both FR06 blocks with a codex marker."""
    (tmp_path / ".codex").mkdir()

    result = _run_sync(tmp_path, client="codex")

    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.exists()
    content = agents_md.read_text(encoding="utf-8")

    assert "<!-- trw:transport-loss:codex -->" in content
    assert "<!-- trw:capabilities:codex -->" in content
    for boundary in _FOUR_BOUNDARIES:
        assert boundary in content
    for label in _THREE_CLASSES:
        assert label in content
    assert result["agents_md_synced"] is True


def test_sync_result_surfaces_capability_parity_check(tmp_path: Path) -> None:
    """PRD-CORE-218-FR06: the sync result carries the capability parity verdict.

    Present (empty on the clean live manifest) whenever AGENTS.md is written, so
    a future lifecycle/count drift surfaces loudly in the sync result rather than
    shipping a drifted capability claim silently.
    """
    result = _run_sync(tmp_path, client="all")

    assert "capability_parity_drift" in result
    assert result["capability_parity_drift"] == []


def test_capability_appendix_drops_block_on_parity_drift() -> None:
    """On parity drift the capability block is dropped fail-loud; transport stays.

    Injects a synthetic parity failure so the drop-on-drift branch is exercised
    genuinely: the ``trw:capabilities`` block must be omitted (no drifted claim
    ships) while the manifest-independent transport-loss block still renders and
    the failure detail is surfaced on the returned appendix.
    """
    from trw_mcp.bootstrap import _client_integration_appendix as appendix_mod
    from trw_mcp.bootstrap._client_integrations import (
        ProjectionDriftKind,
        ProjectionParityFailure,
    )

    drift = (ProjectionParityFailure(ProjectionDriftKind.LIFECYCLE_DRIFT, "trw_defunct is retired but still listed"),)

    with patch.object(appendix_mod, "check_projection_parity", return_value=drift):
        built = appendix_mod.build_client_integration_appendix("agents")

    # Drift detected → capability block dropped, but transport block always present.
    assert built.parity_failures == drift
    assert "<!-- trw:capabilities:agents -->" not in built.text
    assert "<!-- trw:transport-loss:agents -->" in built.text


def test_clean_live_manifest_yields_no_parity_drift() -> None:
    """The real surface manifest resolves to a parity-clean capability projection."""
    from trw_mcp.bootstrap._client_integration_appendix import (
        build_client_integration_appendix,
    )

    built = build_client_integration_appendix("agents")
    assert built.parity_failures == ()
    assert "<!-- trw:capabilities:agents -->" in built.text
