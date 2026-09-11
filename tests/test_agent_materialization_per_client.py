"""PRD-CORE-252-FR02 / NFR01: the full agents x clients materialization contract.

Real bytes read from the shipped bundle, real registry, no mocks and no stubbed
formats — the point of the FR is that the emitted document is what a harness
would actually parse, and a mocked render proves nothing about that.
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests._formation_test_support import FormationFixture, formation_env, make_run_dir  # noqa: F401
from tests._layout import MONOREPO_ROOT, requires_monorepo
from trw_mcp.agents.agent_formats import agent_format_for
from trw_mcp.agents.tier_resolver import (
    KNOWN_CLIENTS,
    materialize_agent,
    render_agent_tool_names,
    rewrite_model_line,
)
from trw_mcp.exceptions import AgentFormatError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the package floor is 3.10
    import tomli as tomllib

BUNDLED_AGENTS_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "agents"

#: Both spellings a TRW MCP tool grant has ever carried. claude-code's
#: ``mcp__trw__`` and antigravity's ``mcp_trw_`` — the second is why the agent
#: contract linter missed a dead grant for weeks, so both are asserted here.
_NAMESPACED_TOOL_PREFIXES = ("mcp__trw__", "mcp_trw_")


def _bundled_agents() -> list[Path]:
    paths = sorted(BUNDLED_AGENTS_DIR.glob("*.md"))
    assert paths, "no bundled agents found; this test has stopped testing anything"
    return paths


def _parse_rendered(text: str, serialization: str) -> dict[str, Any]:
    """Parse an emitted agent document under its declared serialization."""
    if serialization == "toml":
        return dict(tomllib.loads(text))
    assert text.startswith("---\n"), "yaml_frontmatter_markdown output must open with a fence"
    block, _ = text[4:].split("\n---\n", 1)
    parsed = yaml.safe_load(block)
    assert isinstance(parsed, dict)
    return dict(parsed)


@pytest.mark.unit
def test_every_agent_renders_for_every_client() -> None:
    """FR02: the whole cross-product, asserted on the emitted bytes.

    Four properties per render: it parses under its declared serialization, it
    carries no key outside the client's retained set, it carries no
    claude-code-namespaced tool name when the client's namespace is bare, and —
    for claude-code — it is byte-identical to the transform that shipped before
    the frontmatter step existed.
    """
    agents = _bundled_agents()
    rendered_count = 0
    unsupported: list[str] = []

    for client in sorted(KNOWN_CLIENTS):
        fmt = agent_format_for(client)
        if not fmt.supports_agents:
            for path in agents:
                with pytest.raises(AgentFormatError, match="no agent surface"):
                    materialize_agent(path.read_text(encoding="utf-8"), client=client)
            unsupported.append(client)
            continue

        for path in agents:
            source = path.read_text(encoding="utf-8")
            out = materialize_agent(source, client=client)
            rendered_count += 1

            parsed = _parse_rendered(out, fmt.serialization)
            surplus = set(parsed) - set(fmt.retained_keys)
            assert not surplus, f"{client}/{path.name}: emitted keys outside the client's format: {sorted(surplus)}"

            if not fmt.tool_namespace:
                for prefix in _NAMESPACED_TOOL_PREFIXES:
                    assert prefix not in out, f"{client}/{path.name}: leaked the {prefix!r} namespace"

    assert unsupported == ["cursor-cli"], "cursor-cli is the only client with no agent surface"
    assert rendered_count == len(agents) * (len(KNOWN_CLIENTS) - len(unsupported))


@pytest.mark.unit
@pytest.mark.parametrize("agent_path", _bundled_agents(), ids=lambda p: p.name)
def test_claude_code_output_is_byte_identical_to_the_pre_change_transform(agent_path: Path) -> None:
    """FR02 regression anchor: the change is additive for the client that worked.

    The baseline is computed live from the two public transforms that composed
    ``materialize_agent`` before the frontmatter step landed, so it cannot rot
    into a stale golden file — and it fails the moment the frontmatter step
    stops being the identity for the dialect the bundle is authored in.
    """
    source = agent_path.read_text(encoding="utf-8")
    pre_change = rewrite_model_line(render_agent_tool_names(source, client="claude-code"), client="claude-code")

    assert materialize_agent(source, client="claude-code") == pre_change


@pytest.mark.unit
def test_unsupported_client_raises_a_typed_error() -> None:
    """A harness with no agent surface must not receive a plausible wrong answer."""
    source = _bundled_agents()[0].read_text(encoding="utf-8")
    with pytest.raises(AgentFormatError):
        materialize_agent(source, client="cursor-cli")
    with pytest.raises(AgentFormatError):
        materialize_agent(source, client="not-a-registered-client")


@pytest.mark.unit
def test_an_undecided_frontmatter_key_fails_rather_than_passing_through() -> None:
    """FR02 boundary: silent passthrough is the defect, so it must be impossible."""
    source = _bundled_agents()[0].read_text(encoding="utf-8")
    grown = source.replace("---\n", "---\nsomeNewKey: 1\n", 1)
    with pytest.raises(AgentFormatError, match="unregistered frontmatter keys"):
        materialize_agent(grown, client="codex")


@pytest.mark.unit
def test_codex_receives_the_body_through_its_instructions_field() -> None:
    """US-2: the body must reach Codex through the field its format uses."""
    path = next(p for p in _bundled_agents() if p.name == "trw-auditor.md")
    parsed = tomllib.loads(materialize_agent(path.read_text(encoding="utf-8"), client="codex"))

    assert parsed["name"] == "trw-auditor"
    assert "TRW Auditor Agent" in parsed["developer_instructions"]
    assert parsed["sandbox_mode"] in {"read-only", "workspace-write"}


@pytest.mark.unit
def test_read_only_agents_are_derived_from_grants_not_from_names() -> None:
    """The retired stubs keyed cursor's ``readonly`` on one hardcoded agent name.

    ``trw-researcher`` grants no write-capable tool and ``trw-implementer``
    grants Edit and Write, so the derivation must split them without either
    name appearing in the transform.
    """
    researcher = (BUNDLED_AGENTS_DIR / "trw-researcher.md").read_text(encoding="utf-8")
    implementer = (BUNDLED_AGENTS_DIR / "trw-implementer.md").read_text(encoding="utf-8")

    assert _parse_rendered(materialize_agent(researcher, client="cursor-ide"), "yaml_frontmatter_markdown")["readonly"]
    assert not _parse_rendered(materialize_agent(implementer, client="cursor-ide"), "yaml_frontmatter_markdown")[
        "readonly"
    ]
    assert tomllib.loads(materialize_agent(researcher, client="codex"))["sandbox_mode"] == "read-only"
    assert tomllib.loads(materialize_agent(implementer, client="codex"))["sandbox_mode"] == "workspace-write"


@pytest.mark.unit
def test_opencode_permissions_are_an_ordered_array_of_rules() -> None:
    """The vendor shape, asserted structurally — a map would pass a truthiness check.

    opencode.ai/v2/docs/agents (fetched 2026-09-03): "Permissions are an ordered
    array of rules", each ``{action, resource, effect}``, and its own read-only
    reviewer example denies ``edit`` and ``shell``. TRW's retired opencode
    bundle shipped the map ``bash: deny / edit: deny / write: deny`` — wrong
    shape, and wrong vocabulary twice over, since the documented v2 action names
    are ``shell`` for shell commands and ``edit`` for all edit/write/patch
    tools, so ``bash`` and ``write`` name nothing.
    """
    documented_actions = {"shell", "edit", "subagent", "read", "glob", "grep", "webfetch", "websearch"}
    documented_effects = {"allow", "ask", "deny"}
    seen_read_only = False

    for path in _bundled_agents():
        parsed = _parse_rendered(
            materialize_agent(path.read_text(encoding="utf-8"), client="opencode"),
            "yaml_frontmatter_markdown",
        )
        rules = parsed.get("permissions")
        if rules is None:
            continue
        seen_read_only = True
        assert isinstance(rules, list), f"{path.name}: permissions must be an ARRAY, got {type(rules).__name__}"
        for rule in rules:
            assert isinstance(rule, dict), f"{path.name}: each permission must be a mapping, got {rule!r}"
            assert set(rule) == {"action", "resource", "effect"}, f"{path.name}: {sorted(rule)}"
            assert rule["action"] in documented_actions, f"{path.name}: undocumented action {rule['action']!r}"
            assert rule["effect"] in documented_effects, f"{path.name}: undocumented effect {rule['effect']!r}"
        assert [r["action"] for r in rules] == ["edit", "shell"], (
            f"{path.name}: a read-only agent must deny exactly the two actions the vendor example denies"
        )

    assert seen_read_only, "no read-only agent emitted permissions; this test has stopped testing anything"


@pytest.mark.unit
def test_copilot_tool_grants_are_scoped_per_agent_not_whole_server() -> None:
    """Copilot's ``tools`` is RESTRICTIVE, so the grant must carry both halves.

    docs.github.com/en/copilot/reference/custom-agents-configuration (fetched
    2026-09-03): "If unset, defaults to all tools"; a list enables "only those
    tools"; MCP tools are referenced ``server/tool``. That page publishes a
    case-insensitive alias table listing the bundle's own tool names, so the
    host grants translate exactly — which is what lets a read-only auditor be
    denied ``edit`` instead of being handed the whole server.
    """
    documented_aliases = {"execute", "read", "edit", "search", "agent", "web", "todo"}

    auditor = _parse_rendered(
        materialize_agent((BUNDLED_AGENTS_DIR / "trw-auditor.md").read_text(encoding="utf-8"), client="copilot"),
        "yaml_frontmatter_markdown",
    )
    implementer = _parse_rendered(
        materialize_agent((BUNDLED_AGENTS_DIR / "trw-implementer.md").read_text(encoding="utf-8"), client="copilot"),
        "yaml_frontmatter_markdown",
    )

    # The discrimination that whole-server grants could not express.
    assert "edit" not in auditor["tools"], "a read-only auditor must not be granted edit"
    assert "edit" in implementer["tools"], "the implementer must keep its write grant"

    for parsed in (auditor, implementer):
        grants = parsed["tools"]
        assert isinstance(grants, list) and grants
        assert grants == sorted(set(grants)), "grants must be sorted and de-duplicated for a stable re-render"
        mcp = [g for g in grants if "/" in g]
        assert mcp, "the trw MCP tools must survive as server/tool grants"
        assert all(g.startswith("trw/") for g in mcp), mcp
        assert all(g in documented_aliases for g in grants if "/" not in g), grants
        # The invalid list form of a key typed `object` by the vendor is gone.
        assert "mcp-servers" not in parsed


@pytest.mark.unit
def test_antigravity_model_is_one_of_the_three_tokens_its_schema_admits() -> None:
    """The drift evidence, closed: no literal Gemini id, no raw capability tier."""
    for path in _bundled_agents():
        parsed = _parse_rendered(
            materialize_agent(path.read_text(encoding="utf-8"), client="antigravity-cli"),
            "yaml_frontmatter_markdown",
        )
        assert parsed["model"] in {"inherit", "flash", "pro"}, f"{path.name}: {parsed['model']!r}"
        assert "temperature" not in parsed
        assert "max_turns" not in parsed
        assert "timeout_mins" not in parsed


@pytest.mark.unit
def test_materialization_latency_budget() -> None:
    """NFR01: median wall time for one client's whole bundle, over 20 runs.

    Measured rather than argued: the frontmatter parse and re-serialize FR02
    adds is per-agent work that now runs once per client instead of once.
    """
    budget_seconds = 0.250
    runs = 20
    sources = [path.read_text(encoding="utf-8") for path in _bundled_agents()]

    durations: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        for source in sources:
            materialize_agent(source, client="codex")
        durations.append(time.perf_counter() - start)

    median = statistics.median(durations)
    assert median < budget_seconds, f"median {median * 1000:.1f} ms exceeds the {budget_seconds * 1000:.0f} ms budget"


# --- PRD-CORE-265-NFR05: the formation surface is client-neutral -------------


@pytest.mark.parametrize(
    "include_commit_gate",
    [False, pytest.param(True, marks=requires_monorepo)],
    ids=["shipped-adapters", "monorepo-commit-adapter"],
)
def test_formation_surface_is_client_neutral(
    formation_env: FormationFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, include_commit_gate: bool
) -> None:
    """NFR05. All four capabilities are reachable from files plus the CLI alone.

    ATTRIBUTION. The profile list is DERIVED from ``_PROFILES``, not typed here,
    so a new supported client is covered the day it is registered rather than
    the day someone remembers to extend a literal. For each profile the test
    drives join, brief, status, and the commit-boundary refusal through the same
    two mechanisms every client has — a YAML file and a subprocess — and asserts
    no client-specific transport was needed. Introduce one (a hook requirement,
    an MCP-only path, a peer-messaging call) and the profile that lacks it fails
    here instead of in a live session.
    """
    import argparse
    import sys as _sys

    from trw_mcp.formation import brief, create, join, owner_of, status
    from trw_mcp.models.config._profiles import _PROFILES
    from trw_mcp.tools._formation_cli import run_formation

    if include_commit_gate:
        assert MONOREPO_ROOT is not None
        assert (MONOREPO_ROOT / "scripts/check_formation_ownership.py").is_file()
        _sys.path.insert(0, str(MONOREPO_ROOT / "scripts"))
        import check_formation_ownership as commit_gate

    profiles = sorted(_PROFILES)
    assert len(profiles) >= 7, f"expected the seven supported profiles, got {profiles}"

    members = [{"member_id": f"m-{client}", "client": client, "owned_paths": [f"src/{client}"]} for client in profiles]
    create(formation_env.orchestrator_run, formation_env.payload(members=members), prds_dir=None)

    runs_root = formation_env.trw_dir / "runs"
    unreachable: dict[str, str] = {}
    for client in profiles:
        member_id = f"m-{client}"
        run = make_run_dir(runs_root, member_id)
        try:
            # (1) JOIN — a file write behind one facade call, no transport.
            join("release-train", member_id, run, pin_key=f"pin-{client}")
            # (2) BRIEF — rendered text on stdout, readable by any client.
            rendered = brief(member_id, run_path=run)
            assert f"src/{client}" in rendered
            # (3) STATUS — the same rows every client reads.
            args = argparse.Namespace(formation_command="status", run_path=str(run), as_json=True)
            with pytest.raises(SystemExit) as exited:
                run_formation(args)
            assert exited.value.code == 0
            board = status(run_path=run)
            assert board is not None and any(row.member_id == member_id for row in board.rows)
            # (4) COMMIT-BOUNDARY REFUSAL — a subprocess-level exit code.
            if include_commit_gate:
                monkeypatch.setattr(commit_gate, "_resolve_caller_run", lambda run=run: run)
                foreign = next(other for other in profiles if other != client)
                assert commit_gate.main([f"src/{foreign}/x.py"]) == 1
                assert commit_gate.main([f"src/{client}/x.py"]) == 0
            assert owner_of(f"src/{client}/x.py", run_path=run) is not None
        except AssertionError as exc:
            unreachable[client] = str(exc)
    assert not unreachable, f"these profiles cannot reach the formation surface through files + CLI: {unreachable}"
