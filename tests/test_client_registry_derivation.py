"""Every client-set enumeration derives from the profile registry.

A hand-maintained list of client names cannot notice a client it was never
told about. This file pins the authoritative accessor
(``models.config._profiles.builtin_client_ids``), its fail-closed floor, and
each consumer that was converted from a restated list to a derivation, so a
future edit cannot quietly reintroduce the parallel list.

The floor tests are the ones that matter. A derivation that silently yields an
empty or truncated set turns every completeness check reading it into a VACUOUS
PASS, which is worse than the hardcoded lists this replaced — those at least
failed loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._client_registry import ACTIVE_CLIENT_IDS, MINIMUM_ACTIVE_CLIENTS, RETIRED_CLIENT_IDS
from trw_mcp.models.config import _profiles as profiles_mod
from trw_mcp.models.config import builtin_client_ids, retired_client_ids

pytestmark = pytest.mark.unit


class TestAuthoritativeAccessor:
    def test_active_ids_are_the_registry_keys_in_registry_order(self) -> None:
        assert builtin_client_ids() == tuple(profiles_mod._PROFILES)

    def test_every_active_id_resolves_to_its_own_profile(self) -> None:
        # Not the claude-code fallback: an id that only resolved by fallback
        # would still "work" while silently inheriting another client's config.
        for client_id in builtin_client_ids():
            assert profiles_mod.resolve_client_profile(client_id).client_id == client_id

    def test_retired_ids_are_excluded_from_the_active_set(self) -> None:
        assert "aider" in retired_client_ids()
        assert not (set(builtin_client_ids()) & retired_client_ids())

    def test_removed_client_is_in_neither_set(self) -> None:
        # gemini was REMOVED outright 2026-07-24, not retired.
        assert "gemini" not in builtin_client_ids()
        assert "gemini" not in retired_client_ids()

    def test_non_vacuity_floor(self) -> None:
        assert len(builtin_client_ids()) >= MINIMUM_ACTIVE_CLIENTS

    def test_truncated_registry_still_yields_the_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A registry that resolves short must NOT produce a short answer.

        This is the whole reason the accessor exists rather than
        ``tuple(_PROFILES)`` at every call site: an import-order accident or a
        bad merge that empties the registry would otherwise make every derived
        completeness check pass over nothing.
        """
        monkeypatch.setattr(
            profiles_mod,
            "_PROFILES",
            {"claude-code": profiles_mod._PROFILES["claude-code"]},
        )
        derived = builtin_client_ids()

        assert set(derived) >= set(profiles_mod._BUILTIN_CLIENT_FLOOR)
        assert len(derived) >= MINIMUM_ACTIVE_CLIENTS

    def test_empty_registry_still_yields_the_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(profiles_mod, "_PROFILES", {})

        assert set(builtin_client_ids()) == set(profiles_mod._BUILTIN_CLIENT_FLOOR)


class TestConvertedConsumers:
    def test_supported_ides_is_derived(self) -> None:
        from trw_mcp.bootstrap._utils import SUPPORTED_IDES

        assert SUPPORTED_IDES == list(ACTIVE_CLIENT_IDS)
        assert "aider" not in SUPPORTED_IDES

    def test_catalog_client_order_is_active_plus_retired(self) -> None:
        from trw_mcp.client_profiles.catalog import _ACTIVE_CLIENT_ORDER, _CLIENT_ORDER

        assert _ACTIVE_CLIENT_ORDER == ACTIVE_CLIENT_IDS
        assert set(_CLIENT_ORDER) == set(ACTIVE_CLIENT_IDS) | RETIRED_CLIENT_IDS

    def test_uninstall_surfaces_cover_every_active_client(self) -> None:
        """The failure this guards: a profile absent from ``_CLIENT_ORDER`` gets
        no uninstall surfaces, so ``trw-mcp uninstall`` reports success and
        leaves that client's config tree on disk forever."""
        from trw_mcp.client_profiles.catalog import client_scaffold_relpaths

        for client_id in ACTIVE_CLIENT_IDS:
            # claude-code's surfaces are all core surfaces, so its per-client
            # projection is legitimately empty; every other client installs a
            # config directory of its own.
            if client_id == "claude-code":
                continue
            assert client_scaffold_relpaths(client_id), f"{client_id} has no uninstall surfaces"

    def test_every_retired_id_is_recognised_by_the_cli(self) -> None:
        """A retired id must stay RECOGNISED, not become "invalid choice".

        ``_RETIRED_IDES`` is legitimately a superset (it also carries ``gemini``,
        which was removed outright rather than retired), so this is containment,
        not equality. Without it, retiring a profile in ``_profiles.py`` alone
        would make ``--ide <id>`` degrade to argparse's bare rejection and
        ``resolve_ide_targets`` treat the id as unknown.
        """
        from trw_mcp.bootstrap._utils import _RETIRED_IDES

        assert RETIRED_CLIENT_IDS <= set(_RETIRED_IDES)

    def test_every_retired_id_keeps_uninstall_surfaces(self) -> None:
        """Retiring a profile must not silently strand its installed files.

        A retired id no longer resolves to its own profile
        (``resolve_client_profile`` returns the claude-code fallback), so its
        surfaces can only come from ``_PROFILE_DIR_SURFACES`` or
        ``_RETIRED_INSTRUCTION_SURFACES``. An empty projection means existing
        installs of that client are no longer removable — forever.
        """
        from trw_mcp.client_profiles.catalog import client_surfaces

        for client_id in RETIRED_CLIENT_IDS:
            assert client_surfaces(client_id), f"retired {client_id} has no uninstall surfaces"

    def test_known_clients_is_derived(self) -> None:
        from trw_mcp.agents.tier_resolver import KNOWN_CLIENTS

        assert KNOWN_CLIENTS == frozenset(ACTIVE_CLIENT_IDS)
        assert "aider" not in KNOWN_CLIENTS

    def test_every_active_client_has_an_agent_format(self) -> None:
        from trw_mcp.agents.agent_formats import agent_format_for

        for client_id in ACTIVE_CLIENT_IDS:
            fmt = agent_format_for(client_id)
            assert fmt.client_id == client_id

    def test_agent_recorder_clients_are_derived(self) -> None:
        from trw_mcp.agents.agent_formats import agent_format_for
        from trw_mcp.bootstrap._managed_client_artifacts import (
            _AGENT_CLIENTS,
            _AGENT_RECORDER_EXCLUSIONS,
        )

        expected = {
            client_id
            for client_id in ACTIVE_CLIENT_IDS
            if client_id not in _AGENT_RECORDER_EXCLUSIONS and agent_format_for(client_id).supports_agents
        }
        assert set(_AGENT_CLIENTS) == expected
        assert _AGENT_CLIENTS, "an empty recorder set disables the user-edit guard for every client"

    def test_agent_recorder_exclusions_carry_reasons(self) -> None:
        from trw_mcp.bootstrap._managed_client_artifacts import _AGENT_RECORDER_EXCLUSIONS

        assert _AGENT_RECORDER_EXCLUSIONS
        for client_id, reason in _AGENT_RECORDER_EXCLUSIONS.items():
            assert client_id in ACTIVE_CLIENT_IDS
            assert reason.strip(), f"{client_id} is excluded with no stated reason"

    def test_installer_template_lists_exactly_the_active_clients(self) -> None:
        """The standalone installer cannot IMPORT the registry, so parity is a test.

        ``install-trw.template.py`` ships to users as a self-contained script, so
        its ``_SUPPORTED_IDES`` and ``_IDE_META`` are a genuine second copy of
        the client set — the one enumeration here that cannot be derived away.
        The existing guard asserts a single client name (``antigravity-cli``)
        appears in the text, which is the same shape as the defects this audit
        closes: it can only notice the client it was told about. This is set
        parity, read from the template's own AST.

        A user selecting a client the template does not list gets a ValueError
        at install time; a client missing from ``_IDE_META`` gets a blank menu
        entry or a KeyError.
        """
        import ast

        template = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"
        if not template.is_file():
            pytest.skip("standalone installer template absent (packaged mirror)")

        tree = ast.parse(template.read_text(encoding="utf-8"))
        found: dict[str, set[str]] = {}
        for node in tree.body:
            # Both plain and annotated assignments: the template declares
            # ``_SUPPORTED_IDES = [...]`` but ``_IDE_META: dict[...] = {...}``.
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                name, value = node.target.id, node.value
            else:
                continue
            if name not in {"_SUPPORTED_IDES", "_IDE_META"}:
                continue
            if isinstance(value, ast.List):
                found[name] = {e.value for e in value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            elif isinstance(value, ast.Dict):
                found[name] = {k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}

        assert set(found) == {"_SUPPORTED_IDES", "_IDE_META"}, (
            f"could not read both installer-template client collections (found {sorted(found)}); "
            "the parse broke, so this check would otherwise pass vacuously"
        )
        for name, ids in found.items():
            assert ids == set(ACTIVE_CLIENT_IDS), (
                f"install-trw.template.py {name} does not match the client-profile registry: "
                f"missing {sorted(set(ACTIVE_CLIENT_IDS) - ids)}, extra {sorted(ids - set(ACTIVE_CLIENT_IDS))}"
            )

    def test_meta_tune_tables_cover_every_active_client(self) -> None:
        from trw_mcp.channels._manifest_models import (
            CLIENT_CORRECTION_FACTORS,
        )

        for client_id in ACTIVE_CLIENT_IDS:
            assert client_id in CLIENT_CORRECTION_FACTORS
