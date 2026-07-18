"""Tests for the CC-05 explorer model override (PRD-CORE-210 FR07/FR08).

``CLAUDE_CODE_EXPLORER_MODEL`` is resolved at template-render time with an
allowlist — fable/unknown values must never reach the generated agent file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.channels.claude_code._explorer_subagent import (
    EXPLORER_MODEL_ENV_VAR,
    get_explorer_agent_content,
    install_cc05_subagent,
)


def test_default_model_is_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXPLORER_MODEL_ENV_VAR, raising=False)
    assert "model: haiku" in get_explorer_agent_content()


def test_sonnet_override_is_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXPLORER_MODEL_ENV_VAR, "sonnet")
    content = get_explorer_agent_content()
    assert "model: sonnet" in content
    assert "model: haiku" not in content


def test_override_is_case_and_whitespace_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXPLORER_MODEL_ENV_VAR, "  Sonnet ")
    assert "model: sonnet" in get_explorer_agent_content()


@pytest.mark.parametrize("rejected", ["fable", "opus", "claude-fable-5", "bogus"])
def test_disallowed_values_fall_back_to_haiku(monkeypatch: pytest.MonkeyPatch, rejected: str) -> None:
    # fable stays main-loop-only by operator rule; opus is not an allowed
    # explorer tier; arbitrary strings must never reach the agent file.
    monkeypatch.setenv(EXPLORER_MODEL_ENV_VAR, rejected)
    content = get_explorer_agent_content()
    assert "model: haiku" in content
    assert f"model: {rejected}" not in content


def test_empty_env_value_falls_back_to_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXPLORER_MODEL_ENV_VAR, "")
    assert "model: haiku" in get_explorer_agent_content()


def test_no_unrendered_placeholder_remains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EXPLORER_MODEL_ENV_VAR, raising=False)
    assert "{model}" not in get_explorer_agent_content()


def test_install_writes_override_and_reinstall_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EXPLORER_MODEL_ENV_VAR, "sonnet")
    assert install_cc05_subagent(tmp_path) is True
    written = (tmp_path / ".claude/agents/trw-distill-explorer.md").read_text(encoding="utf-8")
    assert "model: sonnet" in written

    # Same env → idempotent no-op.
    assert install_cc05_subagent(tmp_path) is False

    # Changed env → the file is refreshed on the next install pass.
    monkeypatch.delenv(EXPLORER_MODEL_ENV_VAR)
    assert install_cc05_subagent(tmp_path) is True
    refreshed = (tmp_path / ".claude/agents/trw-distill-explorer.md").read_text(encoding="utf-8")
    assert "model: haiku" in refreshed
