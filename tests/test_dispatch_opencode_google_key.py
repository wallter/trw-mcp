"""opencode's google provider reads GOOGLE_GENERATIVE_AI_API_KEY (DoD-5 DISPATCH-OPENCODE-KEY-NAME).

Dispatch used to forward only GEMINI_API_KEY to an opencode child, so every google-model
dispatch failed with ProviderAuthError. These tests build the child environment the way a
dispatch does and read the variables the child would see.
"""

from __future__ import annotations


def test_opencode_child_gets_the_google_key_under_the_name_it_reads() -> None:
    from trw_mcp.dispatch._env import build_subprocess_env

    env = build_subprocess_env("opencode", {"PATH": "/bin", "GEMINI_API_KEY": "gk-1"})

    assert env["GOOGLE_GENERATIVE_AI_API_KEY"] == "gk-1"


def test_an_explicit_google_generative_key_wins_over_the_synonym() -> None:
    from trw_mcp.dispatch._env import build_subprocess_env

    env = build_subprocess_env(
        "opencode", {"PATH": "/bin", "GEMINI_API_KEY": "gk-1", "GOOGLE_GENERATIVE_AI_API_KEY": "gk-2"}
    )

    assert env["GOOGLE_GENERATIVE_AI_API_KEY"] == "gk-2"


def test_the_synonym_reaches_no_client_that_does_not_read_the_key() -> None:
    from trw_mcp.dispatch._env import build_subprocess_env

    env = build_subprocess_env("claude", {"PATH": "/bin", "GEMINI_API_KEY": "gk-1"})

    assert "GOOGLE_GENERATIVE_AI_API_KEY" not in env
    assert "GEMINI_API_KEY" not in env
