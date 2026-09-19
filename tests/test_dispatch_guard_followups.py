"""Regression tests for two PRD-CORE-281 guard follow-ups (REPAIR-DESIGN-01).

(a) Codex posture overrides through ``extra_args``. The documented contract is that
    ``extra_args`` "must never become a back door that re-enables writes, disables isolation,
    or points the child at a different config/MCP" (``dispatch/_types.py``,
    ``_reject_security_override_tokens``). For codex that includes the raw config override
    ``-c``/``--config`` (it can set any nested key, including the TRW server's ``env`` table)
    and the short aliases ``-s`` (sandbox) and ``-a`` (approval policy) of flags the floor
    already refuses in their long form. Every spelling is covered: split, ``=``, attached.
    Other clients keep their own meaning of those letters: opencode's ``-s`` selects a session.

(b) Template substitution. A launcher path that legitimately contains the literal text
    ``{mcp_args}`` must stay literal in both rendered templates rather than being re-expanded
    into quotes inside the child's JSON/TOML config literal. The existing refusals of quote,
    backslash and control characters must survive the repair.

Pure: no subprocess, no dispatch. ``mcp_server_launcher`` is replaced only to supply an
input path; the renderers under test run unmodified.
"""

from __future__ import annotations

import json
from typing import Literal

import pytest
from pydantic import ValidationError

from trw_mcp.dispatch import DispatchRequest, _posture
from trw_mcp.dispatch._client_specs import client_spec_for
from trw_mcp.dispatch._types import effective_forbidden_tokens

pytestmark = pytest.mark.unit

Posture = Literal["default", "with_trw", "reviewer"]


def _codex_request(posture: Posture, extra: tuple[str, ...]) -> DispatchRequest:
    if posture == "with_trw":
        return DispatchRequest(client="codex", prompt="hi", with_trw=True, extra_args=extra)
    if posture == "reviewer":
        return DispatchRequest(client="codex", prompt="hi", posture="reviewer", extra_args=extra)
    return DispatchRequest(client="codex", prompt="hi", extra_args=extra)


# ── (a) codex posture overrides are refused in every accepted spelling ──

_OVERRIDE = "mcp_servers.trw.env={}"
_CODEX_OVERRIDE_FORMS: list[tuple[str, ...]] = [
    ("-c", _OVERRIDE),
    ("--config", _OVERRIDE),
    (f"--config={_OVERRIDE}",),
    (f"-c={_OVERRIDE}",),
    (f"-c{_OVERRIDE}",),
    ("-c", 'model_provider="other"'),  # a non-mcp key: ALL raw overrides, not a key denylist
    ("-s", "danger-full-access"),
    ("-s=danger-full-access",),
    ("-sdanger-full-access",),
    ("-a", "never"),
    ("-a=never",),
    ("-anever",),
]
_FORM_IDS = [
    "c-split",
    "config-split",
    "config-eq",
    "c-eq",
    "c-attached",
    "c-other-key",
    "s-split",
    "s-eq",
    "s-attached",
    "a-split",
    "a-eq",
    "a-attached",
]
_POSTURES: list[Posture] = ["default", "with_trw", "reviewer"]


@pytest.mark.parametrize("posture", _POSTURES)
@pytest.mark.parametrize("extra", _CODEX_OVERRIDE_FORMS, ids=_FORM_IDS)
def test_codex_refuses_posture_overrides_in_extra_args(posture: Posture, extra: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="extra_args may not override"):
        _codex_request(posture, extra)


@pytest.mark.parametrize("token", ["-c", "--config", "-s", "-a"])
def test_codex_reported_floor_names_every_refused_flag(token: str) -> None:
    """The effective floor must REPORT what is enforced, not hide it in a side predicate."""
    assert token in effective_forbidden_tokens("codex")


def test_opencode_keeps_its_session_selector() -> None:
    """``-s`` means a session to opencode; a codex rule must not leak to other clients."""
    req = DispatchRequest(client="opencode", prompt="hi", extra_args=("-s", "ses_1"))
    assert req.extra_args == ("-s", "ses_1")
    assert "-s" not in effective_forbidden_tokens("opencode")


def test_benign_codex_extra_args_are_still_accepted() -> None:
    assert DispatchRequest(client="codex", prompt="hi", extra_args=("--verbose",)).extra_args == ("--verbose",)


def test_typed_codex_fields_are_unaffected() -> None:
    """The supported way to set these is the typed fields, which must keep working."""
    assert DispatchRequest(client="codex", prompt="hi", model="gpt-5").model == "gpt-5"
    assert DispatchRequest(client="codex", prompt="hi", read_only=False).read_only is False
    assert DispatchRequest(client="codex", prompt="hi", posture="reviewer").posture == "reviewer"
    assert DispatchRequest(client="codex", prompt="hi", with_trw=True).with_trw is True


# ── (b) a brace-containing launcher path stays literal in both renderers ──

_BRACE_PATH = "/opt/py{mcp_args}/bin/python"
# Every known placeholder, plus an unknown one, must stay literal inside a substituted value.
_BRACE_PATHS = [_BRACE_PATH, "/opt/py{mcp_command}/bin/python", "/opt/py{reviewer_tools}/bin/python", "/opt/{x}/python"]
_ARGS = ("-m", "trw_mcp.server")
Renderer = Literal["reviewer", "trw_access"]


def _render(renderer: Renderer, client: str) -> list[str]:
    spec = client_spec_for(client)
    if renderer == "reviewer":
        return _posture.render_reviewer_argv(spec)
    return _posture.render_trw_access_argv(spec)


def _use_launcher(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setattr(_posture, "mcp_server_launcher", lambda: (path, _ARGS))


@pytest.mark.parametrize("path", _BRACE_PATHS)
@pytest.mark.parametrize("renderer", ["reviewer", "trw_access"])
def test_claude_mcp_config_keeps_a_brace_path_literal(
    monkeypatch: pytest.MonkeyPatch, renderer: Renderer, path: str
) -> None:
    _use_launcher(monkeypatch, path)
    argv = _render(renderer, "claude")
    payload = json.loads(argv[argv.index("--mcp-config") + 1])

    assert payload["mcpServers"]["trw"]["command"] == path
    assert payload["mcpServers"]["trw"]["args"] == list(_ARGS)


@pytest.mark.parametrize("path", _BRACE_PATHS)
@pytest.mark.parametrize("renderer", ["reviewer", "trw_access"])
def test_codex_command_override_keeps_a_brace_path_literal(
    monkeypatch: pytest.MonkeyPatch, renderer: Renderer, path: str
) -> None:
    _use_launcher(monkeypatch, path)
    argv = _render(renderer, "codex")

    overrides = [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == "-c"]
    commands = [token for token in overrides if token.startswith("mcp_servers.trw_dispatch_") and ".command=" in token]
    assert len(commands) == 1
    prefix = commands[0].split(".command=", 1)[0]
    assert commands[0] == f'{prefix}.command="{path}"'
    assert f"{prefix}.args={json.dumps(list(_ARGS))}" in overrides
    assert "mcp_servers.trw.enabled=false" in overrides


@pytest.mark.parametrize("renderer", ["reviewer", "trw_access"])
@pytest.mark.parametrize(
    "bad", ['/opt/py"x/bin/python', "/opt/py\\x/bin/python", "/opt/py\nx/bin/python", "/opt/py\rx", "/opt/py\tx"]
)
def test_unsafe_launcher_characters_are_still_refused(
    monkeypatch: pytest.MonkeyPatch, renderer: Renderer, bad: str
) -> None:
    _use_launcher(monkeypatch, bad)
    with pytest.raises((_posture.ReviewerPostureError, _posture.TrwAccessError), match="cannot be embedded"):
        _render(renderer, "claude")
