"""PRD-CORE-252-FR01 / NFR03: the per-client agent-format registry.

The registry replaces five hand-maintained template dictionaries. These tests
assert the properties that make it a registry rather than a sixth copy: it is
keyed on ``KNOWN_CLIENTS`` exactly, it reads the tool namespace from the profile
rather than restating it, and it accounts for every frontmatter key the shipped
bundle actually declares.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trw_mcp.agents.agent_formats import (
    BUNDLED_AGENT_KEYS,
    AgentFormat,
    agent_format_for,
)
from trw_mcp.agents.agent_frontmatter import translate_agent_document
from trw_mcp.agents.tier_resolver import KNOWN_CLIENTS
from trw_mcp.exceptions import AgentFormatError
from trw_mcp.models.config._profiles import resolve_client_profile

BUNDLED_AGENTS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "agents"


def _bundled_agent_paths() -> list[Path]:
    return sorted(BUNDLED_AGENTS_DIR.glob("*.md"))


@pytest.mark.unit
def test_registry_covers_known_clients_exactly() -> None:
    """FR01: one entry per KNOWN_CLIENTS id, no more, no fewer, all well-formed.

    The four acceptance criteria in one test, because they are one property:
    the table is complete, each supported row is usable, the one unsupported
    row says why, and no row restates a namespace the profile already owns.
    """
    resolved = {client: agent_format_for(client) for client in KNOWN_CLIENTS}

    # Key set equality. An id in KNOWN_CLIENTS with no entry raises above; an
    # entry outside KNOWN_CLIENTS is caught here.
    from trw_mcp.agents import agent_formats as _formats

    assert set(_formats._REGISTRY) == set(KNOWN_CLIENTS)
    assert "aider" not in _formats._REGISTRY, "the retired aider id must be in neither set"

    supported = {cid: fmt for cid, fmt in resolved.items() if fmt.supports_agents}
    unsupported = {cid: fmt for cid, fmt in resolved.items() if not fmt.supports_agents}

    for client_id, fmt in supported.items():
        assert fmt.destination_dir, f"{client_id}: supported entry needs a destination"
        assert fmt.filename_suffix, f"{client_id}: supported entry needs a filename suffix"
        assert fmt.serialization in {"yaml_frontmatter_markdown", "toml"}
        assert fmt.retained_keys, f"{client_id}: supported entry retains no keys"

    assert unsupported, "at least one client (cursor-cli) has no agent surface"
    for client_id, fmt in unsupported.items():
        assert fmt.destination_dir is None, f"{client_id}: unsupported entry must have no destination"
        assert fmt.unsupported_reason, f"{client_id}: unsupported entry must carry a reason"

    # The Antigravity drift class: a namespace stated twice can disagree.
    for client_id, fmt in resolved.items():
        assert fmt.tool_namespace == resolve_client_profile(client_id).tool_namespace_prefix


@pytest.mark.unit
def test_bundled_key_universe_matches_the_shipped_bundle() -> None:
    """BUNDLED_AGENT_KEYS is a claim about the bundle; check it against the bundle.

    Without this, the registry's "every bundled key is mapped or dropped"
    validator is self-referential: it would pass against a stale constant while
    a newly-added frontmatter key leaked through untranslated.
    """
    observed: set[str] = set()
    for path in _bundled_agent_paths():
        block, _ = path.read_text(encoding="utf-8").split("\n---\n", 1)
        parsed = yaml.safe_load(block.removeprefix("---\n"))
        assert isinstance(parsed, dict), f"{path.name}: frontmatter is not a mapping"
        observed |= set(parsed)

    assert observed == set(BUNDLED_AGENT_KEYS), (
        "the bundle's frontmatter key set moved; every client entry in agent_formats.py "
        "must decide the new key's fate (map it or drop it) before it can ship"
    )


@pytest.mark.unit
def test_an_undecided_bundled_key_is_a_construction_error() -> None:
    """FR01 boundary: a key with neither a mapping nor a drop rule fails loudly."""
    with pytest.raises(ValueError, match="undecided bundled keys"):
        AgentFormat(
            client_id="synthetic",
            destination_dir=".synthetic/agents",
            key_map={"name": "name"},
            dropped_keys=frozenset({"description"}),
        )


@pytest.mark.unit
def test_an_unsupported_entry_cannot_also_declare_a_destination() -> None:
    with pytest.raises(ValueError, match="unsupported entries need"):
        AgentFormat(
            client_id="synthetic",
            supports_agents=False,
            destination_dir=".synthetic/agents",
            unsupported_reason="no surface",
        )


@pytest.mark.unit
def test_unregistered_client_raises_rather_than_falling_back() -> None:
    """A harness TRW does not know must not receive a claude-code-shaped file."""
    with pytest.raises(AgentFormatError, match="no agent format registered"):
        agent_format_for("some-unknown-harness")


@pytest.mark.unit
@pytest.mark.parametrize(
    "stem",
    ["../escape", "sub/dir", "/absolute", "..", ".hidden", "", "with space", "trw-x\x00y"],
)
def test_destination_paths_are_confined_and_values_are_quoted(stem: str, tmp_path: Path) -> None:
    """NFR03: a name is validated before it becomes a path component.

    An agent ``name`` ships in the wheel, but a user may edit the bundle in a
    fork, so the boundary treats it as untrusted. Every traversal attempt must
    be rejected rather than resolving outside the destination.
    """
    fmt = agent_format_for("cursor-ide")
    with pytest.raises(AgentFormatError, match="unsafe agent name"):
        fmt.destination_for(stem)

    # And a legitimate name resolves strictly inside the destination.
    root = tmp_path.resolve()
    resolved = (root / fmt.destination_for("trw-auditor")).resolve()
    assert resolved.is_relative_to((root / str(fmt.destination_dir)).resolve())


@pytest.mark.unit
def test_frontmatter_values_are_quoted_not_interpolated() -> None:
    """NFR03: shell metacharacters and escapes survive as data, not as syntax."""
    hostile = (
        "---\n"
        "name: trw-hostile\n"
        'description: "$(rm -rf /) `id` \\u001b[31mred\\u001b[0m: value # comment"\n'
        "model: balanced\n"
        "effort: high\n"
        "maxTurns: 10\n"
        "memory: project\n"
        "tools:\n  - Read\n"
        "disallowedTools:\n  - Bash\n"
        "---\n\nbody\n"
    )
    rendered = translate_agent_document(hostile, agent_format_for("cursor-ide"))
    block, _ = rendered[4:].split("\n---\n", 1)
    parsed = yaml.safe_load(block)
    assert parsed["description"].startswith("$(rm -rf /)"), "the value round-trips as data"
    assert parsed["name"] == "trw-hostile"


@pytest.mark.unit
def test_byte_cap_is_a_typed_field_not_a_literal() -> None:
    """NFR03: the read bound is a documented Pydantic field with a positive default."""
    fmt = agent_format_for("claude-code")
    assert fmt.max_agent_bytes > 0
    assert "max_agent_bytes" in AgentFormat.model_fields
    with pytest.raises(ValueError):
        AgentFormat(
            client_id="synthetic",
            destination_dir=".synthetic/agents",
            key_map={key: key for key in BUNDLED_AGENT_KEYS},
            max_agent_bytes=0,
        )


@pytest.mark.unit
def test_registry_module_exports_exactly_the_lookup_and_the_model() -> None:
    """NFR05: a narrow interface with the per-client knowledge behind it."""
    from trw_mcp.agents import agent_formats

    assert agent_formats.__all__ == ["AgentFormat", "agent_format_for"]
