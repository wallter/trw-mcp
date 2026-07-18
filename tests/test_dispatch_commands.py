"""Behavior tests for the dispatch argv builder (trw_mcp.dispatch._commands)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.dispatch import build_command
from trw_mcp.dispatch._commands import _CLIENT_SPECS, SUPPORTED_CLIENTS
from trw_mcp.dispatch._types import _FORBIDDEN_EXTRA_ARG_TOKENS, DispatchRequest


def _req(client: str, **kw: object) -> DispatchRequest:
    base: dict[str, object] = {"client": client, "prompt": "audit this"}
    base.update(kw)
    return DispatchRequest(**base)  # type: ignore[arg-type]


def test_supported_clients_match_specs() -> None:
    assert set(SUPPORTED_CLIENTS) == set(_CLIENT_SPECS)


def test_claude_isolated_readonly_default_argv() -> None:
    argv = build_command(_req("claude"))
    # Isolation keeps user auth (--setting-sources user) and disables the host
    # MCP so the child can't recurse into the trw MCP. --bare is NOT used (it
    # drops user login). Verified live 2026-06-21.
    assert argv == [
        "claude",
        "--output-format",
        "json",
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "-p",
        "audit this",
    ]


def test_claude_no_isolate_drops_isolation_flags() -> None:
    argv = build_command(_req("claude", isolate=False))
    assert "--bare" not in argv
    assert "--strict-mcp-config" not in argv
    assert "--setting-sources" not in argv
    # prompt still passed via -p as a single token
    assert argv[-2:] == ["-p", "audit this"]


def test_claude_model_flag() -> None:
    argv = build_command(_req("claude", model="opus"))
    assert "--model" in argv and argv[argv.index("--model") + 1] == "opus"


def test_codex_isolation_and_readonly_flags() -> None:
    argv = build_command(_req("codex"))
    assert argv[:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in argv
    assert "--ignore-user-config" in argv
    # read-only is the default
    idx = argv.index("--sandbox")
    assert argv[idx + 1] == "read-only"
    # prompt is the trailing positional token
    assert argv[-1] == "audit this"


def test_codex_allow_writes_switches_to_workspace_write_sandbox() -> None:
    argv = build_command(_req("codex", read_only=False))
    # --allow-writes must ACTUALLY enable writes, not silently drop the sandbox.
    idx = argv.index("--sandbox")
    assert argv[idx + 1] == "workspace-write"


def test_codex_no_isolate_drops_ignore_user_config() -> None:
    argv = build_command(_req("codex", isolate=False))
    assert "--ignore-user-config" not in argv
    # but skip-git-repo-check is always present
    assert "--skip-git-repo-check" in argv


def test_agy_prompt_via_p_flag() -> None:
    # read-only default adds --sandbox; prompt is still the trailing -p token.
    argv = build_command(_req("agy"))
    assert argv == ["agy", "--sandbox", "-p", "audit this"]


def test_agy_does_not_get_pty_wrapping_in_command_builder() -> None:
    # PTY wrapping is the runner's job, not build_command's.
    argv = build_command(_req("agy", use_pty=True))
    assert argv[0] == "agy"
    assert "script" not in argv


def test_opencode_format_and_dir() -> None:
    argv = build_command(_req("opencode", cwd=Path("/tmp/proj")))
    assert argv[:2] == ["opencode", "run"]
    assert "--format" in argv and argv[argv.index("--format") + 1] == "json"
    assert "--dir" in argv and argv[argv.index("--dir") + 1] == "/tmp/proj"
    assert argv[-1] == "audit this"


def test_opencode_no_dir_when_cwd_absent() -> None:
    argv = build_command(_req("opencode"))
    assert "--dir" not in argv


def test_extra_args_appended_before_prompt() -> None:
    argv = build_command(_req("agy", extra_args=["--foo", "bar"]))
    # extra_args land after the read-only --sandbox, before the -p prompt.
    assert argv == ["agy", "--sandbox", "--foo", "bar", "-p", "audit this"]


def test_prompt_is_a_single_token_never_split() -> None:
    nasty = "rm -rf / ; echo $(whoami) && cat /etc/passwd"
    argv = build_command(_req("codex", prompt=nasty))
    # the entire dangerous string survives as exactly one argv element
    assert argv.count(nasty) == 1
    assert nasty in argv


def test_gemini_is_not_a_supported_client() -> None:
    assert "gemini" not in SUPPORTED_CLIENTS
    with pytest.raises(Exception):
        DispatchRequest(client="gemini", prompt="x")  # type: ignore[arg-type]


# --- read_only enforcement matrix (P1-1) --------------------------------------


def test_claude_read_only_default_adds_no_write_flag() -> None:
    # claude -p denies writes by default; read-only adds nothing extra.
    argv = build_command(_req("claude"))
    assert "--permission-mode" not in argv


def test_claude_allow_writes_adds_accept_edits() -> None:
    argv = build_command(_req("claude", read_only=False))
    idx = argv.index("--permission-mode")
    assert argv[idx + 1] == "acceptEdits"


def test_codex_read_only_uses_read_only_sandbox() -> None:
    argv = build_command(_req("codex"))
    idx = argv.index("--sandbox")
    assert argv[idx + 1] == "read-only"


def test_agy_read_only_adds_sandbox_writes_adds_skip_permissions() -> None:
    ro = build_command(_req("agy"))
    assert "--sandbox" in ro
    assert "--dangerously-skip-permissions" not in ro
    rw = build_command(_req("agy", read_only=False))
    assert "--dangerously-skip-permissions" in rw
    assert "--sandbox" not in rw


def test_opencode_read_only_adds_nothing_writes_adds_skip_permissions() -> None:
    ro = build_command(_req("opencode"))
    assert "--dangerously-skip-permissions" not in ro
    rw = build_command(_req("opencode", read_only=False))
    assert "--dangerously-skip-permissions" in rw


@pytest.mark.parametrize("client", list(SUPPORTED_CLIENTS))
def test_read_only_true_never_emits_a_write_bypass_flag(client: str) -> None:
    # The core invariant: no write/permission-bypass flag for ANY client when
    # read_only is True.
    argv = build_command(_req(client))
    bypass = {"--dangerously-skip-permissions", "acceptEdits", "workspace-write"}
    assert not (bypass & set(argv))


# --- extra_args security validator (P1-2) -------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "--sandbox",
        "--dangerously-skip-permissions",
        "--no-isolate",
        "--bare",
        "--ignore-user-config",
        "--mcp-config",
        "--strict-mcp-config",
        "--setting-sources",
        "--permission-mode",
        "--permission-prompt-tool",
        "--yes",
        "--yes-always",
    ],
)
def test_extra_args_rejects_security_override_token(token: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _req("codex", extra_args=[token])
    assert "security flag" in str(exc.value)


def test_extra_args_rejects_flag_value_form() -> None:
    # --sandbox=workspace-write must also be blocked (flag portion is checked).
    with pytest.raises(ValidationError):
        _req("codex", extra_args=["--sandbox=workspace-write"])


def test_extra_args_benign_token_passes_into_argv() -> None:
    argv = build_command(_req("codex", extra_args=["--verbose"]))
    assert "--verbose" in argv


# --- model validator (P2-4) ---------------------------------------------------


@pytest.mark.parametrize("model", ["opus", "gpt-4o", "claude-3.5", "anthropic/claude:1", "a_b.c@d"])
def test_model_validator_accepts_benign_names(model: str) -> None:
    argv = build_command(_req("claude", model=model))
    assert model in argv


@pytest.mark.parametrize("model", ["opus; rm -rf /", "model name", "a$b", "", "m`whoami`", "a&b"])
def test_model_validator_rejects_bad_names(model: str) -> None:
    # The validator is a charset allowlist: any token with shell metacharacters
    # or whitespace is rejected.
    with pytest.raises(ValidationError):
        _req("claude", model=model)


def test_model_validator_rejects_overlong_name() -> None:
    # F-08: even a charset-clean name is rejected past the 256-char cap.
    with pytest.raises(ValidationError):
        _req("claude", model="a" * 300)


def test_model_validator_accepts_name_at_cap() -> None:
    # Exactly 256 charset-clean chars is allowed (boundary).
    name = "a" * 256
    argv = build_command(_req("claude", model=name))
    assert name in argv


# --- model argv-injection defense (P1): a charset-clean value must not be able to
#     smuggle a security-posture flag through the argv token after ``--model``. ---


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-8",
        "anthropic/claude-sonnet-5",
        "gpt-5.6",
        "qwen3:32b",
    ],
)
def test_model_validator_accepts_real_client_model_ids(model: str) -> None:
    # Real model ids never start with '-' and carry no forbidden flag head, so
    # the injection defense leaves them fully valid.
    argv = build_command(_req("claude", model=model))
    assert model in argv


@pytest.mark.parametrize("model", ["-foo", "--read-only", "--allow-writes", "-p"])
def test_model_validator_rejects_leading_dash(model: str) -> None:
    # A leading '-' would position the value as its own argv flag after --model,
    # so any dash-led value is rejected even when charset-clean.
    with pytest.raises(ValidationError):
        _req("claude", model=model)


def test_model_validator_rejects_flag_value_form() -> None:
    # The '--flag=value' head is split and checked exactly like extra_args; the
    # leading '-' also independently rejects it.
    with pytest.raises(ValidationError):
        _req("claude", model="--model=x")


@pytest.mark.parametrize("token", sorted(_FORBIDDEN_EXTRA_ARG_TOKENS))
def test_model_validator_rejects_forbidden_security_tokens(token: str) -> None:
    # Every token blocked for extra_args is blocked identically for model — a
    # value like '--dangerously-skip-permissions' must not reach argv as the
    # --model operand and re-enable the very posture extra_args guards.
    with pytest.raises(ValidationError):
        _req("claude", model=token)


@pytest.mark.parametrize("token", sorted(_FORBIDDEN_EXTRA_ARG_TOKENS))
def test_model_forbidden_token_uses_same_error_path_as_extra_args(token: str) -> None:
    # Same rejection wording ("security flag") for both surfaces so callers and
    # tests observe one error path.
    with pytest.raises(ValidationError) as model_exc:
        _req("claude", model=token)
    with pytest.raises(ValidationError) as extra_exc:
        _req("claude", extra_args=[token])
    assert "security flag" in str(model_exc.value)
    assert "security flag" in str(extra_exc.value)
