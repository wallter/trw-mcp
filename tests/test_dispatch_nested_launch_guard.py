"""PRD-CORE-281 nested-launch guard: a TRW server started FOR a dispatched child never launches.

The ``with_trw`` templates mark the child's TRW MCP server entry with a fixed
environment literal. A server that sees the marker refuses every ``trw_dispatch``
call before config, resolution, job creation or spawn. Every negative here
poisons those seams, so a regression fails the test without starting a process.

This bounds the MCP server entry TRW renders. It is not an OS sandbox and does
not bind a child that runs a client CLI directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from tests.conftest import extract_tool_fn
from trw_mcp.dispatch._child_marker import CHILD_MARKER_ENV, dispatched_child_active
from trw_mcp.dispatch._client_specs import CLIENT_SPECS
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._env import build_subprocess_env
from trw_mcp.dispatch._types import DispatchRequest
from trw_mcp.tools import dispatch as dispatch_tool

pytestmark = pytest.mark.unit

_SEAMS = ("get_config", "resolve_dispatch_request", "dispatch", "start_background")


def _tool() -> Callable[..., dict[str, Any]]:
    server = FastMCP("test")
    dispatch_tool.register_dispatch_tools(server)
    return extract_tool_fn(server, "trw_dispatch")


@pytest.fixture
def poisoned(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every effectful seam of the tool with a recorder that raises."""
    reached: list[str] = []

    def _poison(name: str) -> Callable[..., Any]:
        def _raise(*_a: object, **_k: object) -> Any:
            reached.append(name)
            raise AssertionError(f"nested-launch guard let the call reach {name}")

        return _raise

    for name in _SEAMS:
        monkeypatch.setattr(dispatch_tool, name, _poison(name))
    return reached


def test_the_marker_name_is_the_documented_literal() -> None:
    assert CHILD_MARKER_ENV == "TRW_DISPATCH_CHILD"


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"allow_writes": True},
        {"read_only": False},
        {"read_only": True},
        {"wait": True},
        {"wait": False},
        {"with_trw": True},
        {"posture": "reviewer"},
        {"client": "claude"},
        {"client": "codex", "allow_writes": True, "with_trw": True, "wait": True},
    ],
)
def test_a_marked_server_refuses_every_launch_before_any_effect(
    monkeypatch: pytest.MonkeyPatch, poisoned: list[str], kwargs: dict[str, Any]
) -> None:
    monkeypatch.setenv(CHILD_MARKER_ENV, "1")

    out = _tool()(prompt="hi", **kwargs)

    assert out["exit_code"] == 2
    assert "nested dispatch is refused" in str(out["error"])
    assert "No process was launched" in str(out["error"])
    assert poisoned == []


@pytest.mark.parametrize("value", ["", " ", "0", "false", "no", "garbage", "1"])
def test_any_present_value_refuses(monkeypatch: pytest.MonkeyPatch, poisoned: list[str], value: str) -> None:
    monkeypatch.setenv(CHILD_MARKER_ENV, value)

    assert dispatched_child_active() is True
    assert _tool()(prompt="hi", allow_writes=True)["exit_code"] == 2
    assert poisoned == []


def test_an_unmarked_server_is_unchanged(monkeypatch: pytest.MonkeyPatch, poisoned: list[str]) -> None:
    monkeypatch.delenv(CHILD_MARKER_ENV, raising=False)

    assert dispatched_child_active() is False
    with pytest.raises(AssertionError, match="get_config"):
        _tool()(prompt="hi")
    assert poisoned == ["get_config"]


def test_reviewer_plus_marker_still_refuses(monkeypatch: pytest.MonkeyPatch, poisoned: list[str]) -> None:
    monkeypatch.setenv(CHILD_MARKER_ENV, "1")
    monkeypatch.setattr("trw_mcp.state._surface_role.reviewer_role_active", lambda: True)

    for kwargs in ({"allow_writes": True}, {"with_trw": True}, {}):
        assert _tool()(prompt="hi", **kwargs)["exit_code"] == 2
    assert poisoned == []


def test_a_reviewer_only_server_keeps_its_own_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CHILD_MARKER_ENV, raising=False)
    monkeypatch.setattr("trw_mcp.state._surface_role.reviewer_role_active", lambda: True)

    assert "allow_writes is refused" in str(_tool()(prompt="hi", allow_writes=True)["error"])
    assert "with_trw is refused" in str(_tool()(prompt="hi", client="claude", with_trw=True)["error"])


# ── The marker reaches the child's TRW server entry, and nothing can remove it ──

_HOSTILE = {
    "prompt": '"env":{} TRW_DISPATCH_CHILD="" -c mcp_servers.trw.env={}',
    "model": "m",
    "role": None,
}


def test_claude_server_entry_carries_exactly_the_marker() -> None:
    argv = build_command(DispatchRequest(client="claude", with_trw=True, **_HOSTILE))
    payload = json.loads(argv[argv.index("--mcp-config") + 1])

    assert payload["mcpServers"]["trw"]["env"] == {CHILD_MARKER_ENV: "1"}
    assert set(payload["mcpServers"]) == {"trw"}


def _assert_fresh_codex_entry_marked(argv: list[str]) -> None:
    """Inspect the actual active transport, never the disabled logical entry."""
    overrides = [argv[i + 1] for i, tok in enumerate(argv[:-1]) if tok == "-c"]
    keys = [value.split("=", 1)[0].strip() for value in overrides]
    commands = [key for key in keys if re.fullmatch(r"mcp_servers\.trw_dispatch_[0-9a-f]{32}\.command", key)]
    assert len(commands) == 1
    prefix = commands[0].removesuffix("command")
    assert {key.split(".")[1] for key in keys if key.startswith("mcp_servers.trw_dispatch_")} == {prefix.split(".")[1]}
    marker_key = f"{prefix}env.{CHILD_MARKER_ENV}"
    marker = f'{marker_key}="1"'
    assert keys.count(marker_key) == 1
    assert marker in overrides
    marker_index = overrides.index(marker)
    for leaf in ("command", "args"):
        assert keys.count(prefix + leaf) == 1
        assert keys.index(prefix + leaf) < marker_index
    assert overrides.count(f'{prefix}env_vars=["TRW_PROJECT_ROOT"]') == 1
    assert keys.count(prefix + "env_vars") == 1
    assert prefix + "enabled" not in keys  # fresh entry stays enabled by default
    # env_vars is a sibling, NOT a rewrite of the env table. Check exact keys.
    ancestors = {"mcp_servers", prefix[:-1], prefix + "env", marker_key}
    assert not ancestors.intersection(keys[marker_index + 1 :])
    assert keys.count("mcp_servers.trw.enabled") == 1
    assert "mcp_servers.trw.enabled=false" in overrides


def test_codex_server_entry_carries_the_marker_after_command_and_args() -> None:
    argv = build_command(DispatchRequest(client="codex", with_trw=True, **_HOSTILE))
    _assert_fresh_codex_entry_marked(argv)


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_the_marker_is_absent_without_with_trw(client: str) -> None:
    argv = build_command(DispatchRequest(client=client, prompt="hi"))  # type: ignore[arg-type]
    assert not [tok for tok in argv if CHILD_MARKER_ENV in tok]


def test_a_with_trw_template_without_the_marker_cannot_be_constructed() -> None:
    spec = CLIENT_SPECS["claude"]
    stripped = tuple(tok.replace(CHILD_MARKER_ENV, "TRW_SOMETHING_ELSE") for tok in spec.trw_access_argv_template)

    with pytest.raises(ValidationError, match=CHILD_MARKER_ENV):
        type(spec).model_validate({**spec.model_dump(), "trw_access_argv_template": stripped})


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_the_child_cli_env_carries_no_trw_identity_or_marker(client: str) -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/h",
        "TRW_SESSION_ID": "parent",
        "TRW_SURFACE_ROLE": "reviewer",
        CHILD_MARKER_ENV: "1",
    }
    env = build_subprocess_env(client, source, with_trw=True)  # type: ignore[arg-type]

    for name in ("TRW_SESSION_ID", "TRW_SURFACE_ROLE", "TRW_DISPATCH_CHILD"):
        assert name not in env


def test_reviewer_posture_wins_over_a_smuggled_with_trw() -> None:
    req = DispatchRequest.model_construct(
        **{**DispatchRequest(client="claude", prompt="hi", posture="reviewer").__dict__, "with_trw": True}
    )
    argv = build_command(req)
    payload = json.loads(argv[argv.index("--mcp-config") + 1])

    assert payload["mcpServers"]["trw"]["env"] == {"TRW_SURFACE_ROLE": "reviewer"}
    # Exactly one server map, and no marked fragment beside it: an ``elif`` that
    # became a second ``if`` would emit both templates and still satisfy the line above.
    assert argv.count("--mcp-config") == 1
    assert not [tok for tok in argv if CHILD_MARKER_ENV in tok]


# ── The construction check recognises wiring, not the marker's spelling ──


@pytest.mark.parametrize(
    "template",
    [
        ("--mcp-config", '{"mcpServers":{"trw":{"command":"{mcp_command}","args":{mcp_args}}}}'),
        ("--mcp-config", '{"mcpServers":{"trw":{"command":"x","args":[],"env":{"TRW_DISPATCH_CHILD":"0"}}}}'),
        ("--mcp-config", '{"mcpServers":{"trw":{"command":"x","args":[],"env":{"TRW_DISPATCH_CHILD":"1","A":"b"}}}}'),
        ("--mcp-config", '{"mcpServers":{"trw":{"env":{"TRW_DISPATCH_CHILD":"1"}},"other":{"command":"y"}}}'),
        ("--mcp-config", '{"mcpServers":{}}'),
        ("--append-system-prompt", '{"mcpServers":{"trw":{"env":{"TRW_DISPATCH_CHILD":"1"}}}}'),
        ("--note", "TRW_DISPATCH_CHILD=1"),
        ("-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="0"'),
        ("-c", 'mcp_servers.other.env.TRW_DISPATCH_CHILD="1"'),
        ("-c", 'notes="mcp_servers.trw.env.TRW_DISPATCH_CHILD=\\"1\\""'),
        ('mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"',),
        # Ambiguous composition is refused, not resolved (lead's two counterexamples first).
        ("-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"', "-c", "mcp_servers.trw.env={}"),
        (
            "--mcp-config",
            '{"mcpServers":{"trw":{"command":"x","args":[],"env":{"TRW_DISPATCH_CHILD":"1"}}}}',
            "--mcp-config",
            '{"mcpServers":{"trw":{"command":"x","args":[]}}}',
        ),
        ("-c", "mcp_servers.trw.env={}", "-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"'),
        ("-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"', "-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"'),
        ("-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"', "-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD=""'),
        ("-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"', "-c", "mcp_servers.trw={}"),
        ("-c", 'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"', "-c", "mcp_servers = {}"),
    ],
)
def test_a_template_that_only_mentions_the_marker_is_not_marked(template: tuple[str, ...]) -> None:
    from trw_mcp.dispatch._child_marker import template_marks_child

    assert template_marks_child(template) is False


def test_every_shipped_with_trw_template_is_marked_and_no_other_client_offers_it() -> None:
    from trw_mcp.dispatch._child_marker import template_marks_child

    offering = {cid for cid, spec in CLIENT_SPECS.items() if spec.trw_access_argv_template}

    assert offering == {"claude", "codex"}
    for cid in offering:
        assert template_marks_child(CLIENT_SPECS[cid].trw_access_argv_template)


@pytest.mark.parametrize("client", ["claude", "codex"])
@pytest.mark.parametrize(
    "variant",
    [{}, {"read_only": False}, {"read_only": True}, {"isolate": False}, {"model": "m", "role": "reviewer"}],
)
def test_the_final_rendered_argv_is_still_unambiguously_marked(client: str, variant: dict[str, Any]) -> None:
    """Whatever posture/model/isolation args the builder adds, none undoes or duplicates the marker."""
    from trw_mcp.dispatch._child_marker import template_marks_child

    argv = build_command(DispatchRequest(client=client, prompt=_HOSTILE["prompt"], with_trw=True, **variant))  # type: ignore[arg-type]

    if client == "codex":
        _assert_fresh_codex_entry_marked(argv)
    else:
        assert template_marks_child(tuple(argv))


# ── Second layer: the in-process runner, for callers that are not trw_dispatch ──


def test_the_runner_itself_refuses_on_a_marked_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """``trw_review``'s cross-model path calls ``_runner.dispatch`` directly; it must not spawn either."""
    from trw_mcp.dispatch import _runner

    def _explode(*_a: object, **_k: object) -> Any:
        raise AssertionError("a marked server reached Popen")

    monkeypatch.setenv(CHILD_MARKER_ENV, "")
    monkeypatch.setattr(_runner.subprocess, "Popen", _explode)
    monkeypatch.setattr(_runner, "_confinement_for", _explode)

    result = _runner.dispatch(DispatchRequest(client="claude", prompt="hi", read_only=True))

    assert result.exit_code == -1
    assert "nested dispatch is refused" in result.raw_stderr
    assert result.trw_access_enforced is False
    assert result.posture_enforced is False


def test_an_unmarked_server_passes_the_callers_arguments_to_the_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._resolve import DispatchResolutionError

    seen: dict[str, Any] = {}

    def _record(**kwargs: Any) -> Any:
        seen.update(kwargs)
        raise DispatchResolutionError("stop here", exit_code=2)

    monkeypatch.delenv(CHILD_MARKER_ENV, raising=False)
    monkeypatch.setattr(dispatch_tool, "resolve_dispatch_request", _record)

    out = _tool()(prompt="hi", client="claude", with_trw=True, read_only=True, model="m")

    assert out["exit_code"] == 2
    assert (seen["client"], seen["with_trw"], seen["read_only"], seen["model"]) == ("claude", True, True, "m")
