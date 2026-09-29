"""PRD-FIX-118/R8 sol round 1 P1: init/update must write hook-env.d for EVERY
resolved client, not just ``ide_targets[0]``.

Before this fix, a multi-client install (``init_project(ide="all")``, or an
update over a project with several IDE configs already present) wrote only the
FIRST resolved client's ``.trw/runtime/hook-env.d/<key>.sh``. Every other
installed, hooks-capable client either kept a stale file from a previous run
or -- for codex/copilot, which share ``.claude/hooks`` with claude-code --
silently inherited whichever profile happened to be first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._file_ops import hook_env_dir, write_hook_env_for_clients

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("no_memory_daemon")]


def test_write_hook_env_for_clients_writes_one_file_per_client(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"

    written = write_hook_env_for_clients(trw_dir, ["claude-code", "copilot", "cursor-ide"])

    names = sorted(p.name for p in written)
    assert names == sorted({"claude.sh", "github.sh", "cursor-rules.sh"})
    for path in written:
        assert path.parent == hook_env_dir(trw_dir)
        assert path.exists()


def test_write_hook_env_for_clients_content_is_per_profile(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"

    write_hook_env_for_clients(trw_dir, ["claude-code", "opencode"])

    claude_content = (hook_env_dir(trw_dir) / "claude.sh").read_text(encoding="utf-8")
    opencode_content = (hook_env_dir(trw_dir) / "opencode.sh").read_text(encoding="utf-8")
    assert "Claude Code" in claude_content
    assert "NUDGE_ENABLED=true" in claude_content
    assert "OpenCode" in opencode_content
    assert "NUDGE_ENABLED=false" in opencode_content


def test_write_hook_env_for_clients_deduplicates(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"

    written = write_hook_env_for_clients(trw_dir, ["claude-code", "claude-code", "claude-code"])

    assert len(written) == 1


def test_write_hook_env_for_clients_empty_falls_back_to_claude_code(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"

    written = write_hook_env_for_clients(trw_dir, [])

    assert len(written) == 1
    assert written[0].name == "claude.sh"


def test_write_hook_env_for_clients_one_bad_client_does_not_block_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-open per client: a single profile's write failure must not drop the others."""
    import trw_mcp.bootstrap._hook_env as hook_env_module
    from trw_mcp.models.config._profiles import resolve_client_profile

    real_write = hook_env_module._write_hook_env_file

    def _flaky(trw_dir: Path, profile: object, **kwargs: object) -> Path:
        if getattr(profile, "client_id", None) == "copilot":
            raise RuntimeError("boom")
        return real_write(trw_dir, profile, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(hook_env_module, "_write_hook_env_file", _flaky)
    monkeypatch.setattr(
        "trw_mcp.models.config._profiles.resolve_client_profile",
        resolve_client_profile,
    )

    trw_dir = tmp_path / ".trw"
    written = write_hook_env_for_clients(trw_dir, ["claude-code", "copilot", "cursor-ide"])

    names = sorted(p.name for p in written)
    assert names == sorted({"claude.sh", "cursor-rules.sh"}), "one client's failure must not drop the others"


def test_init_all_clients_writes_hook_env_for_every_hooks_capable_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration-level proof: a real ide='all' init covers every resolved client."""
    import structlog

    from tests._ide_detection_isolation import isolate_ide_detection
    from trw_mcp.bootstrap import init_project

    isolate_ide_detection(monkeypatch)
    saved = structlog.get_config()
    try:
        (tmp_path / ".git").mkdir()

        result = init_project(tmp_path, ide="all")

        assert not result["errors"], result["errors"]
        keys = {p.stem for p in hook_env_dir(tmp_path / ".trw").glob("*.sh")}
        # Every hooks-capable built-in profile must have written its own file --
        # not just whichever client happened to be first in the resolved list.
        assert {"claude", "cursor-rules", "cursor-cli", "github", "antigravity-cli"} <= keys
    finally:
        structlog.configure(**saved)


def test_update_all_clients_refreshes_hook_env_for_every_hooks_capable_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The update path must fan out the same way the init path does."""
    import structlog

    from tests._ide_detection_isolation import isolate_ide_detection
    from trw_mcp.bootstrap import init_project, update_project

    isolate_ide_detection(monkeypatch)
    saved = structlog.get_config()
    try:
        (tmp_path / ".git").mkdir()
        init_result = init_project(tmp_path, ide="all")
        assert not init_result["errors"], init_result["errors"]

        # Wipe the hook-env directory to prove UPDATE (not the earlier init)
        # is what repopulates every client's file.
        import shutil

        shutil.rmtree(hook_env_dir(tmp_path / ".trw"), ignore_errors=True)

        update_result = update_project(tmp_path)

        assert not update_result.get("errors"), update_result.get("errors")
        keys = {p.stem for p in hook_env_dir(tmp_path / ".trw").glob("*.sh")}
        assert {"claude", "cursor-rules", "cursor-cli", "github", "antigravity-cli"} <= keys
    finally:
        structlog.configure(**saved)
