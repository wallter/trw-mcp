"""Behavior tests for the sanitized subprocess env allowlist."""

from __future__ import annotations

from trw_mcp.dispatch._env import build_runner_env, build_subprocess_env


def _source() -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "LANG": "en_US.UTF-8",
        "LC_CTYPE": "en_US.UTF-8",
        "TERM": "xterm",
        "USER": "u",
        # planted secrets that must NOT leak to the child:
        "AWS_SECRET_ACCESS_KEY": "PLANTED-SECRET",
        "DATABASE_URL": "postgres://secret",
        "ANTHROPIC_API_KEY": "sk-ant-xxx",
        "OPENAI_API_KEY": "sk-openai-yyy",
        "GEMINI_API_KEY": "g-zzz",
    }


def test_base_vars_always_present() -> None:
    env = build_subprocess_env("codex", source_env=_source())
    for name in ("PATH", "HOME", "LANG", "TERM", "USER"):
        assert env[name] == _source()[name]


def test_locale_vars_passed_by_prefix() -> None:
    env = build_subprocess_env("codex", source_env=_source())
    assert env["LC_CTYPE"] == "en_US.UTF-8"


def test_planted_unrelated_secret_is_excluded() -> None:
    env = build_subprocess_env("codex", source_env=_source())
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "DATABASE_URL" not in env
    assert "PLANTED-SECRET" not in env.values()


def test_codex_gets_only_openai_key() -> None:
    env = build_subprocess_env("codex", source_env=_source())
    assert env["OPENAI_API_KEY"] == "sk-openai-yyy"
    assert "ANTHROPIC_API_KEY" not in env
    assert "GEMINI_API_KEY" not in env


def test_claude_gets_anthropic_key_not_openai() -> None:
    env = build_subprocess_env("claude", source_env=_source())
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-xxx"
    assert "OPENAI_API_KEY" not in env


def test_agy_gets_gemini_key_not_anthropic() -> None:
    env = build_subprocess_env("agy", source_env=_source())
    assert env["GEMINI_API_KEY"] == "g-zzz"
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env


def test_opencode_gets_all_three_provider_keys() -> None:
    env = build_subprocess_env("opencode", source_env=_source())
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-xxx"
    assert env["OPENAI_API_KEY"] == "sk-openai-yyy"
    assert env["GEMINI_API_KEY"] == "g-zzz"
    # but still no unrelated secret
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_missing_allowed_var_is_simply_absent() -> None:
    env = build_subprocess_env("codex", source_env={"PATH": "/bin"})
    assert env == {"PATH": "/bin"}


# --- F-01: build_runner_env (the intermediate _run_job child env) -------------


def _runner_source() -> dict[str, str]:
    return {
        "PATH": "/usr/bin",
        "HOME": "/home/u",
        "PYTHONPATH": "/work/src:/work/other/src",
        "VIRTUAL_ENV": "/work/.venv",
        # planted host secret + the client's own key:
        "AWS_SECRET_ACCESS_KEY": "PLANTED-SECRET",
        "OPENAI_API_KEY": "sk-openai-yyy",
    }


def test_runner_env_excludes_unrelated_host_secret() -> None:
    env = build_runner_env("codex", source_env=_runner_source())
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "PLANTED-SECRET" not in env.values()


def test_runner_env_includes_client_key_and_pythonpath() -> None:
    env = build_runner_env("codex", source_env=_runner_source())
    # The client's own provider key still reaches the foreign agent.
    assert env["OPENAI_API_KEY"] == "sk-openai-yyy"
    # PYTHONPATH + VIRTUAL_ENV are forwarded so `python -m trw_mcp...` imports.
    assert env["PYTHONPATH"] == "/work/src:/work/other/src"
    assert env["VIRTUAL_ENV"] == "/work/.venv"


def test_runner_env_omits_passthrough_when_absent() -> None:
    # No PYTHONPATH/VIRTUAL_ENV in the source -> they are simply absent, not "".
    env = build_runner_env("codex", source_env={"PATH": "/bin", "OPENAI_API_KEY": "k"})
    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env
