"""PRD-CORE-185 FR05: portability classifier + automatic write routing.

Portable learnings (operator directives, cross-cutting patterns, raw-context
drops) route to the machine-local USER tier (``user:<id>`` + the user-home
store) when a user-scope store is present; project-specific learnings (file
paths, repo-local symbols) stay in the PROJECT tier. The gate is presence of
the store, not a user toggle; default is project (conservative).
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from trw_mcp.models.config import _reset_config
from trw_mcp.state import _tier_routing, memory_adapter
from trw_mcp.state._tier_routing import USER_NAMESPACE, classify_tier, route_tier
from trw_mcp.state._user_tier import get_user_backend, reset_user_backend


@pytest.fixture(autouse=True)
def _isolated_user_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the user store at a clean tmp dir + enable the user tier."""
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "true")
    _reset_config()
    memory_adapter.reset_backend()
    reset_user_backend()
    yield
    memory_adapter.reset_backend()
    reset_user_backend()
    _reset_config()


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
# route_tier gate: no user store -> always project
# --------------------------------------------------------------------------- #


def test_route_no_user_scope_forces_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no user-scope store is present, ALL writes go project (override irrelevant)."""
    monkeypatch.setattr(_tier_routing, "user_scope_present", lambda: False)
    assert route_tier(scope="user", tags=["directive"]) == "project"
    assert route_tier(scope="auto", source_type="human") == "project"


def test_route_explicit_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_tier_routing, "user_scope_present", lambda: True)
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
    monkeypatch.setattr(_tier_routing, "user_scope_present", lambda: True)
    with capture_logs() as captured:
        tier = route_tier(scope="user", summary="fix in trw_mcp/state/foo.py:42")
    assert tier == "user"
    assert "tier_routing_user_override_project_signal" in _warning_events(captured)


def test_route_user_override_honored_with_dotted_symbol_and_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_tier_routing, "user_scope_present", lambda: True)
    with capture_logs() as captured:
        tier = route_tier(scope="user", summary="patch trw_mcp.state.memory_adapter call")
    assert tier == "user"
    assert "tier_routing_user_override_project_signal" in _warning_events(captured)


def test_route_user_override_honored_with_project_tag_and_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_tier_routing, "user_scope_present", lambda: True)
    with capture_logs() as captured:
        tier = route_tier(scope="user", tags=["gotcha"], summary="x")
    assert tier == "user"
    assert "tier_routing_user_override_project_signal" in _warning_events(captured)


def test_route_user_override_honored_without_project_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path-free content still honors the explicit user override."""
    monkeypatch.setattr(_tier_routing, "user_scope_present", lambda: True)
    assert route_tier(scope="user", summary="always commit frequently") == "user"


def test_user_scope_present_memoizes_disk_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """core185-8: user_scope_present probes the disk at most once until reset."""
    _tier_routing.reset_user_scope_cache()
    # Force the disk-probe branch: config flag False so it falls to Path.exists.
    monkeypatch.setattr(_tier_routing, "_user_scope_cached", None)

    class _Cfg:
        user_tier_enabled = False

    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _Cfg())

    calls = {"n": 0}

    def _resolve(*, create: bool = True) -> Path:
        calls["n"] += 1
        return Path("/nonexistent/user/memory")

    monkeypatch.setattr("trw_mcp.state._user_paths.resolve_user_memory_dir", _resolve)

    first = _tier_routing.user_scope_present()
    second = _tier_routing.user_scope_present()
    assert first is False and second is False
    assert calls["n"] == 1, "disk probe must run once and be memoized"

    # After an explicit reset, the probe runs again.
    _tier_routing.reset_user_scope_cache()
    _tier_routing.user_scope_present()
    assert calls["n"] == 2


def test_reset_user_backend_clears_scope_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """core185-8: reset_user_backend re-arms the presence probe (config change observed)."""
    _tier_routing.reset_user_scope_cache()
    monkeypatch.setattr(_tier_routing, "_user_scope_cached", True)
    reset_user_backend()
    assert _tier_routing._user_scope_cached is None


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
# End-to-end store routing (physical DB placement)
# --------------------------------------------------------------------------- #


def _project_trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / "repoA" / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    return trw_dir


def test_portable_write_lands_in_user_store(tmp_path: Path) -> None:
    """A portable learning at scope=auto routes to the user store, not project."""
    trw_dir = _project_trw_dir(tmp_path)
    result = memory_adapter.store_learning(
        trw_dir,
        "L-portable1",
        "always commit frequently per operator",
        "operator directive about cadence",
        tags=["directive"],
        source_type="human",
    )
    assert result["status"] == "recorded"

    user_backend = get_user_backend()
    entry = user_backend.get("L-portable1")
    assert entry is not None
    assert entry.namespace == USER_NAMESPACE

    # NOT in the project store.
    project_backend = memory_adapter.get_backend(trw_dir)
    assert project_backend.get("L-portable1") is None


def test_project_write_lands_in_project_store(tmp_path: Path) -> None:
    """A project-specific learning (repo path) stays in the project store."""
    trw_dir = _project_trw_dir(tmp_path)
    memory_adapter.store_learning(
        trw_dir,
        "L-projspecific",
        "bug in trw_mcp/state/memory_adapter.py recall path",
        "repo-local detail",
        tags=["directive"],  # portable tag, but the path overrides -> project
    )
    project_backend = memory_adapter.get_backend(trw_dir)
    entry = project_backend.get("L-projspecific")
    assert entry is not None
    assert entry.namespace == "default"

    user_backend = get_user_backend()
    assert user_backend.get("L-projspecific") is None


def test_native_user_entry_stamps_metadata_tier(tmp_path: Path) -> None:
    """core185-11: native user-tier entries stamp metadata['tier']='user'.

    Promoted-via-backfill entries already stamp it; native entries must match so
    a caller reading entry.metadata['tier'] sees a consistent schema regardless
    of how the user-tier entry was created.
    """
    trw_dir = _project_trw_dir(tmp_path)
    memory_adapter.store_learning(
        trw_dir,
        "L-native-tier",
        "operator cadence directive",
        "always commit frequently",
        tags=["directive"],
        source_type="human",
    )
    entry = get_user_backend().get("L-native-tier")
    assert entry is not None
    assert entry.metadata.get("tier") == "user"


def test_project_entry_does_not_stamp_metadata_tier(tmp_path: Path) -> None:
    """core185-11: project-tier entries keep the back-compat metadata (no tier key)."""
    trw_dir = _project_trw_dir(tmp_path)
    memory_adapter.store_learning(
        trw_dir,
        "L-proj-no-tier",
        "bug in trw_mcp/state/memory_adapter.py recall path",
        "repo-local detail",
        scope="project",
    )
    entry = memory_adapter.get_backend(trw_dir).get("L-proj-no-tier")
    assert entry is not None
    assert "tier" not in entry.metadata


def test_caller_metadata_tier_injection_overridden_on_project_route(tmp_path: Path) -> None:
    """core185-METADATA-TIER-INJECT-5: caller metadata['tier'] cannot force user routing.

    A caller passing ``metadata={'tier': 'user'}`` on content that routes to the
    PROJECT tier must NOT have that injected key survive -- it would make
    ``tier_of_entry()`` return 'user' and the entry would be written to the user
    backend despite the project classification. The routing decision is
    authoritative over caller-supplied metadata.
    """
    trw_dir = _project_trw_dir(tmp_path)
    memory_adapter.store_learning(
        trw_dir,
        "L-inject",
        "bug in trw_mcp/state/memory_adapter.py recall path",
        "repo-local detail",
        metadata={"tier": "user"},  # injection attempt on project-routed content
    )
    project_backend = memory_adapter.get_backend(trw_dir)
    entry = project_backend.get("L-inject")
    assert entry is not None, "project-routed entry must land in the project store"
    # The injected user tier must be stripped (project entries carry no tier key).
    assert entry.metadata.get("tier") != "user"
    assert get_user_backend().get("L-inject") is None


def test_explicit_scope_user_override(tmp_path: Path) -> None:
    trw_dir = _project_trw_dir(tmp_path)
    memory_adapter.store_learning(
        trw_dir,
        "L-forceuser",
        "ambiguous content",
        "detail",
        scope="user",
    )
    assert get_user_backend().get("L-forceuser") is not None


def test_explicit_scope_project_override(tmp_path: Path) -> None:
    trw_dir = _project_trw_dir(tmp_path)
    memory_adapter.store_learning(
        trw_dir,
        "L-forceproj",
        "always commit frequently",
        "detail",
        tags=["directive"],
        source_type="human",
        scope="project",
    )
    assert memory_adapter.get_backend(trw_dir).get("L-forceproj") is not None
    assert get_user_backend().get("L-forceproj") is None


def test_no_user_scope_all_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the user tier disabled, a portable learning stays in the project store."""
    monkeypatch.setenv("TRW_USER_TIER_ENABLED", "false")
    _reset_config()
    trw_dir = _project_trw_dir(tmp_path)
    memory_adapter.store_learning(
        trw_dir,
        "L-noscope",
        "always commit frequently per operator",
        "directive",
        tags=["directive"],
        source_type="human",
    )
    assert memory_adapter.get_backend(trw_dir).get("L-noscope") is not None
