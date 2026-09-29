"""PRD-CORE-149 FR04 / R8 hook-env-per-client: .trw/runtime/hook-env.d/<key>.sh generation.

Validates that the bootstrap helper writes a well-formed shell env file with
per-profile flags, is idempotent across rewrites, degrades safely when absent
(backward compat for pre-FR04 installs), and -- the point of the R8 split --
that no two clients' syncs ever clobber the same file. Before this split every
profile rewrote a SINGLE shared ``hook-env.sh``, so whichever client synced
LAST silently decided every other client's ``NUDGE_ENABLED``/``TRW_SESSION_ID``
too (the PRD-FIX-118 concurrency defect).
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
    assert written == trw_dir / "runtime" / "hook-env.d" / "claude.sh"
    assert written.exists()
    content = _read(written)
    # Values are shell-quoted via shlex.quote: metachar-free tokens are emitted
    # bare, values with spaces are single-quoted.
    assert "HOOKS_ENABLED" not in content, "hooks_enabled lives in .trw/config.yaml now"
    assert "export NUDGE_ENABLED=true" in content
    assert "export TRW_CLIENT_DISPLAY_NAME='Claude Code'" in content
    assert "export TRW_CLIENT_CONFIG_DIR=.claude" in content


def test_opencode_profile_writes_false_flags(tmp_path: Path) -> None:
    profile = resolve_client_profile("opencode")
    written = _write_hook_env_file(tmp_path / ".trw", profile)
    content = _read(written)
    assert written == tmp_path / ".trw" / "runtime" / "hook-env.d" / "opencode.sh"
    # opencode is a light-mode profile without TRW hooks: it installs none and leaves the shared
    # config alone (it used to seed hooks_enabled: false there, disabling every other client's hooks).
    assert not (tmp_path / ".trw" / "config.yaml").exists()
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


def test_syncing_a_different_client_never_touches_this_clients_file(tmp_path: Path) -> None:
    """R8: the defect this split fixes.

    Before the per-client split, syncing opencode AFTER claude-code overwrote
    the ONE shared hook-env.sh with opencode's flags, silently switching off
    Claude Code's own nudges and dropping its TRW_SESSION_ID export --
    disarming a DIFFERENT client's already-installed hooks. Each client must
    now own a file no other client's sync can reach.
    """
    trw_dir = tmp_path / ".trw"
    claude_path = _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    claude_before = _read(claude_path)

    opencode_path = _write_hook_env_file(trw_dir, resolve_client_profile("opencode"))

    assert opencode_path != claude_path
    assert _read(claude_path) == claude_before, "a different client's sync must never touch this file"
    assert "NUDGE_ENABLED=true" in _read(claude_path)
    assert "Claude Code" in _read(claude_path)
    assert "NUDGE_ENABLED=false" in _read(opencode_path)
    assert "OpenCode" in _read(opencode_path)


def test_syncing_claude_code_then_opencode_then_grok_preserves_each_clients_own_file(tmp_path: Path) -> None:
    """The full multi-client, multi-sync-order scenario named in the defect report."""
    trw_dir = tmp_path / ".trw"
    order = ("claude-code", "opencode", "grok", "claude-code", "opencode")
    written: dict[str, Path] = {}
    for client_id in order:
        written[client_id] = _write_hook_env_file(trw_dir, resolve_client_profile(client_id))

    claude_content = _read(written["claude-code"])
    opencode_content = _read(written["opencode"])
    grok_content = _read(written["grok"])

    assert written["claude-code"].name == "claude.sh"
    assert written["opencode"].name == "opencode.sh"
    assert written["grok"].name == "grok.sh"
    assert "NUDGE_ENABLED=true" in claude_content
    assert "Claude Code" in claude_content
    assert "NUDGE_ENABLED=false" in opencode_content
    assert "OpenCode" in opencode_content
    assert "Grok Build CLI" in grok_content


def test_retired_profile_cannot_leak_stale_identity_into_hook_env(tmp_path: Path) -> None:
    """FR04: sanitized profile resolution precedes hook policy persistence."""
    profile = resolve_client_profile("aider")
    written = _write_hook_env_file(tmp_path / ".trw", profile)
    content = _read(written)

    assert profile.client_id == "claude-code"
    assert "Aider" not in content
    assert "TRW_CLIENT_DISPLAY_NAME='Claude Code'" in content


def test_creates_parent_runtime_dir(tmp_path: Path) -> None:
    """runtime/hook-env.d must be auto-created; init can run before .trw/runtime/ exists."""
    trw_dir = tmp_path / ".trw"
    assert not (trw_dir / "runtime").exists()
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    assert (trw_dir / "runtime" / "hook-env.d").is_dir()
    assert (trw_dir / "runtime" / "hook-env.d" / "claude.sh").exists()


def test_writing_a_new_client_deletes_the_stale_shared_file(tmp_path: Path) -> None:
    """The pre-split shared hook-env.sh is TRW-generated; a sync cleans it up rather than leaving it inert."""
    trw_dir = tmp_path / ".trw"
    legacy = trw_dir / "runtime"
    legacy.mkdir(parents=True)
    (legacy / "hook-env.sh").write_text("export NUDGE_ENABLED=true\n", encoding="utf-8")

    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))

    assert not (legacy / "hook-env.sh").exists()


def test_malicious_display_name_is_shell_escaped(tmp_path: Path) -> None:
    """A profile value with shell metacharacters must not inject when sourced.

    The generated hook-env.d/<key>.sh is ``source``d by every TRW hook at
    startup, so an unescaped display_name like ``$(touch pwned)`` would
    execute. shlex.quote must neutralize it.
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


def _reads_during_publish(monkeypatch: pytest.MonkeyPatch, target: Path) -> list[str]:
    """What a concurrent reader sees at *target* while the new bytes are being synced."""
    import os

    from trw_mcp.state import persistence

    seen: list[str] = []
    real_fsync = os.fsync

    def _fsync_and_peek(fd: int) -> None:
        seen.append(target.read_text(encoding="utf-8"))
        real_fsync(fd)

    monkeypatch.setattr(persistence.os, "fsync", _fsync_and_peek)
    return seen


def test_a_sourcing_hook_never_sees_a_partial_hook_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = tmp_path / ".trw"
    profile = resolve_client_profile("claude-code")
    path = _write_hook_env_file(trw_dir, profile)
    before = _read(path)
    seen = _reads_during_publish(monkeypatch, path)

    # A rewrite of the SAME client's file (a re-sync, or a nudge_enabled flip)
    # is what must stay atomic -- a DIFFERENT client's sync no longer touches
    # this path at all under the per-client split, so it would not exercise
    # this file's own atomicity.
    changed = profile.model_copy(update={"nudge_enabled": False})
    _write_hook_env_file(trw_dir, changed)

    assert seen[0] == before  # the old script stays whole until the replace (later peeks: config, flags)
    assert "export NUDGE_ENABLED=false" in _read(path)
    assert (path.stat().st_mode & 0o777) == 0o644
    assert sorted(p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")) == []


def test_hook_env_is_published_where_os_has_no_fchmod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows has no ``os.fchmod`` before Python 3.13; the atomic publish must not depend on it."""
    import os

    monkeypatch.delattr(os, "fchmod", raising=False)
    written = _write_hook_env_file(tmp_path / ".trw", resolve_client_profile("claude-code"))

    assert "export NUDGE_ENABLED=true" in _read(written)
    assert (written.stat().st_mode & 0o777) == 0o644


@pytest.mark.parametrize("hookless", ["opencode", "grok"])
def test_syncing_a_hookless_client_never_disables_the_other_clients_hooks(tmp_path: Path, hookless: str) -> None:
    """worker-3 P1: in a multi-client repo, syncing opencode or grok switched Claude Code's hooks off."""
    from trw_mcp.state._hook_flags import hook_flags_path

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    operator_config = "# operator note\ntask_type: coding\n"
    (trw_dir / "config.yaml").write_text(operator_config, encoding="utf-8")

    for client in ("claude-code", hookless, "claude-code", hookless):
        _write_hook_env_file(trw_dir, resolve_client_profile(client))

    assert (trw_dir / "config.yaml").read_text(encoding="utf-8") == operator_config  # never rewritten
    assert "hooks_enabled=true" in hook_flags_path(trw_dir).read_text(encoding="utf-8")


def test_hooks_enabled_client_warns_when_resolved_hooks_are_off(tmp_path: Path) -> None:
    """New R8 requirement: a client that installs hooks but whose project has hooks_enabled
    resolved false gets one clear, named warning -- not silence."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text("hooks_enabled: false\n", encoding="utf-8")

    warnings: list[str] = []
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"), warnings=warnings)

    assert len(warnings) == 1
    message = warnings[0]
    assert "Claude Code" in message
    assert "hooks_enabled" in message
    assert ".trw/config.yaml" in message


def test_hookless_profile_never_warns_when_hooks_are_off(tmp_path: Path) -> None:
    """A profile with no TRW hooks installed has nothing to warn about."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text("hooks_enabled: false\n", encoding="utf-8")

    warnings: list[str] = []
    _write_hook_env_file(trw_dir, resolve_client_profile("opencode"), warnings=warnings)

    assert warnings == []


def test_hooks_enabled_client_never_warns_when_hooks_resolve_on(tmp_path: Path) -> None:
    warnings: list[str] = []
    _write_hook_env_file(tmp_path / ".trw", resolve_client_profile("claude-code"), warnings=warnings)

    assert warnings == []


# --------------------------------------------------------------------------- #
# sol round 1 P2: the warning must name the layer that actually decided
# hooks_enabled, not always point at .trw/config.yaml.
# --------------------------------------------------------------------------- #


def test_warning_names_the_env_var_when_that_is_what_set_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_HOOKS_ENABLED", "false")
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    # No project config.yaml at all -- the env var alone is what silences hooks.

    warnings: list[str] = []
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"), warnings=warnings)

    assert len(warnings) == 1
    assert "TRW_HOOKS_ENABLED" in warnings[0]
    assert str(trw_dir / "config.yaml") not in warnings[0], "must not blame the project file when env decided it"


def test_warning_names_the_machine_config_when_that_is_what_set_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRW_HOOKS_ENABLED", raising=False)
    fake_home = tmp_path / "home"
    (fake_home / ".trw").mkdir(parents=True)
    (fake_home / ".trw" / "config.yaml").write_text("hooks_enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    trw_dir = tmp_path / "project" / ".trw"
    trw_dir.mkdir(parents=True)
    # Project config.yaml exists but never mentions hooks_enabled -- the
    # machine layer is what's actually deciding it.
    (trw_dir / "config.yaml").write_text("task_root: docs\n", encoding="utf-8")

    warnings: list[str] = []
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"), warnings=warnings)

    assert len(warnings) == 1
    assert str(fake_home / ".trw" / "config.yaml") in warnings[0]
    assert str(trw_dir / "config.yaml") not in warnings[0], "must not blame the project file when machine decided it"


# --------------------------------------------------------------------------- #
# sol round 1 P1: a not-yet-upgraded install's .claude/hooks/lib-trw.sh still
# sources the legacy shared hook-env.sh directly (it predates the per-client
# split and is refreshed only by init/update's own hook-copy step, never by a
# sync). Deleting that file out from under it would silently drop its
# nudge/session settings.
# --------------------------------------------------------------------------- #


def _install_pre_split_lib(trw_dir: Path) -> Path:
    """A pre-split lib-trw.sh: sources the single shared hook-env.sh, no per-client key."""
    hooks_dir = trw_dir.parent / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    lib = hooks_dir / "lib-trw.sh"
    lib.write_text(
        "#!/bin/sh\n"
        'if [ -f "$PWD/.trw/runtime/hook-env.sh" ]; then\n'
        '  . "$PWD/.trw/runtime/hook-env.sh" 2>/dev/null || true\n'
        "fi\n",
        encoding="utf-8",
    )
    return lib


def test_legacy_shared_file_is_kept_alive_for_a_not_yet_upgraded_install(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    _install_pre_split_lib(trw_dir)

    written = _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))

    legacy = trw_dir / "runtime" / "hook-env.sh"
    assert legacy.exists(), "an old installed lib-trw.sh still sources this file directly"
    assert legacy.read_text(encoding="utf-8") == written.read_text(encoding="utf-8")


def test_legacy_shared_file_refreshes_alongside_the_per_client_file(tmp_path: Path) -> None:
    """The legacy file must track the LATEST write, not freeze at its first content."""
    trw_dir = tmp_path / ".trw"
    _install_pre_split_lib(trw_dir)

    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code").model_copy(update={"nudge_enabled": False}))

    legacy_content = (trw_dir / "runtime" / "hook-env.sh").read_text(encoding="utf-8")
    assert "export NUDGE_ENABLED=false" in legacy_content


def test_legacy_shared_file_is_deleted_once_the_lib_is_upgraded(tmp_path: Path) -> None:
    """Once the installed library is refreshed (contains the split marker), retirement resumes."""
    trw_dir = tmp_path / ".trw"
    lib = _install_pre_split_lib(trw_dir)
    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))
    assert (trw_dir / "runtime" / "hook-env.sh").exists()

    # Simulate init/update refreshing the physical hook scripts.
    lib.write_text(lib.read_text(encoding="utf-8") + "\n# hook-env.d marker\n", encoding="utf-8")

    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))

    assert not (trw_dir / "runtime" / "hook-env.sh").exists()


def test_no_installed_lib_at_all_never_writes_the_legacy_file(tmp_path: Path) -> None:
    """A fresh project with no .claude/hooks/lib-trw.sh has nothing old to preserve for."""
    trw_dir = tmp_path / ".trw"

    _write_hook_env_file(trw_dir, resolve_client_profile("claude-code"))

    assert not (trw_dir / "runtime" / "hook-env.sh").exists()
