"""Tests for channels/antigravity/_explorer_subagent.py.

PRD-DIST-2404 FR07-FR11, FR14, FR18.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_sidecar(
    hotspot_count: int = 5,
    convention_count: int = 3,
) -> dict[str, Any]:
    return {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": [
            {
                "file": f"src/mod_{i}.py",
                "risk_score": round(0.9 - i * 0.05, 2),
                "churn": 20 - i,
                "caller_count": 5 - i,
            }
            for i in range(hotspot_count)
        ],
        "conventions": [f"Convention {i}: use structlog for logging" for i in range(convention_count)],
    }


# ---------------------------------------------------------------------------
# FR07: YAML frontmatter strict parse
# ---------------------------------------------------------------------------


def test_yaml_frontmatter_valid_strict_parser(tmp_path: Path) -> None:
    """FR07 + PRD-CORE-252 follow-up: frontmatter is the registry's antigravity-cli
    shape -- name/description/model only, nothing that client's own subagent
    reference (antigravity.google/docs/subagents) does not document."""
    from trw_mcp.channels.antigravity._explorer_subagent import (
        generate_distill_explorer_agent,
    )

    sidecar = _make_sidecar()
    result = generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_yaml",
    )

    assert result.status == "written"
    agent_path = tmp_path / ".agents" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists()

    content = agent_path.read_text()
    # Extract frontmatter between first --- pair.
    parts = content.split("---")
    assert len(parts) >= 3, "Expected at least two --- delimiters for frontmatter"
    frontmatter_text = parts[1]

    parsed = yaml.safe_load(frontmatter_text)
    assert isinstance(parsed, dict)
    for field in ("name", "description", "model"):
        assert field in parsed, f"Missing required frontmatter field: {field}"
    # The registry drops these for antigravity-cli: literal Gemini ids and
    # per-agent timing/tool knobs that client's reference does not accept.
    for field in ("tools", "temperature", "max_turns", "timeout_mins", "disallowedTools", "effort", "memory"):
        assert field not in parsed, f"Unsupported frontmatter field {field!r} leaked into antigravity-cli output"
    assert parsed["model"] in {"inherit", "flash", "pro"}, f"model {parsed['model']!r} not in the documented set"


def test_frontmatter_name_is_trw_distill_explorer(tmp_path: Path) -> None:
    """FR07: frontmatter name is 'trw-distill-explorer'."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar = _make_sidecar()
    generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_name",
    )

    agent_path = tmp_path / ".agents" / "agents" / "trw-distill-explorer.md"
    content = agent_path.read_text()
    parts = content.split("---")
    parsed = yaml.safe_load(parts[1])
    assert parsed["name"] == "trw-distill-explorer"


# ---------------------------------------------------------------------------
# FR08: default tier is T1 (NOT T2)
# ---------------------------------------------------------------------------


def test_default_tier_is_t1(tmp_path: Path) -> None:
    """FR08: default tier is T1, not T2 (audit P1-15)."""
    from trw_mcp.channels.antigravity._explorer_subagent import (
        _DEFAULT_TIER,
        generate_distill_explorer_agent,
    )

    assert _DEFAULT_TIER == "T1"

    sidecar = _make_sidecar()
    result = generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_tier",
    )

    assert result.tier_used == "T1"


def test_t2_tier_override_accepted(tmp_path: Path) -> None:
    """FR08: T2 tier override is accepted via tier_override."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar = _make_sidecar()
    result = generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_t2",
        tier_override="T2",
    )

    assert result.status == "written"
    assert result.tier_used == "T2"


# ---------------------------------------------------------------------------
# FR09: idempotent skip on same SHA
# ---------------------------------------------------------------------------


def test_idempotent_skip_on_same_sha(tmp_path: Path) -> None:
    """FR09: second write with same sidecar SHA is skipped."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar = _make_sidecar()

    result1 = generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="stable-sha",
    )
    assert result1.status == "written"

    result2 = generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="stable-sha",
    )
    assert result2.status == "skipped_same_sha"


# ---------------------------------------------------------------------------
# FR10: no mutation tools in tools list
# ---------------------------------------------------------------------------


def test_no_mutation_tools(tmp_path: Path) -> None:
    """FR10: write_file, edit_file, trw_deliver must not appear in tools list."""
    from trw_mcp.channels.antigravity._explorer_subagent import (
        _AGENT_TOOLS,
        _MUTATION_TOOLS,
    )

    for mut in _MUTATION_TOOLS:
        assert mut not in _AGENT_TOOLS, f"Mutation tool {mut!r} found in _AGENT_TOOLS"


def test_no_mutation_tools_in_generated_file(tmp_path: Path) -> None:
    """FR10: generated agent file must not name write_file, edit_file, trw_deliver.

    The antigravity-cli format registry drops the bundled ``tools`` grant
    entirely for this client (its documented frontmatter has no such field —
    see ``test_yaml_frontmatter_valid_strict_parser``), so the read-only
    guarantee now rests on the body never instructing the mutation tools
    rather than on a host-enforced allowlist. This asserts that body-level
    property directly.
    """
    from trw_mcp.channels.antigravity._explorer_subagent import (
        _MUTATION_TOOLS,
        generate_distill_explorer_agent,
    )

    sidecar = _make_sidecar()
    generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_nomut",
    )

    agent_path = tmp_path / ".agents" / "agents" / "trw-distill-explorer.md"
    content = agent_path.read_text()
    parts = content.split("---")
    parsed = yaml.safe_load(parts[1])
    assert "tools" not in parsed, "antigravity-cli's registry entry drops the tools key; it must not resurface"

    for mut in _MUTATION_TOOLS:
        assert mut not in content, f"Mutation tool {mut!r} found in generated agent content"


# ---------------------------------------------------------------------------
# FR10: description contains "Read-only"
# ---------------------------------------------------------------------------


def test_description_contains_read_only(tmp_path: Path) -> None:
    """FR10: subagent description must include 'Read-only' phrase."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar = _make_sidecar()
    generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_ro",
    )

    agent_path = tmp_path / ".agents" / "agents" / "trw-distill-explorer.md"
    content = agent_path.read_text()
    parts = content.split("---")
    parsed = yaml.safe_load(parts[1])
    desc = parsed.get("description", "")
    assert "Read-only" in desc or "read-only" in desc.lower(), f"'Read-only' not found in description: {desc!r}"


# ---------------------------------------------------------------------------
# FR11 / P1-23: no unsubstituted template vars
# ---------------------------------------------------------------------------


def test_no_unsubstituted_template_vars(tmp_path: Path) -> None:
    """FR11: no {{ }} in generated output (P1-23)."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar = _make_sidecar()
    generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=sidecar,
        sidecar_sha="sha_tmpl",
    )

    agent_path = tmp_path / ".agents" / "agents" / "trw-distill-explorer.md"
    content = agent_path.read_text()
    assert "{{ " not in content, "Unsubstituted template vars found"


# ---------------------------------------------------------------------------
# FR14 / P2-19: sidecar absent writes placeholder subagent
# ---------------------------------------------------------------------------


def test_sidecar_absent_writes_placeholder_subagent(tmp_path: Path) -> None:
    """FR14: missing sidecar writes subagent with placeholder table (P2-19)."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    result = generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=None,
        sidecar_sha=None,
    )

    assert result.status == "written", f"Expected written, got {result.status}: {result.error}"
    agent_path = tmp_path / ".agents" / "agents" / "trw-distill-explorer.md"
    assert agent_path.exists()

    content = agent_path.read_text()
    # Valid YAML frontmatter.
    parts = content.split("---")
    parsed = yaml.safe_load(parts[1])
    assert "name" in parsed
    # Placeholder table present.
    assert "<path>" in content
    assert "{{ " not in content


# ---------------------------------------------------------------------------
# PRD-CORE-252 follow-up: routed through the FR01 format registry
# ---------------------------------------------------------------------------


def test_lands_at_the_registrys_antigravity_destination(tmp_path: Path) -> None:
    """The writer's destination is the registry's, not a second hardcoded path.

    Before this fix ``_AGENT_RELATIVE_PATH`` was a module-level literal
    (``.antigravitycli/agents/...``) independent of
    ``agent_format_for("antigravity-cli").destination_dir`` (``.agents/agents``,
    PRD-CORE-252-FR03) -- the two could drift, and had: the bundled specialists
    already moved, this dynamically-rendered subagent had not.
    """
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.channels.antigravity._explorer_subagent import (
        _AGENT_RELATIVE_PATH,
        generate_distill_explorer_agent,
    )

    registry_destination = agent_format_for("antigravity-cli").destination_for("trw-distill-explorer")
    assert _AGENT_RELATIVE_PATH == registry_destination

    result = generate_distill_explorer_agent(
        repo_root=tmp_path,
        sidecar_data=_make_sidecar(),
        sidecar_sha="sha_dest",
    )

    assert result.status == "written"
    assert result.path == registry_destination
    assert (tmp_path / registry_destination).is_file()
    # And the file did NOT land at the retired location.
    assert not (tmp_path / ".antigravitycli" / "agents" / "trw-distill-explorer.md").exists()


def test_retired_location_is_swept_on_update(tmp_path: Path) -> None:
    """A stale pre-move copy at ``.antigravitycli/agents`` is cleaned up.

    ``trw-distill-explorer.md`` joined ``RELOCATED_CLIENT_AGENTS`` alongside the
    other three antigravity agent names PRD-CORE-252 already relocated, so an
    existing install's stale copy is removed the same way theirs is.
    """
    from trw_mcp.bootstrap._version_migration import RELOCATED_CLIENT_AGENTS

    assert "trw-distill-explorer.md" in RELOCATED_CLIENT_AGENTS[".antigravitycli/agents"]


# ---------------------------------------------------------------------------
# FR21 — cross-reference to trw-distill-explorer
#
# ``test_trw_explorer_description_cross_reference`` is REMOVED, not relaxed.
# FR21's carrier was the ``trw-explorer.md`` entry of
# ``_ANTIGRAVITY_AGENT_TEMPLATES``: a hand-written antigravity stub whose
# description pointed at ``@trw-distill-explorer``. PRD-CORE-252-FR04 deleted
# that template set and retired the ``trw-explorer`` name with it
# (bundled-or-discard, OQ-1), so the sentence has no carrier on this client.
#
# Re-homing it onto a bundled specialist's description was considered and
# rejected here: PRD-CORE-252 §2 lists "changing what any agent does — the
# bodies, tool grants, and behavioural contracts" as an explicit non-goal, and
# the bundled corpus is client-neutral while this cross-reference is
# antigravity-specific. Restoring the pointer is the distill channel's call and
# belongs in the PRD that owns FR21 — it is NOT delivered by PRD-CORE-252, and
# asserting a weaker version of it here would misreport that.
#
# Everything else FR21 depends on — the channel's own explorer subagent (now
# ``.agents/agents/trw-distill-explorer.md``, per the registry-routing tests
# above), its content and its preservation — is asserted by the tests above
# and is untouched.
# ---------------------------------------------------------------------------
