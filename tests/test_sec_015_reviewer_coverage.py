"""PRD-SEC-015 Phase-1 — coverage that closes gaps left by T15-T20's dedicated
files (``test_reviewer_surface_contract.py``, ``test_reviewer_surface_enforcement.py``,
``test_reviewer_ceremony_exemption.py``, ``test_reviewer_docs_claims.py``).

Two properties earn a NEW file rather than an append:

1. Every existing enforcement test selects the reviewer role by monkeypatching
   ``get_config`` with a HAND-ROLLED stand-in class carrying a ``surface_role``
   attribute. That proves the middleware's own branch logic; this file drives
   the REAL ``trw_mcp.models.config._loader._build_config()`` path (the
   precedent is ``test_config_retired_key_warning.py::
   test_the_loader_calls_the_warning_on_the_real_path`` — monkeypatch
   ``_read_yaml_overrides`` so the merge cascade runs for real without touching
   this developer box's actual ``~/.trw/config.yaml``) to prove the FR02/FR14
   properties empirically against a REAL ``TRWConfig`` object (not a
   stand-in class) rather than assume them: a project ``.trw/config.yaml`` CAN
   select the role (FR02, now that the typed field has landed), an env-declared
   role still dominates a project config's ``surface_role``/
   ``tool_resolution_mode: all`` (FR14), and the role cannot be widened by
   ``tool_resolution_mode: all`` regardless of which layer declared it.
2. The bogus-env-value cases in ``test_reviewer_surface_enforcement.py`` (
   ``test_the_env_marker_is_case_insensitive_and_ignores_a_bogus_value``) only
   exercise the raw parser ``_env_marks_reviewer()`` in isolation. This file
   drives the same values through the REAL settings-construction path: since
   FR02 landed, ``surface_role`` is a typed ``Literal["agent", "reviewer"]``
   BaseSettings field, so ``TRW_SURFACE_ROLE`` is no longer read ONLY by the
   raw parser — it is also parsed directly into that field. A value that is
   not the sentinel after case/whitespace normalization (mirrored by a
   ``field_validator`` on the field itself, ``_fields_tools.py::
   _normalize_surface_role``) is therefore a vocabulary-gate rejection at
   config-construction time (``ValidationError``), not a silent fall-through —
   "a typo must not resolve to a role at all, in either direction"
   (``test_reviewer_surface_config.py::
   test_a_bogus_role_is_rejected_rather_than_silently_becoming_reviewer``).
   This file proves that end to end against the real loader.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from structlog.testing import capture_logs

from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware, reset_surface_authority_state
from trw_mcp.models.config import TRWConfig, get_config, reload_config
from trw_mcp.models.config import _loader as config_loader
from trw_mcp.models.config._retired_keys import _reset_warned_keys
from trw_mcp.models.phase_policy import RIGID_TOOLS
from trw_mcp.models.surface_packs import OPERATOR_ONLY_TOOLS, REVIEWER_TOOLS
from trw_mcp.tools import phase_overrides

pytestmark = pytest.mark.unit

_MOD = "trw_mcp.middleware.surface_authority"


@dataclass
class _FakeTool:
    name: str


@dataclass
class _FakeContext:
    _session_id: str = "coverage-sess"

    @property
    def session_id(self) -> str:
        return self._session_id


@dataclass
class _FakeMessage:
    name: str


@dataclass
class _FakeMiddlewareContext:
    message: Any = None
    fastmcp_context: _FakeContext | None = None


_EXECUTED = object()


async def _execute(_ctx: Any) -> Any:
    return _EXECUTED


@pytest.fixture
def middleware() -> SurfaceAuthorityMiddleware:
    return SurfaceAuthorityMiddleware()


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    phase_overrides.reset_overrides()
    reset_surface_authority_state()
    _reset_warned_keys()
    yield
    phase_overrides.reset_overrides()
    reset_surface_authority_state()
    _reset_warned_keys()


def _build_real_config(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]) -> TRWConfig:
    """Build a REAL ``TRWConfig`` through the production merge cascade.

    Mirrors ``test_config_retired_key_warning.py::
    test_the_loader_calls_the_warning_on_the_real_path``: ``_read_yaml_overrides``
    is stubbed so the SAME dict is returned for both the machine and project
    layers (harmless — a deep-merge of a mapping with itself is a no-op), which
    keeps the test hermetic against whatever ``~/.trw/config.yaml`` happens to
    exist on the machine running it, while every other step (env-shadowing
    filter, unrecognised-key warning, ``extra="ignore"`` drop) is the real code.
    """
    monkeypatch.setattr(config_loader, "_read_yaml_overrides", lambda _path: dict(overrides))
    monkeypatch.setattr(config_loader, "resolve_platform_api_key", lambda _path: "")
    cfg = config_loader._build_config()
    reload_config(cfg)
    return cfg


# ── FR02/FR14: the typed field is landed — config-only selection now works ──


def test_a_real_config_yaml_declaring_surface_role_is_now_a_recognised_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR02 has landed: ``TRWConfig`` now defines ``surface_role``, so a project
    ``.trw/config.yaml`` naming it is no longer dropped by ``extra="ignore"`` or
    warned about as an unrecognised key — it is a live, typed attribute."""
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)

    with capture_logs() as logs:
        cfg = _build_real_config(monkeypatch, {"surface_role": "reviewer"})

    assert cfg.surface_role == "reviewer"
    unrecognised = [entry for entry in logs if entry["event"] == "config_key_not_recognised"]
    assert not any(entry.get("config_key") == "surface_role" for entry in unrecognised), logs


async def test_the_same_config_file_now_bounds_the_real_middleware_without_the_env_marker(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion to the test above, driven through the REAL production
    ``TRWConfig`` singleton (not a hand-rolled stand-in class): now that FR02
    has landed, a project ``.trw/config.yaml`` alone (no env marker) DOES
    activate the role — config-only selection is a legitimate FR02 path. FR14
    is what stops an env-declared role from being DOWNGRADED by a hostile
    config, not what stops a config-only declaration from working at all."""
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    _build_real_config(monkeypatch, {"surface_role": "reviewer"})
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: None)

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())
    denied = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]
    assert denied is not _EXECUTED
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"

    tools = [_FakeTool(name=n) for n in sorted(REVIEWER_TOOLS | {"trw_deliver", "trw_build_check"})]

    async def call_next(_ctx: Any) -> Any:
        return tools

    list_ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    listed = await middleware.on_list_tools(list_ctx, call_next)  # type: ignore[arg-type]
    assert {t.name for t in listed} == set(REVIEWER_TOOLS)


# ── FR03/FR14: a REAL project config.yaml cannot widen an env-declared reviewer ──


async def test_a_real_project_config_with_mode_all_still_denies_the_reviewer(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stronger form of the existing ``mode="all"`` regression: here
    ``tool_resolution_mode`` is set on a REAL ``TRWConfig`` built through the
    production merge cascade (as an audited project's ``.trw/config.yaml``
    would set it), not a hand-rolled class. The env-declared role must still
    dominate it."""
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    cfg = _build_real_config(monkeypatch, {"tool_resolution_mode": "all"})
    assert cfg.tool_resolution_mode == "all"  # non-vacuity: the override really landed

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())
    denied = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    assert denied is not _EXECUTED
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"
    assert denied.structured_content["surface_role"] == "reviewer"

    tools = [_FakeTool(name=n) for n in sorted(REVIEWER_TOOLS | {"trw_deliver"})]

    async def call_next(_ctx: Any) -> Any:
        return tools

    list_ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    listed = await middleware.on_list_tools(list_ctx, call_next)  # type: ignore[arg-type]
    assert {t.name for t in listed} == set(REVIEWER_TOOLS)


async def test_the_same_real_config_without_the_env_marker_is_fully_widened(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-vacuity: the SAME real ``mode="all"`` config, without the env
    declaration, is an ordinary widened agent session (proves the denial above
    comes from the role, not from some other effect of the stubbed loader)."""
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    _build_real_config(monkeypatch, {"tool_resolution_mode": "all"})

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())
    assert await middleware.on_call_tool(ctx, _execute) is _EXECUTED  # type: ignore[arg-type]


# ── Bogus env values through the REAL settings-construction path ───────


@pytest.mark.parametrize("bogus_value", ["admin", "REVIEWERX", "reviewer-ish", "operator"])
def test_bogus_role_values_are_rejected_at_real_config_construction(
    monkeypatch: pytest.MonkeyPatch, bogus_value: str
) -> None:
    """FR02: a value that merely LOOKS role-shaped is a vocabulary-gate
    rejection, not a silent fall-through to agent. Since ``surface_role`` is
    now a typed ``Literal["agent", "reviewer"]`` BaseSettings field,
    ``TRW_SURFACE_ROLE`` is parsed directly into it — end to end through the
    REAL production loader, not just the isolated ``_env_marks_reviewer()``
    parser (``test_reviewer_surface_enforcement.py``) or the direct
    ``TRWConfig()`` constructor (``test_reviewer_surface_config.py::
    test_a_bogus_role_is_rejected_rather_than_silently_becoming_reviewer``)."""
    from pydantic import ValidationError

    monkeypatch.setenv("TRW_SURFACE_ROLE", bogus_value)

    with pytest.raises(ValidationError):
        _build_real_config(monkeypatch, {})


def test_empty_surface_role_env_value_is_also_rejected_at_real_config_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-vacuity edge case: an explicitly EMPTY ``TRW_SURFACE_ROLE`` (distinct
    from unset) is set, not absent, so it reaches the same vocabulary gate as
    any other non-member string rather than silently defaulting to 'agent'."""
    from pydantic import ValidationError

    monkeypatch.setenv("TRW_SURFACE_ROLE", "")

    with pytest.raises(ValidationError):
        _build_real_config(monkeypatch, {})


async def test_an_unset_surface_role_still_behaves_as_an_ordinary_agent_through_the_real_middleware(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-vacuity for the rejection tests above: with NO env var at all (the
    ordinary case for every existing session), the real middleware still
    executes the RIGID tool ``trw_deliver`` and advertises the full surface —
    proving the rejection above is about a SET-but-invalid value, not about
    ``surface_role`` breaking ordinary agent sessions."""
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    _build_real_config(monkeypatch, {})
    monkeypatch.setattr(f"{_MOD}.resolve_task_type", lambda **_: None)

    assert "trw_deliver" in RIGID_TOOLS  # non-vacuity: this tool IS excluded from REVIEWER_TOOLS
    assert "trw_deliver" not in REVIEWER_TOOLS

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())
    result = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]
    assert result is _EXECUTED

    tools = [_FakeTool(name=n) for n in sorted(REVIEWER_TOOLS | {"trw_deliver", "trw_build_check"})]

    async def call_next(_ctx: Any) -> Any:
        return tools

    list_ctx = _FakeMiddlewareContext(fastmcp_context=_FakeContext())
    listed = await middleware.on_list_tools(list_ctx, call_next)  # type: ignore[arg-type]
    advertised = {t.name for t in listed}
    assert "trw_deliver" in advertised
    assert advertised != set(REVIEWER_TOOLS)


async def test_only_the_exact_reviewer_sentinel_activates_the_bound(
    middleware: SurfaceAuthorityMiddleware, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control for the parametrized negative above, in the same
    end-to-end shape: the exact sentinel (case/whitespace-normalized) DOES
    collapse the surface and DOES deny the RIGID tool."""
    monkeypatch.setenv("TRW_SURFACE_ROLE", " Reviewer ")
    _build_real_config(monkeypatch, {})

    ctx = _FakeMiddlewareContext(message=_FakeMessage("trw_deliver"), fastmcp_context=_FakeContext())
    denied = await middleware.on_call_tool(ctx, _execute)  # type: ignore[arg-type]

    assert denied is not _EXECUTED
    assert denied.structured_content is not None
    assert denied.structured_content["error_type"] == "tool_not_in_reviewer_surface"


# ── FR01: reviewer tools are never operator-only ────────────────────────


def test_reviewer_tools_are_disjoint_from_operator_only_tools() -> None:
    """A reviewer surface must never include an operator-only tool — that class
    is excluded from the eligible public surface entirely, so a member here
    would be a silent widening beyond what any ordinary agent can even reach."""
    assert REVIEWER_TOOLS.isdisjoint(OPERATOR_ONLY_TOOLS)


# ── FR05: the ceremony exemption compared symmetrically in one test ─────


async def test_reviewer_and_agent_sessions_diverge_on_the_ceremony_warning_in_one_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct side-by-side proof (rather than two separately-verified files):
    the SAME inactive-session precondition and the SAME tool call produce a
    prepended warning for an ordinary agent and none for a reviewer."""
    from mcp.types import TextContent

    from tests._test_ceremony_middleware_gate_support import (
        FakeContext,
        FakeMessage,
        FakeMiddlewareContext,
        FakeRequestContext,
        FakeToolResult,
        _text,
    )
    from trw_mcp.middleware.ceremony import CeremonyMiddleware, reset_state

    reset_state()
    mw = CeremonyMiddleware()

    async def call_next(_ctx: Any) -> Any:
        return FakeToolResult(content=[TextContent(type="text", text="ok")])

    agent_ctx = FakeMiddlewareContext(
        message=FakeMessage(name="trw_code_search"),
        fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id="agent-sess")),
    )
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    agent_out = await mw.on_call_tool(agent_ctx, call_next)  # type: ignore[arg-type]

    reviewer_ctx = FakeMiddlewareContext(
        message=FakeMessage(name="trw_code_search"),
        fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id="reviewer-sess")),
    )
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    reviewer_out = await mw.on_call_tool(reviewer_ctx, call_next)  # type: ignore[arg-type]

    assert len(agent_out.content) == 2, "an ordinary inactive session must still get the ceremony warning"
    assert len(reviewer_out.content) == 1, "a reviewer session must never get the ceremony warning"
    assert _text(reviewer_out.content[0]) == "ok"


# ── Sanity: the fixture helper itself builds a config the loader would ─────


def test_build_real_config_helper_round_trips_a_recognised_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-vacuity for ``_build_real_config``: a field TRWConfig DOES define
    must actually land on the object, or every test above would be passing
    for the wrong reason (the override silently not applying at all)."""
    cfg = _build_real_config(monkeypatch, {"tool_resolution_mode": "all"})
    assert cfg.tool_resolution_mode == "all"
    assert get_config() is cfg
