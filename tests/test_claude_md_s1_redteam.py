"""CLAUDE-MD S1 red-team findings: TRW never deletes or overwrites user text outside its own block.

Each test reproduces one finding of the adversarial pass on S1 (operator rule, 2026-10-01: TRW may only add or
strip its own uniquely identified marked block in a user's ``CLAUDE.md`` / ``AGENTS.md``; HB-2).
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Any

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401 -- fixture

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_START = "<!-- trw:start -->"
_END = "<!-- trw:end -->"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


# --- B1: --force never replaces a user's AGENTS.md wholesale ---------------------------------------------------


def test_force_keeps_a_users_agents_md_and_adds_the_block(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._grok import generate_grok_agents_md

    repo = _repo(tmp_path)
    agents = repo / "AGENTS.md"
    agents.write_text("# Team rules\n\nAlways run make check.\n", encoding="utf-8")

    result = generate_grok_agents_md(repo, force=True)

    text = agents.read_text(encoding="utf-8")
    assert not result["errors"], result["errors"]
    assert text.startswith("# Team rules\n\nAlways run make check.\n"), text
    assert _START in text and "@.trw/INSTRUCTIONS.md" in text


def test_force_refreshes_only_the_trw_block(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._grok import generate_grok_agents_md

    repo = _repo(tmp_path)
    agents = repo / "AGENTS.md"
    agents.write_text(f"# Mine\n\n{_START}\nstale TRW text\n{_END}\n\nAfter the block.\n", encoding="utf-8")

    generate_grok_agents_md(repo, force=True)

    text = agents.read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\n") and text.endswith("\n\nAfter the block.\n"), text
    assert "stale TRW text" not in text and "@.trw/INSTRUCTIONS.md" in text


def test_force_refuses_an_ambiguous_agents_md(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._grok import generate_grok_agents_md

    repo = _repo(tmp_path)
    agents = repo / "AGENTS.md"
    original = f"{_START}\nmy note\n{_START}\nTRW\n{_END}\n"
    agents.write_text(original, encoding="utf-8")

    result = generate_grok_agents_md(repo, force=True)

    assert agents.read_text(encoding="utf-8") == original
    assert result["errors"], "an ambiguous file is reported, never guessed at"


# --- B5: duplicate / nested / fenced markers are refused, never guessed ------------------------------------------


_AMBIGUOUS = {
    "nested-start": f"# Mine\n\n{_START}\nTRW\n{_START}\nmy own note\n{_END}\n\nTail.\n",
    "two-blocks": f"{_START}\nA\n{_END}\n\nmy text between\n\n{_START}\nB\n{_END}\n",
    "lone-start": f"# Mine\n{_START}\nmy text after a stray marker\n",
    "fenced-example-first": (f"# Mine\n\n```\n{_START}\nexample\n{_END}\n```\n\nmy text\n\n{_START}\nTRW\n{_END}\n"),
}


@pytest.mark.parametrize("shape", sorted(_AMBIGUOUS))
def test_sync_merge_refuses_ambiguous_markers(tmp_path: Path, shape: str) -> None:
    from trw_mcp.state.claude_md._instructions_link import agents_link_section
    from trw_mcp.state.claude_md._parser import merge_trw_section

    target = tmp_path / "AGENTS.md"
    target.write_text(_AMBIGUOUS[shape], encoding="utf-8")

    verdict = merge_trw_section(target, agents_link_section(), None, force=True, project_root=tmp_path)

    assert target.read_text(encoding="utf-8") == _AMBIGUOUS[shape]
    assert not verdict.written
    assert verdict.refusal is not None and verdict.refusal["reason"] == "ambiguous_markers"


def test_orphan_strip_leaves_a_nested_block_alone(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._orphan_strip import _strip_orphaned_block

    target = tmp_path / "AGENTS.md"
    target.write_text(_AMBIGUOUS["nested-start"], encoding="utf-8")

    assert _strip_orphaned_block(target, surface="agents_md") is False
    assert target.read_text(encoding="utf-8") == _AMBIGUOUS["nested-start"]


@pytest.mark.parametrize("shape", ["nested-start", "fenced-example-first"])
def test_uninstall_refuses_an_instruction_file_with_ambiguous_markers(tmp_path: Path, shape: str) -> None:
    from trw_mcp.server._subcommands_uninstall_config import _remove_managed_block_file

    target = tmp_path / "AGENTS.md"
    target.write_text(_AMBIGUOUS[shape], encoding="utf-8")

    assert _remove_managed_block_file(target, tmp_path, dry_run=False) == "refused"
    assert target.read_text(encoding="utf-8") == _AMBIGUOUS[shape]


# --- B4: a capture is purged as TRW-only only when the block is exactly what TRW writes --------------------------


def test_a_user_line_inside_the_block_keeps_the_capture(tmp_path: Path) -> None:
    from trw_mcp.server import _subcommands_uninstall_config as cfg
    from trw_mcp.state.claude_md._instructions_link import agents_link_section

    target = tmp_path / "AGENTS.md"
    target.write_text(agents_link_section().replace(_END, f"my personal line\n{_END}"), encoding="utf-8")
    cfg.TRW_ONLY_CAPTURES.clear()
    cfg.PREVIOUS_VERSIONS.clear()

    assert cfg._remove_managed_block_file(target, tmp_path, dry_run=False) == "stripped"

    assert target not in cfg.TRW_ONLY_CAPTURES
    assert "my personal line" in cfg.PREVIOUS_VERSIONS[target].read_text(encoding="utf-8")


def test_a_pristine_trw_block_capture_is_trw_only(tmp_path: Path) -> None:
    from trw_mcp.server import _subcommands_uninstall_config as cfg
    from trw_mcp.state.claude_md._instructions_link import claude_md_link_section

    target = tmp_path / "CLAUDE.md"
    target.write_text(claude_md_link_section(), encoding="utf-8")
    cfg.TRW_ONLY_CAPTURES.clear()
    cfg.PREVIOUS_VERSIONS.clear()

    assert cfg._remove_managed_block_file(target, tmp_path, dry_run=False) == "stripped"

    assert target in cfg.TRW_ONLY_CAPTURES and target not in cfg.PREVIOUS_VERSIONS
    cfg.TRW_ONLY_CAPTURES.clear()


# --- MAJOR: legacy unhashed backups are unproven and kept --------------------------------------------------------


def test_retention_keeps_an_unhashed_legacy_backup(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._write_backup import _prune_retention

    legacy = tmp_path / "AGENTS.md.20250101T000000000000Z"
    legacy.write_text("an old backup the user edited\n", encoding="utf-8")
    (tmp_path / "AGENTS.md.20260101T000000000000Z.0123456789abcdef").write_text("x", encoding="utf-8")

    _prune_retention(tmp_path, "AGENTS.md", 0)

    assert legacy.read_text(encoding="utf-8") == "an old backup the user edited\n"


# --- MAJOR: legacy-block migration keeps the user's CRLF bytes ---------------------------------------------------


def test_legacy_migration_keeps_crlf_outside_the_block(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._instructions_link import agents_link_section
    from trw_mcp.state.claude_md._parser import render_merged_content

    before = "# Mine\r\n\r\nLine one.\r\nLine two.  \r\n"
    after = "Tail one.\r\nTail two.\r\n"
    target = tmp_path / "AGENTS.md"
    target.write_bytes(f"{before}\r\n<!-- TRW:BEGIN -->\r\nold\r\n<!-- TRW:END -->\r\n\r\n{after}".encode())

    merged = render_merged_content(target, agents_link_section())

    assert merged.startswith(before), repr(merged)
    assert after in merged, repr(merged)
    assert "TRW:BEGIN" not in merged and "old\r\n" not in merged


# --- MINOR: uninstall never removes a .trw/trash that existed before it ------------------------------------------


def test_a_partial_uninstall_keeps_a_preexisting_empty_trash(tmp_path: Path) -> None:
    from trw_mcp.server import _uninstall_corpus as corpus

    trw = tmp_path / ".trw"
    (trw / "trash").mkdir(parents=True)
    (trw / "link").symlink_to(tmp_path)  # refused, so the run is partial
    corpus.note_preexisting_trash(trw)

    _removed, errors = corpus.remove_trw_dir(trw, tmp_path, lambda p, _t: str(p))

    assert errors == 1
    assert (trw / "trash").is_dir(), "a .trw/trash this run did not create is never removed by it"


def test_keep_memory_keeps_a_preexisting_empty_trash(tmp_path: Path) -> None:
    from trw_mcp.server import _uninstall_corpus as corpus

    trw = tmp_path / ".trw"
    (trw / "trash").mkdir(parents=True)
    (trw / "memory").mkdir()
    corpus.note_preexisting_trash(trw)

    corpus.keep_memory_in_dir(trw, tmp_path, lambda p, _t: str(p))

    assert (trw / "trash").is_dir()


def test_a_trash_this_run_created_is_still_removed_when_empty(tmp_path: Path) -> None:
    from trw_mcp.server import _uninstall_corpus as corpus

    trw = tmp_path / ".trw"
    (trw / "memory").mkdir(parents=True)
    corpus.note_preexisting_trash(trw)
    (trw / "trash").mkdir()  # made by this run

    corpus.keep_memory_in_dir(trw, tmp_path, lambda p, _t: str(p))

    assert not (trw / "trash").exists()


# --- MINOR: cleanup failures are logged, a partial write is cleaned up -------------------------------------------


def test_a_failed_fresh_dir_cleanup_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
) -> None:
    from trw_mcp.bootstrap import _proven_replace

    real_rmdir = os.rmdir

    def denied(path: Any, *args: Any, **kwargs: Any) -> None:
        if path == "trash":
            raise PermissionError(errno.EACCES, "Permission denied")
        real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(_proven_replace.os, "rmdir", denied)

    assert _proven_replace.create_exclusive(tmp_path / "AGENTS.md", tmp_path, b"x\n").status == "replaced"

    assert any(e.get("event") == "proven_replace_cleanup_failed" for e in captured_structlog), captured_structlog


def test_a_partial_write_that_runs_out_of_space_leaves_the_target_and_no_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _proven_replace

    real_write = os.write
    calls = {"n": 0}

    def half_then_full(fd: int, data: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            return real_write(fd, bytes(data[: max(1, len(data) // 2)]))
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(_proven_replace.os, "write", half_then_full)
    target = tmp_path / "AGENTS.md"
    target.write_bytes(b"user\n")

    replaced = _proven_replace.replace_proven(target, tmp_path, b"user\n", b"user\n" + b"more\n" * 50)

    assert (replaced.status, replaced.failed) == ("refused", True)
    assert target.read_bytes() == b"user\n"
    assert calls["n"] == 2
    assert not (tmp_path / ".trw").exists(), "the stage and the directories made for it are all gone"
