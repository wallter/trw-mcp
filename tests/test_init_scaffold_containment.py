"""PRD-CORE-262-FR05: a single-client install scaffolds ONE client, and doctor agrees.

Two independent defects, measured on a codex-only ``init-project`` during the
2026-09-04 release rehearsal:

1. The codex-only install created 47 files under ``.claude`` (17 hook scripts,
   29 skill files, a settings file, an empty agents directory) plus a 17-line
   root ``CLAUDE.md``. Four sinks wrote there with no client parameter at all.
2. ``doctor``'s profile row read ``claude-code`` for that project while its
   agent-parity row read ``codex``, because ``_run_doctor`` built ``TRWConfig()``
   -- the bare constructor -- and never loaded the target project's config. That
   row would print the same value for a project with no client directories at
   all, so it could be neither right nor wrong.

The shared helpers live here; ``test_bootstrap_codex_split.py`` (which
PRE-EXISTED this PRD) carries the named attribution test that asserts all three
properties in one run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import structlog

from trw_mcp.bootstrap import init_project


@pytest.fixture(autouse=True)
def _restore_structlog_config() -> object:
    """``init_project`` calls ``configure_logging()``, which globally reconfigures
    structlog and breaks ``structlog.testing.capture_logs()`` in later-running
    test files. Save and restore the config so the mutation does not leak."""
    saved = structlog.get_config()
    yield
    structlog.configure(**saved)


def init_single_client_project(root: Path, client: str) -> dict[str, list[str]]:
    """Run a real ``init-project`` for exactly one client into a fresh git repo."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    result = init_project(root, ide=client)
    assert not result["errors"], result["errors"]
    return result


def claude_scaffold_paths(root: Path) -> list[str]:
    """Every path under a ``.claude`` directory, relative to *root*."""
    claude_dir = root / ".claude"
    if not claude_dir.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in claude_dir.rglob("*"))


def doctor_rows(root: Path) -> dict[str, tuple[str, str]]:
    """Run the real ``doctor`` subcommand against *root* and return {name: (status, message)}."""
    from trw_mcp.server._subcommands_doctor import _doctor_core, _resolve_target_config

    target = root.resolve()
    results = _doctor_core(target, _resolve_target_config(target))
    return {row.name: (row.status, row.message) for row in results}


def recorded_target_platforms(root: Path) -> list[str]:
    import yaml

    data = yaml.safe_load((root / ".trw" / "config.yaml").read_text(encoding="utf-8")) or {}
    platforms = data.get("target_platforms") or []
    assert isinstance(platforms, list)
    return [str(entry) for entry in platforms]


def test_claude_code_init_still_receives_its_full_scaffold(tmp_path: Path) -> None:
    """RISK-008 regression: containment removes only FOREIGN surfaces.

    Selecting claude-code must still produce the hooks, skills, settings file and
    root instruction file it always did -- otherwise the fix traded one silent
    wrong install for another.
    """
    init_single_client_project(tmp_path, "claude-code")

    assert (tmp_path / ".claude" / "settings.json").is_file()
    assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "claude-code lost its bundled hooks"
    assert list((tmp_path / ".claude" / "skills").iterdir()), "claude-code lost its bundled skills"
    assert (tmp_path / "CLAUDE.md").is_file(), "claude-code lost its root instruction file"
    assert recorded_target_platforms(tmp_path) == ["claude-code"]


def test_bare_init_with_a_preexisting_codex_marker_keeps_the_default_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CORE262-13: auto-detected codex-only must NOT take the explicit-only path.

    ``detect_ide`` resolves a bare (``ide=None``) install to ``["codex"]`` when
    the target already has a ``.codex/`` marker on disk -- e.g. a project
    scaffolded by another tool, or a re-run after a partial codex-only setup.
    That is detection, not a user request for codex-only, so it must keep
    HEAD's default (Claude Code) scaffold. Only an EXPLICIT
    ``init_project(ide="codex")`` may drop it -- see
    ``test_codex_init_creates_no_claude_scaffold`` in the sibling module.
    Reverting the ``explicit`` threading in ``_wants_claude_scaffold`` turns
    this red.

    ``cursor``/``cursor-agent`` PATH lookups are filtered out (the same
    pattern as ``test_bootstrap_ide_detection.py``): otherwise a developer box
    with Cursor installed resolves detection to ``["cursor-ide", "codex"]``,
    which is not codex-only either way and would make this test pass for the
    wrong reason regardless of the fix.
    """
    import shutil as _shutil

    from trw_mcp.bootstrap import _utils

    original_which = _shutil.which

    def _which_filtered(cmd: str, *args: object, **kwargs: object) -> str | None:
        if cmd in {"cursor", "cursor-agent"}:
            return None
        return original_which(cmd, *args, **kwargs)

    monkeypatch.setattr(_utils.shutil, "which", _which_filtered)
    monkeypatch.delenv("CURSOR_TRACE_ID", raising=False)
    monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)

    (tmp_path / ".git").mkdir()
    (tmp_path / ".codex").mkdir()

    from trw_mcp.bootstrap._utils import detect_ide

    assert detect_ide(tmp_path) == ["codex"], "precondition: detection must resolve to codex-only here"

    result = init_project(tmp_path)  # bare -- no ide= override
    assert not result["errors"], result["errors"]

    assert (tmp_path / ".claude" / "settings.json").is_file(), "auto-detected codex-only dropped the default scaffold"
    assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "auto-detected codex-only dropped the bundled hooks"
    assert list((tmp_path / ".claude" / "skills").iterdir()), "auto-detected codex-only dropped the bundled skills"
    assert (tmp_path / "CLAUDE.md").is_file(), "auto-detected codex-only dropped the root instruction file"


def test_orphan_strip_failure_is_a_recorded_error_not_a_silent_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CORE262-14: a genuine strip-write failure must not report a clean install.

    ``_strip_orphaned_block`` used to catch ``OSError`` and return ``False`` --
    indistinguishable from its own far more common "nothing to strip" no-op --
    so ``_generate_root_files`` (which never inspected that return value) could
    report a clean install while the foreign TRW block stayed in CLAUDE.md.
    The fix routes the write through ``FileStateWriter``, which raises
    ``StateError`` instead; this proves the caller now surfaces it. Reverting
    either half (the ``FileStateWriter`` write or the ``except StateError``
    catch in ``_generate_root_files``) turns this red: the first by letting
    the induced failure vanish into a swallowed ``False``, the second by
    letting the ``StateError`` escape uncaught instead of landing in
    ``result["errors"]``.
    """
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateWriter

    # A claude-code install first, so CLAUDE.md exists with a real TRW block
    # for the subsequent codex-only run to find "orphaned" and try to strip.
    init_single_client_project(tmp_path, "claude-code")
    assert "trw:start" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    def _raise(self: FileStateWriter, path: Path, content: str) -> None:
        raise StateError("induced write failure", path=str(path))

    monkeypatch.setattr(FileStateWriter, "write_text", _raise)

    result = init_project(tmp_path, ide="codex")

    assert any("CLAUDE.md" in err and "induced write failure" in err for err in result["errors"]), result["errors"]


def test_codex_init_scaffolds_codex_surfaces(tmp_path: Path) -> None:
    """Containment must not cost codex its own surfaces or the shared tree."""
    init_single_client_project(tmp_path, "codex")

    assert (tmp_path / ".codex" / "config.toml").is_file()
    assert (tmp_path / ".codex" / "INSTRUCTIONS.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "trw-deliver" / "SKILL.md").is_file()
    assert (tmp_path / ".trw" / "config.yaml").is_file()
    assert (tmp_path / ".trw" / "frameworks").is_dir()
    assert (tmp_path / "docs").is_dir()


def test_doctor_profile_row_warns_when_requested_and_resolved_disagree(tmp_path: Path) -> None:
    """A silent profile fallback must be WARN naming both values, never PASS.

    ``_check_profile`` used to return PASS whenever the REQUESTED identifier was
    merely a known profile, without ever comparing it to the resolved one.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _check_profile

    agreeing = TRWConfig(target_platforms=["codex"])
    status, message = _check_profile(tmp_path, agreeing).status, _check_profile(tmp_path, agreeing).message
    assert status == "PASS", message
    assert "codex" in message

    unknown = TRWConfig(target_platforms=["not-a-real-client"])
    row = _check_profile(tmp_path, unknown)
    assert row.status == "WARN"
    assert "not-a-real-client" in row.message
    assert row.message.count("claude-code") >= 1


def test_doctor_resolves_the_target_projects_config_not_a_constructor_default(tmp_path: Path) -> None:
    """``_resolve_target_config`` reads the project's record; the bare constructor does not."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _resolve_target_config

    init_single_client_project(tmp_path, "codex")

    assert TRWConfig().target_platforms != ["codex"], "the bare constructor must not accidentally agree"
    assert _resolve_target_config(tmp_path).target_platforms == ["codex"]

    # A project with no config at all falls back to defaults rather than raising.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _resolve_target_config(empty).target_platforms == TRWConfig().target_platforms


def test_doctor_ignores_a_config_yaml_platform_api_key(tmp_path: Path) -> None:
    """PRD-SEC-005-FR03: a tracked ``config.yaml`` is never a credential source."""
    from trw_mcp.server._subcommands_doctor import _resolve_target_config

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text(
        "target_platforms:\n- codex\nplatform_api_key: leaked-from-config\n", encoding="utf-8"
    )
    resolved = _resolve_target_config(tmp_path)
    assert resolved.target_platforms == ["codex"]
    assert resolved.platform_api_key != "leaked-from-config"


def test_doctor_json_output_is_unchanged_in_shape(tmp_path: Path) -> None:
    """The row set stays serializable and every row keeps its name/status/message."""
    init_single_client_project(tmp_path, "codex")
    rows = doctor_rows(tmp_path)
    assert "profile" in rows and "agent_parity" in rows
    payload = json.dumps({name: {"status": s, "message": m} for name, (s, m) in rows.items()})
    assert "profile" in payload


def test_run_doctor_entry_point_uses_the_target_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI seam itself -- not just the helper -- must report the recorded client.

    Reading ``_resolve_target_config`` directly would prove the helper works
    while ``_run_doctor`` still called the bare constructor; this exercises the
    argparse entry point the operator actually runs.
    """
    from trw_mcp.server._subcommands_doctor import _run_doctor

    init_single_client_project(tmp_path, "codex")
    args = argparse.Namespace(target_dir=str(tmp_path), format="human", fix=False)
    with pytest.raises(SystemExit):
        _run_doctor(args)
    stdout = capsys.readouterr().out
    profile_lines = [line for line in stdout.splitlines() if "] profile:" in line]
    assert profile_lines, stdout
    assert "codex" in profile_lines[0], profile_lines[0]
    assert "profile: claude-code" not in profile_lines[0]
