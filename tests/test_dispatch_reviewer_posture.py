"""PRD-SEC-015-FR06/FR07 + OD-6 — the reviewer posture is argv, not a sentence.

What is under test is the CHANNEL: that ``posture="reviewer"`` renders TRW's own
MCP server into the child's command line (never sourced from the reviewed repo),
marks it ``TRW_SURFACE_ROLE=reviewer``, refuses every client that cannot carry
it, refuses writes, and reports ``posture_enforced`` from the registry rather
than from a hardcoded literal.

Every claim is asserted in BOTH directions — reviewer renders it, default does
not — because a one-directional assertion passes just as well against a builder
that emits the reviewer fragment unconditionally, which would be a security
regression the test was meant to catch.

The no-spawn claims use a SENTINEL binary that writes a file when it runs, plus
a positive control that proves the sentinel really does write. "No process was
started" asserted against a binary that could not have written anything either
way is not evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from itertools import product
from pathlib import Path
from typing import get_args

import pytest
import tomllib


@pytest.fixture(autouse=True)
def _stable_mcp_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("trw_mcp.dispatch._posture.uuid.uuid4", lambda: SimpleNamespace(hex="test"))


from tests._dispatch_host import unconfined_off_darwin
from trw_mcp.dispatch import _run_job
from trw_mcp.dispatch._client_spec_types import ClientSpec, ClientVerification
from trw_mcp.dispatch._client_specs import _SPEC_BY_ID, SUPPORTED_CLIENTS, client_spec_for
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._env import build_runner_env, build_subprocess_env
from trw_mcp.dispatch._posture import (
    ReviewerPostureError,
    mcp_server_launcher,
    render_reviewer_argv,
)
from trw_mcp.dispatch._resolve import DispatchResolutionError, resolve_dispatch_request
from trw_mcp.dispatch._roles import ROLE_TABLE, apply_role
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchPosture, DispatchRequest
from trw_mcp.models.surface_packs import REVIEWER_TOOLS, reviewer_tools_toml_array

# Tokens that would give a reviewer the ability to write or to approve its own
# tool use. Drawn from the registry's own write fragments plus the documented
# bypass flags, so this list cannot silently fall behind the specs.
_WRITE_ENABLING_TOKENS = (
    "workspace-write",
    "acceptEdits",
    "--permission-mode",
    "--dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox",
    "--allowedTools",
    "--allowed-tools",
    "--allow-all-tools",
    "--yolo",
    "--auto",
    "--full-auto",
)


class _Cfg:
    """Minimal dispatch config: every client enabled, read-only baseline."""

    dispatch_default_client = "codex"
    dispatch_role_client: dict[str, str] = {}
    dispatch_enabled_clients = ["claude", "codex", "agy", "opencode", "cursor-cli", "copilot", "grok"]
    dispatch_default_models: dict[str, str] = {}
    dispatch_default_timeout_s = 60
    dispatch_default_read_only = True


def _req(client: str, **kwargs: object) -> DispatchRequest:
    return DispatchRequest(client=client, prompt="audit this", **kwargs)  # type: ignore[arg-type]


def _codex_reviewer_argv() -> list[str]:
    return build_command(_req("codex", posture="reviewer"))


def _claude_reviewer_argv() -> list[str]:
    return build_command(_req("claude", posture="reviewer"))


def _mcp_config_payload(argv: list[str]) -> dict[str, object]:
    payload = argv[argv.index("--mcp-config") + 1]
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)
    return parsed


# ── every reviewer-tool name reaches the codex command line, exactly once each


def test_codex_reviewer_argv_carries_each_reviewer_tool_exactly_once() -> None:
    joined = " ".join(_codex_reviewer_argv())
    # Counted as QUOTED tokens, not substrings: ``trw_code`` is a prefix of
    # e.g. a hypothetical ``trw_code`` + ``_search`` name, so a substring
    # count would misreport an occurrence of a name outside the current,
    # smaller REVIEWER_TOOLS set.
    for name in REVIEWER_TOOLS:
        occurrences = joined.count(f'"{name}"')
        assert occurrences == 1, f'"{name}" appears {occurrences}x in the reviewer argv'
    # PRD-CORE-300 collapsed the four retired code/hint tools into the single
    # trw_code tool, slice S4 already dropped codebase-risk-report, S9 folded
    # the graph-related tool into trw_recall and S11b dropped the two meta
    # tools, so the reviewer set is now exactly 2.
    assert len(REVIEWER_TOOLS) == 2


def test_codex_reviewer_allowlist_is_the_rendered_ssot_and_parses_as_toml() -> None:
    argv = _codex_reviewer_argv()
    allowlist = [tok for tok in argv if tok.startswith("mcp_servers.trw_dispatch_test.enabled_tools=")]
    assert len(allowlist) == 1
    # The token is byte-identical to the SSOT rendering, so the dispatch layer
    # and scripts/print_reviewer_tools.py cannot bound a child differently.
    assert allowlist[0] == f"mcp_servers.trw_dispatch_test.enabled_tools={reviewer_tools_toml_array()}"
    parsed = tomllib.loads(allowlist[0].replace("mcp_servers.trw_dispatch_test.enabled_tools=", "enabled_tools = ", 1))
    assert parsed["enabled_tools"] == sorted(REVIEWER_TOOLS)


def test_codex_reviewer_argv_pre_approves_only_the_bounded_trw_server() -> None:
    """PRD-CORE-300-NFR02: under ``codex exec`` the approval policy is ``never``,
    so an MCP call that needs approval is refused ("MCP tool call requires
    approval, but approval policy is never"). The live S10 probe measured exactly
    that: the reviewer lane listed ``trw_code`` and could not call it. The fresh
    TRW server is already bounded to the read-only reviewer set, so its tools
    are pre-approved there, and only there: the disabled legacy ``trw`` entry
    gets no approval key."""
    argv = _codex_reviewer_argv()
    approvals = [tok for tok in argv if "default_tools_approval_mode" in tok]
    assert approvals == ['mcp_servers.trw_dispatch_test.default_tools_approval_mode="approve"']


def test_codex_reviewer_argv_supplies_the_mcp_transport_from_trw_not_the_repo() -> None:
    argv = _codex_reviewer_argv()
    command, args = mcp_server_launcher()
    assert f'mcp_servers.trw_dispatch_test.command="{command}"' in argv
    assert f"mcp_servers.trw_dispatch_test.args={json.dumps(list(args))}" in argv
    assert 'mcp_servers.trw_dispatch_test.env.TRW_SURFACE_ROLE="reviewer"' in argv
    # OD-6: the interpreter is the dispatching process's own, never a path the
    # reviewed repository could plant.
    assert Path(command).is_absolute()


def test_codex_reviewer_argv_keeps_the_read_only_sandbox_and_no_write_token() -> None:
    argv = _codex_reviewer_argv()
    assert argv.count("--sandbox") == 1
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    for token in _WRITE_ENABLING_TOKENS:
        assert token not in argv


def test_codex_reviewer_argv_now_carries_ignore_user_config_and_disables_apps() -> None:
    # FR-12 RESOLVED (PRD-SEC-015-FR10, 2026-09-24): L-VupD's conflict was measured
    # on a template with NO transport anywhere on the command line. This template
    # supplies mcp_servers.trw's full command/args via -c, which removes that
    # dependency (live-probed 2026-09-24, codex-cli 0.156.0) — so the reviewer argv
    # now carries --ignore-user-config (drops every user/project MCP server) and
    # --disable apps (drops ChatGPT connectors/plugins), appended via
    # reviewer_extra_argv AFTER the rendered -c transport tokens.
    argv = _codex_reviewer_argv()
    assert "--ignore-user-config" in argv
    assert argv[argv.index("--disable") + 1] == "apps"
    # The default posture (no TRW-supplied transport) still emits the bare flag
    # alone, unchanged from before this FR.
    assert build_command(_req("codex")) == [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--json",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "audit this",
    ]


def test_codex_reviewer_extra_argv_is_appended_after_the_mcp_transport_tokens() -> None:
    argv = _codex_reviewer_argv()
    # Every "-c ..." pair precedes the hardening flags: the transport is fully
    # rendered before anything else is appended, never interleaved with it.
    last_c_index = max(i for i, tok in enumerate(argv) if tok == "-c")
    assert argv.index("--ignore-user-config") > last_c_index + 1


def test_codex_default_posture_renders_none_of_the_reviewer_channel() -> None:
    argv = build_command(_req("codex"))
    joined = " ".join(argv)
    assert "-c" not in argv
    assert "mcp_servers" not in joined
    assert "TRW_SURFACE_ROLE" not in joined
    for name in REVIEWER_TOOLS:
        assert name not in joined


# ── claude: one layer (the server-side role), stated honestly ────────────────


def test_claude_reviewer_mcp_config_names_trws_own_server_and_the_role() -> None:
    argv = _claude_reviewer_argv()
    servers = _mcp_config_payload(argv)["mcpServers"]
    assert isinstance(servers, dict)
    entry = servers["trw"]
    command, args = mcp_server_launcher()
    assert entry == {"command": command, "args": list(args), "env": {"TRW_SURFACE_ROLE": "reviewer"}}
    assert argv.count("--strict-mcp-config") == 1
    assert argv.count("--mcp-config") == 1


def test_claude_reviewer_argv_has_no_write_or_tool_preauthorisation_token() -> None:
    argv = _claude_reviewer_argv()
    for token in _WRITE_ENABLING_TOKENS:
        assert token not in argv


def test_claude_reviewer_argv_restricts_the_built_in_tool_set_to_read_only() -> None:
    # PRD-SEC-015-FR10 (NFR02): a bare reviewer dispatch had no --allowedTools/
    # --disallowedTools at all, so shell/edit access depended on the user's own
    # settings.json. --tools REPLACES the built-in tool set (unlike the forbidden
    # --allowedTools, which pre-authorises rather than restricts).
    argv = _claude_reviewer_argv()
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob"
    for name in ("Bash", "Edit", "Write", "WebFetch"):
        assert name not in argv[argv.index("--tools") + 1].split(",")


def test_claude_default_posture_emits_no_tools_restriction() -> None:
    assert "--tools" not in build_command(_req("claude"))


def test_claude_default_posture_still_emits_the_empty_server_map() -> None:
    argv = build_command(_req("claude"))
    assert _mcp_config_payload(argv) == {"mcpServers": {}}
    assert "TRW_SURFACE_ROLE" not in " ".join(argv)


# ── env injection is posture-gated in both directions ───────────────────────


def test_subprocess_env_injects_the_role_only_under_reviewer_posture() -> None:
    src = {"PATH": "/usr/bin", "OPENAI_API_KEY": "k", "SECRET_TOKEN": "leak"}
    reviewer = build_subprocess_env("codex", src, posture="reviewer")
    default = build_subprocess_env("codex", src)
    assert reviewer["TRW_SURFACE_ROLE"] == "reviewer"
    assert "TRW_SURFACE_ROLE" not in default
    # The allowlist is unchanged by the overlay: no host secret rides in with it.
    assert "SECRET_TOKEN" not in reviewer
    assert reviewer["OPENAI_API_KEY"] == "k"


def test_inherited_surface_role_cannot_mark_a_default_posture_child() -> None:
    # A host that is ITSELF a reviewer must not silently mark an unbounded child,
    # and must not be able to un-mark one either: the variable is outside the
    # allowlist, so only the posture overlay can set it.
    src = {"PATH": "/usr/bin", "TRW_SURFACE_ROLE": "reviewer"}
    assert "TRW_SURFACE_ROLE" not in build_subprocess_env("codex", src)
    src_agent = {"PATH": "/usr/bin", "TRW_SURFACE_ROLE": "agent"}
    assert build_subprocess_env("codex", src_agent, posture="reviewer")["TRW_SURFACE_ROLE"] == "reviewer"


def test_runner_env_carries_the_role_and_the_import_passthrough() -> None:
    src = {"PATH": "/usr/bin", "PYTHONPATH": "/src", "VIRTUAL_ENV": "/venv"}
    env = build_runner_env("codex", src, posture="reviewer")
    assert env["TRW_SURFACE_ROLE"] == "reviewer"
    assert env["PYTHONPATH"] == "/src"
    assert "TRW_SURFACE_ROLE" not in build_runner_env("codex", src)


def test_agy_gets_no_reviewer_env_because_it_has_no_posture() -> None:
    assert "TRW_SURFACE_ROLE" not in build_subprocess_env("agy", {"PATH": "/usr/bin"}, posture="reviewer")


# ── refusals ────────────────────────────────────────────────────────────────


def test_request_construction_refuses_reviewer_plus_writes() -> None:
    with pytest.raises(ValueError, match="requires read_only=True"):
        _req("codex", posture="reviewer", read_only=False)


def test_resolution_refuses_reviewer_plus_writes_with_exit_code_two() -> None:
    with pytest.raises(DispatchResolutionError) as exc:
        resolve_dispatch_request(
            client="codex",
            prompt="p",
            role=None,
            model=None,
            cwd=None,
            timeout_s=None,
            read_only=False,
            isolate=True,
            use_pty=False,
            posture="reviewer",
            dispatch_cfg=_Cfg(),
        )
    assert exc.value.exit_code == 2
    assert "read_only" in str(exc.value) or "writes" in str(exc.value)


@pytest.mark.parametrize("client", ["agy", "opencode", "cursor-cli", "copilot"])
def test_resolution_refuses_a_client_that_cannot_carry_the_posture(client: str) -> None:
    with pytest.raises(DispatchResolutionError) as exc:
        resolve_dispatch_request(
            client=client,
            prompt="p",
            role=None,
            model=None,
            cwd=None,
            timeout_s=None,
            read_only=None,
            isolate=True,
            use_pty=False,
            posture="reviewer",
            dispatch_cfg=_Cfg(),
        )
    assert exc.value.exit_code == 2
    assert "reviewer posture" in str(exc.value)
    assert client in str(exc.value)


def test_resolution_refuses_an_unknown_posture_name() -> None:
    with pytest.raises(DispatchResolutionError, match="unknown dispatch posture"):
        resolve_dispatch_request(
            client="codex",
            prompt="p",
            role=None,
            model=None,
            cwd=None,
            timeout_s=None,
            read_only=None,
            isolate=True,
            use_pty=False,
            posture="auditor",
            dispatch_cfg=_Cfg(),
        )


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_resolution_accepts_the_supported_clients(client: str) -> None:
    req = resolve_dispatch_request(
        client=client,
        prompt="p",
        role="code-review",
        model=None,
        cwd=None,
        timeout_s=None,
        read_only=None,
        isolate=True,
        use_pty=False,
        posture="reviewer",
        dispatch_cfg=_Cfg(),
    )
    assert req.posture == "reviewer"
    assert req.read_only is True


def test_render_refuses_a_client_with_no_template() -> None:
    with pytest.raises(ReviewerPostureError, match="no reviewer posture"):
        render_reviewer_argv(client_spec_for("opencode"))


# ── registry invariants ─────────────────────────────────────────────────────


def _spec_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "client_id": "probe",
        "binary": "probe",
        "base_argv": ("probe",),
        "version_argv": ("--version",),
        "output_shape": "trailing_text",
        "sub_agents": "unknown",
        "sandbox": "none",
        "verification": ClientVerification(
            method="primary_source", evidence="test fixture", verified_at=__import__("datetime").date(2026, 9, 16)
        ),
    }
    base.update(overrides)
    return base


def test_a_spec_cannot_claim_a_reviewer_env_without_an_argv_channel() -> None:
    with pytest.raises(ValueError, match="reviewer_env is set but reviewer_argv_template is empty"):
        ClientSpec(**_spec_kwargs(reviewer_env={"TRW_SURFACE_ROLE": "reviewer"}))  # type: ignore[arg-type]


def test_a_spec_cannot_reference_an_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="unknown placeholder"):
        ClientSpec(**_spec_kwargs(reviewer_argv_template=("--config", "{mcp_secret}")))  # type: ignore[arg-type]


def test_a_json_payload_is_not_mistaken_for_a_placeholder() -> None:
    spec = ClientSpec(**_spec_kwargs(reviewer_argv_template=('{"mcpServers":{}}',)))  # type: ignore[arg-type]
    assert spec.supports_reviewer_posture is True


def _expected_outcome(client: str, posture: str) -> str:
    """The outcome a (client, posture) pair must have, read from spec fields only."""
    spec = client_spec_for(client)
    if posture == "reviewer":
        return "enforced" if spec.supports_reviewer_posture else "refused"
    if posture == "isolated-review":
        return "isolated" if spec.isolated_review is not None and spec.host_confinement else "refused"
    return "default"


@pytest.mark.parametrize(
    ("client", "role", "posture"),
    list(product(SUPPORTED_CLIENTS, ROLE_TABLE, get_args(DispatchPosture))),
)
def test_every_client_role_posture_triple(
    monkeypatch: pytest.MonkeyPatch, sentinel: tuple[Path, Path], client: str, role: str, posture: str
) -> None:
    """PRD-CORE-297-FR06: no hand-listed pairs; the registry decides every expectation."""
    from trw_mcp.dispatch import _posture

    monkeypatch.setattr(_posture, "confinement_prefix", lambda: ["sandbox-exec", "-p", "(version 1)"])
    monkeypatch.setattr(
        "trw_mcp.dispatch._host_confinement.confinement_prefix", lambda writable=None: ["env"]
    )  # admits; lets the sentinel write
    _use_sentinel(monkeypatch, sentinel[0], client)
    expected = _expected_outcome(client, posture)
    req = DispatchRequest(
        client=client,  # type: ignore[arg-type]
        prompt=apply_role(role, "review this"),
        timeout_s=60,
        read_only=True,
        posture=posture,  # type: ignore[arg-type]
    )
    result = dispatch(req)
    refused = "reviewer posture refused" in result.raw_stderr
    assert refused is (expected == "refused"), result.raw_stderr
    assert result.posture_enforced is (expected == "enforced")
    assert (result.isolation == "snapshot-write-confined") is (expected == "isolated")
    assert sentinel[1].exists() is (expected != "refused"), "a refusal never spawns; an admission always does"
    if expected != "refused":
        assert result.exit_code == 0, result.raw_stderr
    if expected == "refused":
        with pytest.raises(ReviewerPostureError):
            _posture.verify_reviewer_posture(client, posture, read_only=True)


# ── no process is started for a refused posture (sentinel binary) ───────────


@pytest.fixture
def sentinel(tmp_path: Path) -> tuple[Path, Path]:
    """A binary that WRITES a file when it runs, plus the path it writes."""
    marker = tmp_path / "sentinel-ran.txt"
    script = tmp_path / "sentinel.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text(' '.join(sys.argv[1:]), encoding='utf-8')\n"
        'print(\'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\')\n',
        encoding="utf-8",
    )
    return script, marker


def _use_sentinel(monkeypatch: pytest.MonkeyPatch, script: Path, client: str = "codex") -> None:
    spec = client_spec_for(client)
    patched = spec.model_copy(update={"binary": sys.executable, "base_argv": (sys.executable, str(script))})
    monkeypatch.setitem(_SPEC_BY_ID, client, patched)


def test_the_sentinel_really_writes_when_it_is_spawned(
    monkeypatch: pytest.MonkeyPatch, sentinel: tuple[Path, Path]
) -> None:
    """Positive control for the refusal test below."""
    script, marker = sentinel
    _use_sentinel(monkeypatch, script)
    result = dispatch(_req("codex", posture="reviewer", timeout_s=60))
    assert marker.exists()
    assert result.posture_enforced is True
    assert 'mcp_servers.trw_dispatch_test.env.TRW_SURFACE_ROLE="reviewer"' in marker.read_text(encoding="utf-8")


def test_reviewer_plus_writes_is_refused_before_any_process_starts(
    monkeypatch: pytest.MonkeyPatch, sentinel: tuple[Path, Path]
) -> None:
    script, marker = sentinel
    _use_sentinel(monkeypatch, script)
    # model_construct bypasses the request validator on purpose: it simulates a
    # request rebuilt by some other path, and proves the RUNNER refuses too.
    bad = DispatchRequest.model_construct(
        client="codex",
        prompt="p",
        model=None,
        cwd=None,
        timeout_s=60,
        read_only=False,
        isolate=True,
        use_pty=False,
        extra_args=[],
        posture="reviewer",
    )
    result = dispatch(bad)
    assert not marker.exists(), "a refused reviewer dispatch spawned the child anyway"
    assert result.exit_code == -1
    assert "reviewer posture refused" in result.raw_stderr
    assert result.posture_enforced is False
    assert result.argv_redacted == []


def test_an_unsupported_client_is_refused_before_any_process_starts(
    monkeypatch: pytest.MonkeyPatch, sentinel: tuple[Path, Path]
) -> None:
    script, marker = sentinel
    _use_sentinel(monkeypatch, script, client="agy")
    unconfined_off_darwin(monkeypatch)
    result = dispatch(_req("agy", posture="reviewer", timeout_s=60))
    assert not marker.exists()
    assert result.exit_code == -1
    assert result.posture_enforced is False


def test_default_posture_result_reports_no_enforcement(
    monkeypatch: pytest.MonkeyPatch, sentinel: tuple[Path, Path]
) -> None:
    script, marker = sentinel
    _use_sentinel(monkeypatch, script)
    result = dispatch(_req("codex", timeout_s=60))
    assert marker.exists()
    assert result.posture_enforced is False
    assert result.posture == "default"
    assert "TRW_SURFACE_ROLE" not in marker.read_text(encoding="utf-8")


# ── the background job path reports the same truth ──────────────────────────


def _run_job_result(tmp_path: Path, req: DispatchRequest, name: str) -> dict[str, object]:
    req_path = tmp_path / f"{name}-req.json"
    result_path = tmp_path / f"{name}-result.json"
    req_path.write_text(req.model_dump_json(), encoding="utf-8")
    assert _run_job.main([str(req_path), str(result_path), str(tmp_path / f"{name}-pid.json")]) == 0
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize(("posture", "enforced"), [("reviewer", True), ("default", False)])
def test_run_job_result_reports_posture_enforced_from_the_spec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sentinel: tuple[Path, Path],
    posture: str,
    enforced: bool,
) -> None:
    script, marker = sentinel
    _use_sentinel(monkeypatch, script)
    payload = _run_job_result(tmp_path, _req("codex", posture=posture, timeout_s=60), posture)
    assert marker.exists()
    assert payload["posture"] == posture
    assert payload["posture_enforced"] is enforced


def test_run_job_failure_result_never_claims_enforcement(tmp_path: Path) -> None:
    req_path = tmp_path / "bad-req.json"
    result_path = tmp_path / "bad-result.json"
    req_path.write_text("{not json", encoding="utf-8")
    assert _run_job.main([str(req_path), str(result_path), str(tmp_path / "pid.json")]) == 1
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["posture_enforced"] is False


def test_background_job_env_carries_the_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """``start_background`` builds the intermediate env from the request posture."""
    recorded: dict[str, dict[str, str]] = {}
    real_popen = subprocess.Popen

    class _Popen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            recorded["env"] = dict(kwargs.get("env") or {})  # type: ignore[arg-type]
            self._proc = real_popen([sys.executable, "-c", "pass"], env={"PATH": os.environ.get("PATH", "")})
            self.pid = self._proc.pid

        def wait(self) -> int:
            return self._proc.wait()

    def _only_the_job_launch(argv: list[str], **kwargs: object) -> object:
        """Route ONLY the ``python -m trw_mcp.dispatch._run_job`` launch to the double.

        The patch lands on the shared ``subprocess`` module, so every other
        Popen in the process (the Darwin process-identity capture runs ``ps``
        after the launch) would otherwise overwrite ``recorded["env"]`` with an
        env that never carries the role.
        """
        if "trw_mcp.dispatch._run_job" in argv:
            return _Popen(argv, **kwargs)
        return real_popen(argv, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("trw_mcp.dispatch._jobs.subprocess.Popen", _only_the_job_launch)
    from trw_mcp.dispatch._jobs import start_background

    start_background(_req("codex", posture="reviewer", timeout_s=60), trw_dir=None)
    assert recorded["env"]["TRW_SURFACE_ROLE"] == "reviewer"


# ── FR13: a reviewer-role SERVER will not spawn a writable grandchild ────────


class _FakeJob:
    """Stand-in for the started background job (only its fields are read back)."""

    job_id = "job-1"
    status = "running"
    client = "codex"
    argv_redacted: list[str] = []
    result_path = ""


def _dispatch_tool() -> Any:
    from fastmcp import FastMCP

    from tests.conftest import extract_tool_fn
    from trw_mcp.tools.dispatch import register_dispatch_tools

    server = FastMCP("test")
    register_dispatch_tools(server)
    return extract_tool_fn(server, "trw_dispatch")


def test_reviewer_role_server_refuses_allow_writes_without_launching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")

    def _never(*_a: object, **_k: object) -> None:
        pytest.fail("a reviewer-role server started a writable dispatch")

    monkeypatch.setattr("trw_mcp.tools.dispatch.start_background", _never)
    payload = _dispatch_tool()(prompt="p", client="codex", allow_writes=True)
    assert payload["exit_code"] == 2
    assert "reviewer" in str(payload["error"])


def test_agent_role_server_still_accepts_allow_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The discriminator: the refusal is keyed on the ROLE, not on allow_writes."""
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    started: list[DispatchRequest] = []

    def _record(req: DispatchRequest) -> _FakeJob:
        started.append(req)
        return _FakeJob()

    monkeypatch.setattr("trw_mcp.tools.dispatch.start_background", _record)
    payload = _dispatch_tool()(prompt="p", client="codex", allow_writes=True)
    assert payload.get("error") is None
    assert len(started) == 1
    assert started[0].read_only is False
