"""PRD-CORE-185 FR05: portability classifier + automatic write routing.

Portable learnings (operator directives, cross-cutting patterns, raw-context
drops) route to the ``user:local`` namespace; project-specific learnings (file paths,
repo-local symbols) stay in the project namespace; ambiguous content defaults
to project.

PRD-CORE-280 FR06 removed the presence gate: ``user:local`` always exists in
the one store, so ``route_tier`` runs the classifier with no probe. The
classifier/``route_tier`` sections above are pure functions (no I/O). The "End-to-end store routing" section IS tier/scope
routing behaviour -- the ``daemon_checkout`` route's stated purpose -- so it
is ported there: ``memory_adapter.store_learning`` already runs through
``_store_selection.selected_store`` (PRD-CORE-280 FR01), and read-back that
used to go through ``get_backend``/``get_user_backend`` now reads through
``daemon_checkout.client.get(id, namespace)``.
"""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from typing import Any

import pytest
from structlog.testing import capture_logs

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state import _tier_routing, memory_adapter
from trw_mcp.state._tier_routing import USER_NAMESPACE, classify_tier, route_tier

# --------------------------------------------------------------------------- #
# Pure classifier (no I/O)
# --------------------------------------------------------------------------- #


def test_classify_portable_tag_routes_user() -> None:
    assert classify_tier(tags=["directive"], summary="always commit frequently") == "user"


def test_classify_human_directive_routes_user() -> None:
    assert classify_tier(source_type="human", summary="prefer larger ollama models") == "user"


def test_classify_repo_path_routes_project() -> None:
    """A repo-relative file path in content is a strong PROJECT signal."""
    assert classify_tier(tags=["directive"], summary="fix in trw_mcp/state/foo.py:42") == "project"


def test_classify_dotted_symbol_routes_project() -> None:
    assert classify_tier(summary="patch trw_mcp.state.memory_adapter for the bug") == "project"


def test_classify_ambiguous_defaults_project() -> None:
    assert classify_tier(summary="something happened") == "project"


def test_classify_project_tag_beats_portable() -> None:
    assert classify_tier(tags=["directive", "gotcha"], summary="x") == "project"


# --------------------------------------------------------------------------- #
# route_tier: no presence gate (PRD-CORE-280 FR06)
# --------------------------------------------------------------------------- #


def test_route_needs_no_user_store_or_flag_to_route_portable_content_user() -> None:
    """user:local always exists in the one store and is never pushed, so nothing gates the heuristic."""
    assert route_tier(scope="auto", source_type="human", summary="prefer larger ollama models") == "user"
    assert route_tier(scope="user", tags=["directive"]) == "user"
    assert route_tier(scope="auto", summary="fix in trw_mcp/state/foo.py:42") == "project"


def test_route_explicit_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    # explicit project beats the automatic heuristic
    assert route_tier(scope="project", tags=["directive"]) == "project"
    # explicit user forces user for path-free content
    assert route_tier(scope="user", summary="prefer larger models") == "user"


# --------------------------------------------------------------------------- #
# P2-C: scope="user" override is HONORED even for project-looking content, but
# a structured warning surfaces the cross-project leak risk (WARN + HONOR).
# The FR07 contract requires the explicit override to win; the veto would
# silently override the user's deliberate choice.
# --------------------------------------------------------------------------- #


def _warning_events(captured: list[MutableMapping[str, Any]]) -> list[str]:
    """Pull warning-level event names from a ``capture_logs()`` buffer."""
    return [str(rec.get("event")) for rec in captured if rec.get("log_level") == "warning"]


def test_route_user_override_honored_with_repo_path_and_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit scope=user is honored for repo-paths but emits a leak-risk warning."""
    with capture_logs() as captured:
        tier = route_tier(scope="user", summary="fix in trw_mcp/state/foo.py:42")
    assert tier == "user"
    assert "tier_routing_user_override_project_signal" in _warning_events(captured)


def test_route_user_override_honored_with_dotted_symbol_and_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with capture_logs() as captured:
        tier = route_tier(scope="user", summary="patch trw_mcp.state.memory_adapter call")
    assert tier == "user"
    assert "tier_routing_user_override_project_signal" in _warning_events(captured)


def test_route_user_override_honored_with_project_tag_and_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with capture_logs() as captured:
        tier = route_tier(scope="user", tags=["gotcha"], summary="x")
    assert tier == "user"
    assert "tier_routing_user_override_project_signal" in _warning_events(captured)


def test_route_user_override_honored_without_project_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path-free content still honors the explicit user override."""
    assert route_tier(scope="user", summary="always commit frequently") == "user"


def test_has_project_signal_detects_paths_and_tags() -> None:
    assert _tier_routing.has_project_signal(summary="src/x/y.ts changed") is True
    assert _tier_routing.has_project_signal(tags=["repo-local"]) is True
    assert _tier_routing.has_project_signal(summary="prefer larger models") is False


# --------------------------------------------------------------------------- #
# core185-1: _PATH_RE must NOT fire on YAML-style numeric values or version
# strings. These are portable operator-directive content; a false project
# signal silently routes them to the project tier under scope="auto" (no log).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "set timeout:30 for the gate",
        "use priority:1 directives first",
        "retries:3 on transient outages",
        "we run on Python 3.11.5 everywhere",
        "node 18.20.3 is the baseline",
        "tag the release v1.2.3 and ship",
        "bump the budget to 30",
    ],
)
def test_has_project_signal_ignores_versions_and_yaml_values(text: str) -> None:
    """Version strings + colon-number config values are NOT project signals."""
    assert _tier_routing.has_project_signal(summary=text) is False
    assert _tier_routing.has_project_signal(detail=text) is False


@pytest.mark.parametrize(
    "text",
    [
        "fix in trw_mcp/state/foo.py:42",
        "patch trw_mcp.state.memory_adapter for the bug",
        "src/x/y.ts changed",
        "see foo/bar.md:42 for context",
        "error raised at config.yaml:12",
    ],
)
def test_has_project_signal_still_detects_real_paths(text: str) -> None:
    """Genuine file paths / file:line refs / dotted symbols remain project signals."""
    assert _tier_routing.has_project_signal(summary=text) is True


# --------------------------------------------------------------------------- #
# core185-URL-OVERMATCH-2 + core185-DOTTED-TWOSEG-4: the _PATH_RE must be
# reconciled holistically. It MUST:
#   (a) match real file:line refs + dotted module paths INCLUDING 2-segment
#       (foo.bar, os.path, my_module.HelperClass),
#   (b) NOT match version strings (3.11.5), URLs (host.tld/x.ext), or
#       YAML-style values (timeout:30).
# The URL fix (exclude dotted first-segments / protocol URLs) and the 2-seg
# relaxation interact -- both are fixed together below.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "curl -fsSL https://trwframework.com/install.sh | bash",
        "see http://example.org/page.html for details",
        "fetch https://cdn.example.com/lib.js now",
        "the host trwframework.com/install.sh serves it",
    ],
)
def test_has_project_signal_ignores_urls(text: str) -> None:
    """core185-URL-OVERMATCH-2: a hostname+path inside a URL is NOT a project signal."""
    assert _tier_routing.has_project_signal(summary=text) is False
    assert _tier_routing.has_project_signal(detail=text) is False


@pytest.mark.parametrize(
    "text",
    [
        "patch trw_mcp.state for the bug",
        "the os.path helper is wrong",
        "call memory_adapter.store_learning here",
        "see my_module.HelperClass usage",
        "foo.bar is the entry point",
    ],
)
def test_has_project_signal_detects_two_segment_dotted(text: str) -> None:
    """core185-DOTTED-TWOSEG-4: two-segment dotted module refs are project signals."""
    assert _tier_routing.has_project_signal(summary=text) is True


# --------------------------------------------------------------------------- #
# core185-DOTTED-ABBREV-7: the dotted-module alternative must NOT treat the
# prose abbreviations "e.g" / "i.e" (with or without a trailing dot) as a
# dotted module path, or portable prose gets mis-classified as project-specific.
# A real module that merely STARTS with that shape (e.go, i.eat, e.gc.foo) and
# legit 1-char-segment paths (a.b.c) must still match.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "prefer batching commits, e.g when refactoring",
        "use a sentinel default, i.e None, for optional args",
        "E.G the larger ollama model",
        "I.E commit after each logical unit",
        "e.g.",
        "i.e.",
    ],
)
def test_has_project_signal_ignores_prose_abbreviations(text: str) -> None:
    """core185-DOTTED-ABBREV-7: bare e.g / i.e prose abbrevs are NOT project signals."""
    assert _tier_routing.has_project_signal(summary=text) is False
    assert _tier_routing.has_project_signal(detail=text) is False


@pytest.mark.parametrize(
    "text",
    [
        "the e.go package is the entry point",  # real 2-seg module starting with e.g-ish
        "see i.eat module for context",
        "patch e.gc.foo here",
        "the a.b.c dotted path",
    ],
)
def test_has_project_signal_still_detects_modules_resembling_abbrevs(text: str) -> None:
    """core185-DOTTED-ABBREV-7: real module paths near the abbrev shape still match."""
    assert _tier_routing.has_project_signal(summary=text) is True


def test_abbreviation_does_not_veto_portable_to_project() -> None:
    """core185-DOTTED-ABBREV-7: a portable directive using e.g/i.e stays user-tier."""
    assert classify_tier(tags=["directive"], summary="commit frequently, e.g after each unit") == "user"


def test_url_with_portable_tag_routes_user() -> None:
    """core185-URL-OVERMATCH-2: a portable directive carrying a URL is NOT vetoed to project."""
    assert (
        classify_tier(
            tags=["directive"],
            summary="install via curl -fsSL https://trwframework.com/install.sh | bash",
        )
        == "user"
    )


def test_two_segment_dotted_vetoes_portable_to_project() -> None:
    """core185-DOTTED-TWOSEG-4: a 2-seg repo-local symbol vetoes a portable tag to project."""
    assert classify_tier(tags=["directive"], summary="fix os.path handling") == "project"


def test_classify_version_string_directive_routes_user() -> None:
    """A portable directive carrying a version string is no longer vetoed to project."""
    assert classify_tier(tags=["directive"], summary="standardize on Python 3.11.5") == "user"


def test_classify_yaml_value_directive_routes_user() -> None:
    assert classify_tier(tags=["directive"], summary="set the gate timeout:30") == "user"


# --------------------------------------------------------------------------- #
# End-to-end store routing (physical namespace placement)
# --------------------------------------------------------------------------- #


def _get(daemon_checkout: DaemonCheckout, entry_id: str, namespace: str) -> dict[str, Any] | None:
    return asyncio.run(daemon_checkout.client.get(entry_id, namespace)).get("entry")


def test_portable_write_lands_in_user_store(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A portable learning at scope=auto routes to the user store, not project."""
    trw_dir = daemon_checkout.trw_dir
    result = memory_adapter.store_learning(
        trw_dir,
        "L-portable1",
        "always commit frequently per operator",
        "operator directive about cadence",
        tags=["directive"],
        source_type="human",
    )
    assert result["status"] == "recorded"

    entry = _get(daemon_checkout, "L-portable1", USER_NAMESPACE)
    assert entry is not None
    assert entry["namespace"] == USER_NAMESPACE

    # NOT in the project store.
    assert _get(daemon_checkout, "L-portable1", daemon_checkout.namespace) is None


def test_project_write_lands_in_project_store(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    """A project-specific learning (repo path) stays in the project store."""
    trw_dir = daemon_checkout.trw_dir
    memory_adapter.store_learning(
        trw_dir,
        "L-projspecific",
        "bug in trw_mcp/state/memory_adapter.py recall path",
        "repo-local detail",
        tags=["directive"],  # portable tag, but the path overrides -> project
    )
    entry = _get(daemon_checkout, "L-projspecific", daemon_checkout.namespace)
    assert entry is not None
    assert entry["namespace"] == daemon_checkout.namespace

    assert _get(daemon_checkout, "L-projspecific", USER_NAMESPACE) is None


def test_native_user_entry_stamps_metadata_tier(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core185-11: native user-tier entries stamp metadata['tier']='user'.

    Promoted-via-backfill entries already stamp it; native entries must match so
    a caller reading entry.metadata['tier'] sees a consistent schema regardless
    of how the user-tier entry was created.
    """
    trw_dir = daemon_checkout.trw_dir
    memory_adapter.store_learning(
        trw_dir,
        "L-native-tier",
        "operator cadence directive",
        "always commit frequently",
        tags=["directive"],
        source_type="human",
    )
    entry = _get(daemon_checkout, "L-native-tier", USER_NAMESPACE)
    assert entry is not None
    assert entry["metadata"].get("tier") == "user"


def test_project_entry_does_not_stamp_metadata_tier(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core185-11: project-tier entries keep the back-compat metadata (no tier key)."""
    trw_dir = daemon_checkout.trw_dir
    memory_adapter.store_learning(
        trw_dir,
        "L-proj-no-tier",
        "bug in trw_mcp/state/memory_adapter.py recall path",
        "repo-local detail",
        scope="project",
    )
    entry = _get(daemon_checkout, "L-proj-no-tier", daemon_checkout.namespace)
    assert entry is not None
    assert "tier" not in entry["metadata"]


def test_caller_metadata_tier_injection_overridden_on_project_route(
    daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core185-METADATA-TIER-INJECT-5: caller metadata['tier'] cannot force user routing.

    A caller passing ``metadata={'tier': 'user'}`` on content that routes to the
    PROJECT tier must NOT have that injected key survive -- it would make
    ``tier_of_entry()`` return 'user' and the entry would be written to the user
    backend despite the project classification. The routing decision is
    authoritative over caller-supplied metadata.
    """
    trw_dir = daemon_checkout.trw_dir
    memory_adapter.store_learning(
        trw_dir,
        "L-inject",
        "bug in trw_mcp/state/memory_adapter.py recall path",
        "repo-local detail",
        metadata={"tier": "user"},  # injection attempt on project-routed content
    )
    entry = _get(daemon_checkout, "L-inject", daemon_checkout.namespace)
    assert entry is not None, "project-routed entry must land in the project store"
    # The injected user tier must be stripped (project entries carry no tier key).
    assert entry["metadata"].get("tier") != "user"
    assert _get(daemon_checkout, "L-inject", USER_NAMESPACE) is None


def test_explicit_scope_user_override(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = daemon_checkout.trw_dir
    memory_adapter.store_learning(
        trw_dir,
        "L-forceuser",
        "ambiguous content",
        "detail",
        scope="user",
    )
    assert _get(daemon_checkout, "L-forceuser", USER_NAMESPACE) is not None


def test_explicit_scope_project_override(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = daemon_checkout.trw_dir
    memory_adapter.store_learning(
        trw_dir,
        "L-forceproj",
        "always commit frequently",
        "detail",
        tags=["directive"],
        source_type="human",
        scope="project",
    )
    assert _get(daemon_checkout, "L-forceproj", daemon_checkout.namespace) is not None
    assert _get(daemon_checkout, "L-forceproj", USER_NAMESPACE) is None
