"""Real local subprocess regressions; no model calls or user-config mutations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from trw_mcp.dispatch._client_specs import client_spec_for
from trw_mcp.dispatch._env import build_subprocess_env
from trw_mcp.dispatch._posture import mcp_server_launcher, render_reviewer_argv, render_trw_access_argv


def test_reviewed_cwd_cannot_shadow_mcp_package(tmp_path: Path) -> None:
    package = tmp_path / "trw_mcp"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "server.py").write_text("print('SHADOW_PACKAGE_EXECUTED')")
    command, args = mcp_server_launcher()
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(tmp_path)}
    # The original launcher actually executes the adversarial package.
    vulnerable = subprocess.run(
        [command, "-m", "trw_mcp.server"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30, check=True
    )
    assert "SHADOW_PACKAGE_EXECUTED" in vulnerable.stdout
    isolated = subprocess.run(
        [command, *args, "--help"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30
    )
    assert isolated.returncode == 0, isolated.stderr
    assert "SHADOW_PACKAGE_EXECUTED" not in isolated.stdout
    assert "usage" in isolated.stdout.lower()


@pytest.mark.parametrize("reviewer", [False, True])
def test_codex_effective_transport_has_no_legacy_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reviewer: bool,
) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI needed for actual config-merge regression (no model call)")
    home = tmp_path / "codex-home"
    home.mkdir()
    parent = tmp_path / "parent-project"
    child = tmp_path / "different-child-project"
    parent.mkdir()
    child.mkdir()
    (home / "config.toml").write_text("""[mcp_servers.trw]
command="untrusted"
enabled=false
cwd="/untrusted"
enabled_tools=["wrong"]
disabled_tools=["trw_recall"]
[mcp_servers.trw.env]
TRW_PROJECT_ROOT="/wrong-project"
TRW_SURFACE_ROLE="reviewer"
""")
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(parent))
    spec = client_spec_for("codex")
    full_argv = render_reviewer_argv(spec) if reviewer else render_trw_access_argv(spec)
    # PRD-SEC-015-FR10: under posture='reviewer' the rendered argv now carries
    # --ignore-user-config/--disable apps AFTER the -c transport pairs
    # (reviewer_extra_argv) — flags `codex exec` accepts but the `codex mcp`
    # introspection subcommand this test uses does not ("unexpected argument").
    # This test probes the -c TRANSPORT alone, so only the leading "-c value"
    # pairs are kept; the host-tool-surface hardening is exercised separately
    # by tests/test_dispatch_reviewer_posture.py against the real `exec` argv.
    argv: list[str] = []
    it = iter(full_argv)
    for token in it:
        if token != "-c":
            break
        argv.append(token)
        argv.append(next(it))
    env = build_subprocess_env("codex", with_trw=not reviewer)
    env["CODEX_HOME"] = str(home)
    result = subprocess.run(
        [codex, "mcp", "list", "--json", *argv],
        cwd=child,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    servers = json.loads(result.stdout)
    legacy = next(server for server in servers if server["name"] == "trw")
    fresh = next(server for server in servers if server["name"].startswith("trw_dispatch_"))
    assert legacy["enabled"] is False
    assert fresh["enabled"] is True
    transport = fresh["transport"]
    assert transport["cwd"] is None
    assert transport["args"] == ["-I", "-m", "trw_mcp.server"]
    assert transport["command"] == mcp_server_launcher()[0]
    effective = subprocess.run(
        [codex, "mcp", "get", fresh["name"], "--json", *argv],
        cwd=child,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    entry = json.loads(effective.stdout)
    assert not entry.get("disabled_tools")
    if reviewer:
        assert transport["env"]["TRW_SURFACE_ROLE"] == "reviewer"
        assert "trw_recall" in entry["enabled_tools"]
    else:
        assert not entry.get("enabled_tools")
        assert transport["env"] == {"TRW_DISPATCH_CHILD": "1"}
        assert transport["env_vars"] == ["TRW_PROJECT_ROOT"]
        # Exercise the second-hop filtered env from the actual parsed Codex config,
        # in a different cwd, not merely the CLI's first-hop environment.
        mcp_env = {key: env[key] for key in transport["env_vars"]}
        mcp_env.update(transport["env"])
        resolved = subprocess.run(
            [
                transport["command"],
                "-I",
                "-c",
                "from trw_mcp.state._paths import resolve_project_root; print(resolve_project_root())",
            ],
            cwd=child,
            env=mcp_env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert Path(resolved.stdout.strip()) == parent


def test_codex_fresh_transport_is_not_predictable_or_reused() -> None:
    spec = client_spec_for("codex")
    assert render_trw_access_argv(spec) != render_trw_access_argv(spec)


@pytest.mark.parametrize("reviewer", [False, True])
def test_codex_custom_template_cannot_bypass_controlled_binding(reviewer: bool) -> None:
    from trw_mcp.dispatch._posture import ReviewerPostureError, TrwAccessError

    spec = client_spec_for("codex")
    field = "reviewer_argv_template" if reviewer else "trw_access_argv_template"
    poisoned = spec.model_copy(update={field: (*getattr(spec, field), "-c", "mcp_servers.other.command=untrusted")})
    render = render_reviewer_argv if reviewer else render_trw_access_argv
    with pytest.raises(ReviewerPostureError if reviewer else TrwAccessError, match="controlled transport"):
        render(poisoned)


def test_codex_incompatible_legacy_http_transport_fails_closed(tmp_path: Path) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("Codex CLI needed for actual config-merge regression (no model call)")
    (tmp_path / "config.toml").write_text('[mcp_servers.trw]\nurl="https://example.invalid/mcp"\n')
    result = subprocess.run(
        [codex, "mcp", "list", "--json", *render_trw_access_argv(client_spec_for("codex"))],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "CODEX_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "url is not supported for stdio" in result.stderr


def test_fresh_transport_rendering_follows_capability_not_client_name() -> None:
    spec = client_spec_for("codex").model_copy(update={"client_id": "test-transport"})
    argv = render_trw_access_argv(spec)
    assert any(token.startswith("mcp_servers.trw_dispatch_") for token in argv)
    assert "mcp_servers.trw.enabled=false" in argv
