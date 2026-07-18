"""PRD-CORE-149 FR04: .trw/runtime/hook-env.sh generation.

Validates that the bootstrap helper writes a well-formed shell env file with
per-profile flags, is idempotent across rewrites, and degrades safely when
absent (backward compat for pre-FR04 installs).
"""

from __future__ import annotations

from pathlib import Path
from shlex import quote as shlex_quote

import pytest

from trw_mcp.bootstrap._file_ops import _write_hook_env_file
from trw_mcp.models.config._profiles import resolve_client_profile

pytestmark = pytest.mark.unit


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_bootstrap_writes_hook_env_file(tmp_path: Path) -> None:
    profile = resolve_client_profile("claude-code")
    trw_dir = tmp_path / ".trw"
    written = _write_hook_env_file(trw_dir, profile)
    assert written == trw_dir / "runtime" / "hook-env.sh"
    assert written.exists()
    content = _read(written)
    # Values are shell-quoted via shlex.quote: metachar-free tokens are emitted
    # bare, values with spaces are single-quoted.
    assert "export HOOKS_ENABLED=true" in content
    assert "export NUDGE_ENABLED=true" in content
    assert "export TRW_CLIENT_DISPLAY_NAME='Claude Code'" in content
    assert "export TRW_CLIENT_CONFIG_DIR=.claude" in content


def test_opencode_profile_writes_false_flags(tmp_path: Path) -> None:
    profile = resolve_client_profile("opencode")
    written = _write_hook_env_file(tmp_path / ".trw", profile)
    content = _read(written)
    # opencode is a light-mode profile: hooks_enabled=False, nudge_enabled=False
    assert "export HOOKS_ENABLED=false" in content
    assert "export NUDGE_ENABLED=false" in content
    assert "export TRW_CLIENT_DISPLAY_NAME=OpenCode" in content
    assert "export TRW_CLIENT_CONFIG_DIR=.opencode" in content


def test_rewrites_idempotently(tmp_path: Path) -> None:
    profile = resolve_client_profile("claude-code")
    written = _write_hook_env_file(tmp_path / ".trw", profile)
    first = _read(written)
    _write_hook_env_file(tmp_path / ".trw", profile)
    second = _read(written)
    assert first == second


def test_rewrite_after_profile_change_reflects_new_values(tmp_path: Path) -> None:
    """FR04 idempotency also means: a rewrite with a different profile wins."""
    trw_dir = tmp_path / ".trw"
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    _write_hook_env_file(trw_dir, resolve_client_profile("opencode"))
    content = _read(trw_dir / "runtime" / "hook-env.sh")
    assert "HOOKS_ENABLED=false" in content
    assert "OpenCode" in content
    assert "Claude Code" not in content


def test_retired_profile_cannot_leak_stale_identity_into_hook_env(tmp_path: Path) -> None:
    """FR04: sanitized profile resolution precedes hook policy persistence."""
    profile = resolve_client_profile("gemini")
    written = _write_hook_env_file(tmp_path / ".trw", profile)
    content = _read(written)

    assert profile.client_id == "claude-code"
    assert "Google Gemini" not in content
    assert "TRW_CLIENT_DISPLAY_NAME='Claude Code'" in content


def test_creates_parent_runtime_dir(tmp_path: Path) -> None:
    """runtime/ must be auto-created; init can run before .trw/runtime/ exists."""
    trw_dir = tmp_path / ".trw"
    assert not (trw_dir / "runtime").exists()
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    assert (trw_dir / "runtime").is_dir()
    assert (trw_dir / "runtime" / "hook-env.sh").exists()


def test_malicious_display_name_is_shell_escaped(tmp_path: Path) -> None:
    """A profile value with shell metacharacters must not inject when sourced.

    The generated hook-env.sh is ``source``d by every TRW hook at startup, so an
    unescaped display_name like ``$(touch pwned)`` would execute. shlex.quote
    must neutralize it.
    """
    import subprocess

    profile = resolve_client_profile("claude-code").model_copy(
        update={"display_name": "$(touch " + str(tmp_path / "pwned") + ")`echo hi`"}
    )
    written = _write_hook_env_file(tmp_path / ".trw", profile)
    # Source the file in a real shell; the payload must NOT run.
    subprocess.run(
        ["sh", "-c", f". {shlex_quote(str(written))}"],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert not (tmp_path / "pwned").exists(), "command substitution executed — injection!"
    # And the literal value must survive intact when read back.
    out = subprocess.run(
        ["sh", "-c", f". {shlex_quote(str(written))}; printf '%s' \"$TRW_CLIENT_DISPLAY_NAME\""],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert out.stdout == profile.display_name


def test_file_permissions_are_readable(tmp_path: Path) -> None:
    profile = resolve_client_profile("claude-code")
    written = _write_hook_env_file(tmp_path / ".trw", profile)
    # 0o644 = rw-r--r--
    mode = written.stat().st_mode & 0o777
    assert mode == 0o644
