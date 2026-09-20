"""PRD-CORE-281-FR02 — a dispatched child may be given TRW's own MCP server.

The reported defect (2026-09-17): agents launched by ``trw-mcp dispatch --client
codex`` reported that ``trw_session_start`` and every other ``trw_*`` tool was
unavailable inside the child. That was deliberate isolation, not a fault —
``isolation_argv`` strips MCP for exactly the clients that expose a flag for it
(claude gets ``--mcp-config '{"mcpServers":{}}'``, codex ``--ignore-user-config``)
— but there was no way to ask for the one exception the operator wants: TRW's
own server, and nothing else of the host's.

``with_trw`` is that opt-in, built on the mechanism ``posture='reviewer'``
already proved: TRW renders its server into the child's ARGV, so the transport
comes from the dispatching process rather than from the repository being worked
on. The difference is what is NOT rendered — no ``TRW_SURFACE_ROLE`` marking and
no tool allowlist, so the child gets an ordinary session.

What these tests hold down:
  * default OFF — every existing dispatch keeps its byte-identical argv/env;
  * per client, whether injection is possible, and a REFUSAL (never a silent
    drop) where it is not;
  * the two exclusions (reviewer posture; a reviewer-role parent);
  * the reported field is derived from the argv that was actually built.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _stable_mcp_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("trw_mcp.dispatch._posture.uuid.uuid4", lambda: SimpleNamespace(hex="test"))


from trw_mcp.dispatch._client_specs import CLIENT_SPECS, client_spec_for
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._env import build_runner_env, build_subprocess_env
from trw_mcp.dispatch._posture import (
    TrwAccessError,
    mcp_server_launcher,
    trw_access_enforced,
    verify_trw_access,
)
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._types import DispatchRequest

#: Per-client verdict, asserted rather than described. ``True`` means the
#: registry entry carries an argv channel TRW can render its server into;
#: ``False`` means a with_trw dispatch to it is REFUSED.
_INJECTABLE: dict[str, bool] = {
    "claude": True,  # --mcp-config JSON payload, under --strict-mcp-config
    "codex": True,  # -c mcp_servers.trw_dispatch_test.command/args dotted overrides
    "agy": False,  # no host-config/MCP flag in this version
    "opencode": False,  # config comes from --dir / OPENCODE_CONFIG, not argv
    "cursor-cli": False,
    "copilot": False,
    "grok": False,  # no trw_access_argv_template (GROK_CONFIG drops mcp_servers)
}


class _Cfg:
    """The attributes ``resolve_dispatch_request`` reads off ``config.dispatch``."""

    def __init__(self, *, child_trw_access: bool = False, default_client: str = "codex") -> None:
        self.dispatch_enabled_clients = list(CLIENT_SPECS)
        self.dispatch_default_client = default_client
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s = 600
        self.dispatch_default_read_only = True
        self.dispatch_role_client: dict[str, str] = {}
        self.dispatch_child_trw_access = child_trw_access


def _resolve(**kwargs: object) -> DispatchRequest:
    base: dict[str, object] = {
        "client": "claude",
        "prompt": "audit this",
        "role": None,
        "model": None,
        "cwd": None,
        "timeout_s": None,
        "isolate": True,
        "use_pty": False,
        "dispatch_cfg": _Cfg(),
    }
    base.update(kwargs)
    return resolve_dispatch_request(**base)  # type: ignore[arg-type]


# ── The registry states the per-client answer ────────────────────────────


def test_every_registered_client_declares_its_injectability() -> None:
    """A new client must decide this consciously, not inherit a default."""
    assert set(_INJECTABLE) == set(CLIENT_SPECS)
    for client, expected in _INJECTABLE.items():
        assert client_spec_for(client).supports_trw_access is expected, client


def test_a_template_may_not_smuggle_the_reviewer_allowlist() -> None:
    """with_trw means an ORDINARY session; a reviewer-bounded one would make
    ``trw_access_enforced=True`` describe a surface the caller did not ask for."""
    from pydantic import ValidationError

    from trw_mcp.dispatch._client_specs import ClientSpec

    payload = client_spec_for("codex").model_dump()
    payload["trw_access_argv_template"] = ("-c", "mcp_servers.trw.enabled_tools={reviewer_tools}")
    with pytest.raises(ValidationError, match="reviewer surface"):
        ClientSpec.model_validate(payload)


# ── Default OFF: nothing changes for an existing dispatch ────────────────


@pytest.mark.parametrize("client", sorted(_INJECTABLE))
def test_default_dispatch_is_byte_identical_and_reports_no_access(client: str) -> None:
    req = DispatchRequest(client=client, prompt="hi")  # type: ignore[arg-type]
    assert req.with_trw is False
    argv = build_command(req)
    assert list(client_spec_for(client).isolation_argv) == [
        tok for tok in client_spec_for(client).isolation_argv if tok in argv
    ]
    assert trw_access_enforced(client, req.with_trw) is False
    assert "mcp_servers.trw_dispatch_test.command" not in " ".join(argv)


def test_the_claude_default_still_strips_every_mcp_server() -> None:
    """The isolation contract this feature is the single exception to."""
    argv = build_command(DispatchRequest(client="claude", prompt="hi"))
    assert '{"mcpServers":{}}' in argv


# ── Injection, where the client can carry it ─────────────────────────────


def test_claude_gets_only_trws_server_and_keeps_its_other_isolation() -> None:
    argv = build_command(DispatchRequest(client="claude", prompt="hi", with_trw=True))
    payload = json.loads(argv[argv.index("--mcp-config") + 1])
    command, args = mcp_server_launcher()

    assert set(payload["mcpServers"]) == {"trw"}
    assert payload["mcpServers"]["trw"]["command"] == command
    assert payload["mcpServers"]["trw"]["args"] == list(args)
    # No ROLE marking: this is a peer, not a bounded reviewer. The only env
    # key is the nested-launch marker (tests/test_dispatch_nested_launch_guard.py).
    assert payload["mcpServers"]["trw"]["env"] == {"TRW_DISPATCH_CHILD": "1"}
    # Host isolation that has nothing to do with MCP is UNCHANGED.
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert "--strict-mcp-config" in argv
    # The empty-map isolation fragment must not also be emitted.
    assert '{"mcpServers":{}}' not in argv


def test_codex_gets_the_dotted_overrides_and_no_tool_allowlist() -> None:
    argv = build_command(DispatchRequest(client="codex", prompt="hi", with_trw=True))
    command, args = mcp_server_launcher()
    joined = " ".join(argv)

    assert f'mcp_servers.trw_dispatch_test.command="{command}"' in argv
    assert f"mcp_servers.trw_dispatch_test.args={json.dumps(list(args))}" in argv
    assert "enabled_tools" not in joined
    assert "TRW_SURFACE_ROLE" not in joined
    # Measured conflict (L-VupD): --ignore-user-config beside the -c overrides
    # made codex reject the transport, so the builder emits one or the other.
    assert "--ignore-user-config" not in argv
    # The read-only posture is untouched by the MCP decision.
    assert argv[argv.index("--sandbox") + 1] == "read-only"


def test_the_server_command_is_the_dispatching_interpreter() -> None:
    """A PATH lookup inside the worked-on repo could resolve to that repo's own
    venv — the child must source nothing from it."""
    command, args = mcp_server_launcher()
    assert command.endswith("python") or "python" in command
    assert args == ("-I", "-m", "trw_mcp.server")


# ── Refusal, where it cannot ─────────────────────────────────────────────


@pytest.mark.parametrize("client", [c for c, ok in _INJECTABLE.items() if not ok])
def test_an_unsupported_client_is_refused_not_silently_downgraded(client: str) -> None:
    with pytest.raises(TrwAccessError, match="no TRW-access argv channel"):
        verify_trw_access(client, True)
    with pytest.raises(TrwAccessError):
        build_command(DispatchRequest(client=client, prompt="hi", with_trw=True))  # type: ignore[arg-type]


def test_an_explicit_request_for_an_unsupported_client_exits_two() -> None:
    with pytest.raises(DispatchResolutionError) as err:
        _resolve(client="agy", with_trw=True)
    assert err.value.exit_code == 2


def test_the_runner_refuses_a_request_built_by_another_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_construct skips validators; the pre-spawn guard must still stop it
    reaching Popen with the flag quietly dropped."""
    from trw_mcp.dispatch import _runner

    req = DispatchRequest.model_construct(
        client="agy",
        prompt="hi",
        model=None,
        cwd=None,
        timeout_s=60,
        read_only=True,
        isolate=True,
        posture="default",
        with_trw=True,
        use_pty=False,
        extra_args=(),
        verify_sandbox=False,
    )

    def _explode(*_a: object, **_k: object) -> None:
        raise AssertionError("a child was spawned for a refused request")

    monkeypatch.setattr(_runner.subprocess, "Popen", _explode)
    result = _runner.dispatch(req)

    assert result.exit_code == -1
    assert "trw access refused" in result.raw_stderr
    assert result.trw_access_enforced is False
    assert result.ok is False


# ── Config default: a baseline, never an override of an explicit caller ──


def test_the_config_default_turns_it_on_for_a_capable_client() -> None:
    req = _resolve(client="claude", dispatch_cfg=_Cfg(child_trw_access=True))
    assert req.with_trw is True


def test_an_explicit_false_beats_the_config_default() -> None:
    req = _resolve(client="claude", with_trw=False, dispatch_cfg=_Cfg(child_trw_access=True))
    assert req.with_trw is False


def test_the_config_default_degrades_rather_than_failing_an_unsupported_client() -> None:
    """A project-wide default must not break dispatching to agy at all; the
    result's ``trw_access_enforced=False`` is what states the truth."""
    req = _resolve(client="agy", dispatch_cfg=_Cfg(child_trw_access=True))
    assert req.with_trw is False
    assert trw_access_enforced("agy", req.with_trw) is False


# ── Exclusions ───────────────────────────────────────────────────────────


def test_with_trw_and_reviewer_posture_cannot_coexist() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="reviewer"):
        DispatchRequest(client="claude", prompt="hi", with_trw=True, posture="reviewer")
    with pytest.raises(DispatchResolutionError) as err:
        _resolve(client="claude", with_trw=True, posture="reviewer")
    assert err.value.exit_code == 2


def test_the_config_default_does_not_fight_a_chosen_reviewer_posture() -> None:
    req = _resolve(client="claude", posture="reviewer", dispatch_cfg=_Cfg(child_trw_access=True))
    assert req.with_trw is False
    assert req.posture == "reviewer"


def test_a_reviewer_role_parent_may_not_hand_a_child_an_unbounded_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastmcp import FastMCP

    from tests.conftest import extract_tool_fn
    from trw_mcp.tools.dispatch import register_dispatch_tools

    monkeypatch.setattr("trw_mcp.state._surface_role.reviewer_role_active", lambda: True)
    server = FastMCP("test")
    register_dispatch_tools(server)
    fn = extract_tool_fn(server, "trw_dispatch")
    out = fn(prompt="hi", client="claude", with_trw=True)

    assert out["exit_code"] == 2
    assert "with_trw is refused" in str(out["error"])


# ── Environment ──────────────────────────────────────────────────────────


def test_the_child_is_pointed_at_this_project_only_when_it_asked() -> None:
    src = {"PATH": "/usr/bin", "HOME": "/h", "AWS_SECRET_ACCESS_KEY": "leak"}
    plain = build_subprocess_env("claude", src)
    connected = build_subprocess_env("claude", src, with_trw=True)

    assert "TRW_PROJECT_ROOT" not in plain
    assert connected["TRW_PROJECT_ROOT"]
    # The allowlist is unchanged in both directions.
    assert "AWS_SECRET_ACCESS_KEY" not in connected


def test_a_host_project_root_cannot_reach_a_child_that_did_not_ask() -> None:
    src = {"PATH": "/usr/bin", "TRW_PROJECT_ROOT": "/somewhere/else"}
    assert "TRW_PROJECT_ROOT" not in build_subprocess_env("codex", src)


def test_the_background_launch_path_carries_the_same_pointer() -> None:
    """The two launch paths must not produce different child environments."""
    src = {"PATH": "/usr/bin", "HOME": "/h"}
    direct = build_subprocess_env("claude", src, with_trw=True)
    runner = build_runner_env("claude", src, with_trw=True)
    assert runner["TRW_PROJECT_ROOT"] == direct["TRW_PROJECT_ROOT"]


# ── Wiring: the operator's two entry points actually reach the resolver ──


def test_the_operator_config_field_reaches_the_view_model_the_tools_read() -> None:
    """``get_config().dispatch`` is what BOTH launch paths pass as ``dispatch_cfg``.

    ``_sub_config`` copies by EXACT name, so a missing mirror field on
    ``DispatchConfig`` would leave the resolver reading a hardcoded default while
    ``.trw/config.yaml`` said otherwise — the operator's setting silently lost.
    Every other test here injects a stub cfg and so cannot see that.
    """
    from trw_mcp.models.config import TRWConfig

    assert TRWConfig().dispatch.dispatch_child_trw_access is False
    cfg = TRWConfig(dispatch_child_trw_access=True).dispatch
    assert cfg.dispatch_child_trw_access is True
    assert _resolve(client="claude", dispatch_cfg=cfg).with_trw is True


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], None),
        (["--with-trw"], True),
        (["--no-with-trw"], False),
    ],
)
def test_the_cli_flags_carry_all_three_states(argv: list[str], expected: bool | None) -> None:
    """None is not "off": it is what defers to ``dispatch_child_trw_access``.

    Both flags share one dest, and argparse seeds a shared dest from the action
    it reaches first — with ``store_false``'s own default being True, an absent
    flag could parse as a value the caller never passed.
    """
    from trw_mcp.server._cli_argparse import _build_arg_parser

    args = _build_arg_parser().parse_args(["dispatch", "--prompt", "hi", *argv])
    assert args.with_trw is expected


def test_the_background_job_request_file_round_trips_the_flag() -> None:
    """The detached runner re-parses the request from JSON; a flag lost there
    would launch an isolated child for a caller who was told it was connected."""
    req = DispatchRequest(client="claude", prompt="hi", with_trw=True)
    assert DispatchRequest.model_validate_json(req.model_dump_json()).with_trw is True


# ── What with_trw stops isolating is reported, not just commented ────────


def test_codex_reports_the_isolation_it_gave_up_and_claude_reports_none() -> None:
    """claude's template re-emits its whole isolation fragment bar the empty
    server map; codex's drops --ignore-user-config outright. Only the second is
    a loss, so only the second declares a residue."""
    assert client_spec_for("claude").trw_access_config_residue == ""
    residue = client_spec_for("codex").trw_access_config_residue
    assert "--ignore-user-config" in residue
    assert "not full config isolation" in residue


def test_a_residue_cannot_be_declared_by_a_client_that_can_never_launch_one() -> None:
    from pydantic import ValidationError

    from trw_mcp.dispatch._client_specs import ClientSpec

    payload = client_spec_for("agy").model_dump()
    payload["trw_access_config_residue"] = "reads everything"
    with pytest.raises(ValidationError, match="contradict"):
        ClientSpec.model_validate(payload)


def test_the_residue_is_logged_on_an_enforced_launch_and_only_then(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A residue nobody can read at runtime is a residue nobody weighs."""
    import structlog

    from trw_mcp.dispatch import _runner

    def _events(client: str, *, with_trw: bool) -> list[str]:
        req = DispatchRequest(client=client, prompt="hi", with_trw=with_trw)  # type: ignore[arg-type]
        with structlog.testing.capture_logs() as logs:
            _runner._warn_trw_access_config_residue(req)
        return [entry["event"] for entry in logs]

    assert _events("codex", with_trw=True) == ["dispatch_trw_access_config_residue"]
    assert _events("codex", with_trw=False) == []
    assert _events("claude", with_trw=True) == []
