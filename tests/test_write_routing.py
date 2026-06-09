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
    return [
        str(rec.get("event"))
        for rec in captured
        if rec.get("log_level") == "warning"
    ]


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


def test_has_project_signal_detects_paths_and_tags() -> None:
    assert _tier_routing.has_project_signal(summary="src/x/y.ts changed") is True
    assert _tier_routing.has_project_signal(tags=["repo-local"]) is True
    assert _tier_routing.has_project_signal(summary="prefer larger models") is False


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
