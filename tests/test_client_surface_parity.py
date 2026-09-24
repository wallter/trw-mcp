"""Cross-client SURFACE PARITY: does the install place what the framework promises?

The question this module answers, for every supported client, is the one the
``trw-feedback`` omission got wrong: the injected instruction told cursor-ide
agents to "surface the /trw-feedback skill" while that client's curated bundle
never placed it. That defect survived a dedicated parity test because the test
enumerated a hardcoded 5-tuple of clients -- *an enumeration cannot notice a
client it was never told about*.

So nothing here is enumerated. Both sides are derived:

- the client set from the profile registry (``builtin_client_ids()``), via
  ``tests/_client_registry``;
- each client's agent destination from
  :func:`trw_mcp.agents.agent_formats.agent_format_for`;
- the bundled agent corpus from ``data/agents/*.md``;
- the bundled hook set and its registration carriers from
  ``tests/_hook_carriers``, which globs ``data/**`` rather than reading a list
  of the two template files someone remembered.

Every derived set is paired with a NON-VACUITY FLOOR. A glob that silently
stops matching would otherwise collapse the parametrization to nothing and
leave every surviving case green -- which is how this failure mode hides. When
the cursor-ide fix was made, breaking the skill-root glob took eleven cases to
six and all six still passed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests import _hook_carriers as hc
from tests._client_registry import ACTIVE_CLIENT_IDS, MINIMUM_ACTIVE_CLIENTS
from trw_mcp.agents.agent_formats import agent_format_for
from trw_mcp.agents.tier_resolver import KNOWN_CLIENTS
from trw_mcp.exceptions import AgentFormatError

_DATA_DIR = hc.DATA_DIR
_HOOK_DIR = hc.HOOK_DIR
_MIN_BUNDLED_HOOKS = hc.MINIMUM_BUNDLED_HOOKS
_MIN_HOOK_CARRIERS = hc.MINIMUM_HOOK_CARRIERS
_bundled_hooks = hc.bundled_hooks
_carrier_group = hc.carrier_group
_hook_carriers = hc.hook_carriers
_registered_in = hc.registered_in
_commands = hc._commands

#: Floors. Each is the count observed when this module was written, so a drop
#: below it means the derivation broke rather than the product shrank. Raising
#: one is a deliberate edit; a silent collapse is not. The client and hook
#: floors are shared with their derivation modules rather than restated.
_MIN_CLIENTS = MINIMUM_ACTIVE_CLIENTS
_MIN_BUNDLED_AGENTS = 8  # PRD-CORE-291-FR05: 11 -> 8

# N21 (2026-09-19 grok audit): every check in this module is pure derived-registry
# logic over static bundled data (no tmp_path, no multi-tool interaction) -- the
# same profile as the sibling test_client_registry_derivation.py, which already
# carries this marker. Left unmarked, this whole module defaulted to
# ``integration`` and 5 of 7 registry-totality reds found in that audit were
# invisible to ``make test-fast`` / ``-m unit``, so the fast dev loop reported a
# near-green tree while the client registry was actually broken.
pytestmark = pytest.mark.unit


def _clients() -> list[str]:
    """The installable client set, from the authoritative profile registry.

    ``ACTIVE_CLIENT_IDS`` carries its own import-time non-vacuity floor, so a
    broken derivation raises there rather than collapsing every parametrization
    below into a silent green.
    """
    return sorted(ACTIVE_CLIENT_IDS)


def _bundled_agents() -> list[str]:
    return sorted(p.name.removesuffix(".md") for p in _DATA_DIR.joinpath("agents").glob("*.md"))


# ---------------------------------------------------------------------------
# Non-vacuity floors -- the partner every derivation above needs
# ---------------------------------------------------------------------------


class TestDerivationsAreNotVacuous:
    """Without these, a broken glob makes every assertion below pass silently."""

    def test_client_set_is_complete(self) -> None:
        clients = _clients()
        assert len(clients) >= _MIN_CLIENTS, f"derived only {len(clients)} client(s): {clients}"
        # The two whose surfaces diverge most (a TOML agent format and an
        # absence) must be in the set, or the parity checks lose their edges.
        assert "codex" in clients and "cursor-cli" in clients, clients

    def test_the_two_client_registries_agree(self) -> None:
        """``_PROFILES`` and ``KNOWN_CLIENTS`` are two lists of the same thing.

        ``agent_formats`` is keyed on ``KNOWN_CLIENTS`` while the installer and
        instruction renderers resolve through ``_PROFILES``. A client in one and
        not the other is the precondition for the whole defect class here: a
        profile with no agent format gets no agents, and an agent format with no
        profile renders against a fallback nobody chose.
        """
        profiles, known = set(ACTIVE_CLIENT_IDS), set(KNOWN_CLIENTS)
        assert profiles == known, (
            f"registries diverge — profile-only={sorted(profiles - known)}, known-only={sorted(known - profiles)}"
        )

    def test_bundled_agent_corpus_is_present(self) -> None:
        agents = _bundled_agents()
        assert len(agents) >= _MIN_BUNDLED_AGENTS, f"derived only {len(agents)} bundled agent(s): {agents}"

    def test_bundled_hook_set_is_present(self) -> None:
        hooks = _bundled_hooks()
        assert len(hooks) >= _MIN_BUNDLED_HOOKS, f"derived only {len(hooks)} bundled hook(s): {hooks}"

    def test_hook_carriers_are_discovered(self) -> None:
        carriers = _hook_carriers()
        rel = sorted(str(c.relative_to(_DATA_DIR)) for c in carriers)
        assert len(carriers) >= _MIN_HOOK_CARRIERS, f"derived only {len(carriers)} hook carrier(s): {rel}"
        # Both known claude-code carriers must be found by the PREDICATE, not
        # named by the test. If the predicate stops finding one of them the
        # parity check below would quietly cover one carrier instead of two.
        assert "settings.json" in rel
        assert "plugin/hooks/hooks.json" in rel

    def test_every_carrier_registers_something(self) -> None:
        for carrier in _hook_carriers():
            assert _registered_in(carrier), f"{carrier} matched the carrier predicate but registers no hook"


# ---------------------------------------------------------------------------
# Hooks: bundled set vs each carrier's registration
# ---------------------------------------------------------------------------


class TestHookCarrierParity:
    """A hook is only wired for the users of the carrier that registers it.

    ``scripts/tests/test_bundled_hook_registration.py`` answers the *union*
    question -- "is every bundled hook registered SOMEWHERE?" -- by concatenating
    every carrier into one blob. That is the right ratchet for "delivered but
    dead", and it is blind to the case here: a hook in ``settings.json`` and not
    in the plugin's ``hooks.json`` passes the union test while every plugin user
    ships the script and never fires it. Four hooks were in exactly that state
    (``instructions-loaded.sh``, ``post-compact.sh``,
    ``post-tool-intent-check.sh``, ``pre-tool-intent-guard.sh``).
    """

    @pytest.mark.parametrize(
        "carrier",
        _hook_carriers(),
        ids=lambda p: str(p.relative_to(_DATA_DIR)),
    )
    def test_carrier_is_valid_json_with_a_hooks_block(self, carrier: Path) -> None:
        data = json.loads(carrier.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        hooks = data.get("hooks", data)
        assert isinstance(hooks, dict) and hooks, f"{carrier} has no hook event map"

    @pytest.mark.parametrize(
        "carrier",
        _hook_carriers(),
        ids=lambda p: str(p.relative_to(_DATA_DIR)),
    )
    def test_carrier_references_only_scripts_that_ship(self, carrier: Path) -> None:
        """No carrier may register a hook script the bundle does not contain."""
        data = json.loads(carrier.read_text(encoding="utf-8"))
        referenced = set(re.findall(r"([a-z0-9_-]+\.sh)", "\n".join(_commands(data))))
        assert referenced, f"{carrier} registers no .sh command"
        missing = sorted(name for name in referenced if not (_HOOK_DIR / name).is_file())
        assert not missing, f"{carrier} registers script(s) that are not bundled: {missing}"

    def test_the_carriers_agree_on_which_hooks_are_wired(self) -> None:
        """Per-carrier parity, with no exemptions.

        The rule is that a bundled hook ships only when it is registered, or sourced
        by a registered hook. ``validate-prd-write.sh`` used to be the one stated
        asymmetry (plugin-only, on an unscoped ``Write|Edit`` matcher that denied
        ordinary source edits); it was deleted rather than exempted, so every hook
        must now be wired identically by every carrier of its bundle.
        """
        groups: dict[str, dict[str, set[str]]] = {}
        for carrier in _hook_carriers():
            groups.setdefault(_carrier_group(carrier), {})[str(carrier.relative_to(_DATA_DIR))] = _registered_in(
                carrier
            )

        multi = {key: value for key, value in groups.items() if len(value) > 1}
        assert multi, f"no bundle has two carriers to compare — nothing is being checked: {sorted(groups)}"

        for group, by_carrier in multi.items():
            union: set[str] = set()
            for registered in by_carrier.values():
                union |= registered
            assert union, f"bundle {group!r} yielded no comparable hooks — the assertion would be vacuous"
            for name, registered in by_carrier.items():
                gap = sorted(union - registered)
                assert not gap, (
                    f"{name} does not register hook(s) another carrier of the same bundle does: {gap}. "
                    "TRW ships the script to these users and it never fires. Register it in every carrier, "
                    "or stop shipping it."
                )


# ---------------------------------------------------------------------------
# Instruction surfaces: a promise must name something the install places
# ---------------------------------------------------------------------------

#: ``trw-``-prefixed tokens that are not agents, skills or commands. Each is a
#: package, path, tag or hook-script name, so a mention of one promises no
#: capability. Kept narrow on purpose: anything NOT here must resolve.
_NON_CAPABILITY_TOKENS = frozenset(
    {
        "trw-mcp",
        "trw-memory",
        "trw-eval",
        "trw-distill",
        "trw-loop",
        "trw-swarm",
        "trw-framework",
        "trw-ceremony",
        "trw-reconcile-pending",
        "trw-validation-rules",
    }
)


def _agent_mentions(text: str) -> set[str]:
    """``@trw-x`` references — the syntax an instruction uses to name an agent."""
    return set(re.findall(r"@(trw-[a-z0-9-]+)", text))


class TestInstructionPromiseParity:
    """Every agent an instruction names must be one the install materializes."""

    @pytest.mark.parametrize("client", _clients())
    def test_client_has_a_decided_agent_surface(self, client: str) -> None:
        """Either a destination, or a recorded reason there is none."""
        fmt = agent_format_for(client)
        if fmt.supports_agents:
            assert fmt.destination_dir, client
        else:
            assert fmt.unsupported_reason, client

    def test_every_bundled_agent_has_a_destination_per_supporting_client(self) -> None:
        agents = _bundled_agents()
        assert len(agents) >= _MIN_BUNDLED_AGENTS, agents
        supporting = [c for c in _clients() if agent_format_for(c).supports_agents]
        assert len(supporting) >= _MIN_CLIENTS - 1, f"only {len(supporting)} client(s) support agents: {supporting}"
        for client in supporting:
            fmt = agent_format_for(client)
            for stem in agents:
                dest = fmt.destination_for(stem)
                assert dest.startswith(f"{fmt.destination_dir}/"), (client, stem, dest)

    def test_a_client_with_no_agent_surface_cannot_yield_a_destination(self) -> None:
        """Non-vacuity control for the loop above: the absence branch is live."""
        absent = [c for c in _clients() if not agent_format_for(c).supports_agents]
        assert absent, "no client records an agent-surface absence — the branch above is untested"
        for client in absent:
            with pytest.raises(AgentFormatError):
                agent_format_for(client).destination_for("trw-implementer")

    def test_antigravity_instruction_names_only_installed_agents(self) -> None:
        """The concrete defect this module was written for.

        ``render_antigravity_instructions`` hardcoded a four-name subagent list
        in a directory (``.antigravitycli/agents/``) that PRD-CORE-252 moved
        away from, and one of the four (``trw-explorer``) was retired with the
        old templates. One renderer feeds BOTH Antigravity carriers
        (``ANTIGRAVITY.md`` and ``.agents/rules/trw-ceremony.md``), so the
        dangling promise reached every Antigravity install twice.
        """
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_antigravity_instructions

        text = render_antigravity_instructions()
        fmt = agent_format_for("antigravity-cli")
        assert fmt.destination_dir is not None
        assert f"{fmt.destination_dir}/" in text, "the instruction never states where the agents land"
        assert ".antigravitycli/agents" not in text, "the pre-PRD-CORE-252 destination is back"

        mentioned = _agent_mentions(text)
        assert len(mentioned) >= _MIN_BUNDLED_AGENTS, f"only {len(mentioned)} agent(s) named: {sorted(mentioned)}"
        unplaceable = sorted(mentioned - set(_bundled_agents()))
        assert not unplaceable, f"instruction names agent(s) the install never places: {unplaceable}"

    def test_antigravity_instruction_uses_its_own_tool_namespace(self) -> None:
        """The same drift ``agent_formats`` removed from the agent templates.

        That block asserted an ``mcp_trw_`` prefix while
        ``antigravity-cli``'s profile declares an empty
        ``tool_namespace_prefix``, so it told those agents to call tools by a
        name their harness does not expose.
        """
        from trw_mcp.models.config._profiles import resolve_client_profile
        from trw_mcp.prompts.messaging import render_tool_name
        from trw_mcp.state.claude_md.renderers._review_and_opencode import render_antigravity_instructions

        profile = resolve_client_profile("antigravity-cli")
        text = render_antigravity_instructions()
        expected = render_tool_name("trw_session_start", profile)
        assert f"`{expected}`" in text
        if not profile.tool_namespace_prefix:
            assert "mcp_trw_" not in text, "a namespace this profile does not declare is asserted to its agents"


class TestFastLoopSeesRegistryTotality:
    """N21: ``-m unit`` (``make test-fast``) must actually select this module.

    Marking the module ``unit`` is not itself proof the fast loop runs it --
    ``conftest.py``'s ``pytest_collection_modifyitems`` only *skips* auto-tiering
    when a test already carries an ``unit``/``integration``/``e2e`` marker, so a
    misspelled or module-scoped-only marker could still leave items deselected.
    This spawns the real collection pytest performs and asserts registry-totality
    tests are among the selected items -- the same command ``make test-fast`` runs.
    """

    def test_registry_totality_checks_are_selected_by_the_fast_loop(self) -> None:
        import subprocess
        import sys

        tests_dir = Path(__file__).resolve().parent
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(Path(__file__).name),
                "-m",
                "unit",
                "--collect-only",
                "-q",
            ],
            capture_output=True,
            text=True,
            cwd=tests_dir,
            timeout=60,
        )
        assert "test_client_set_is_complete" in result.stdout, result.stdout
        assert "test_the_two_client_registries_agree" in result.stdout, result.stdout
        assert "no tests ran" not in result.stdout, result.stdout
