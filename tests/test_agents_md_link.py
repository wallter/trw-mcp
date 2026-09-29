"""PRD-CORE-341: AGENTS.md links TRW's instructions by import; no learnings in any instruction file.

TRW used to write its whole instruction block, plus up to five store-derived
learning summaries, into the project's own AGENTS.md. The block now lives in the
TRW-owned ``.trw/INSTRUCTIONS.md``; AGENTS.md carries a two-line link to it (a
sentence for clients that do not expand imports, and the ``@`` import Claude
Code expands). Learnings reach an agent on demand (session start, trw_recall,
the prompt hook, edit hints), never through an instruction file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from trw_mcp.models.config import TRWConfig, reload_config

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

IMPORT_LINE = "@.trw/INSTRUCTIONS.md"
INSTRUCTIONS = ".trw/INSTRUCTIONS.md"
HEADER_PREFIX = "<!-- TRW AUTO-GENERATED — do not edit."
START, END = "<!-- trw:start -->", "<!-- trw:end -->"
GATE = "## Deliver Gate"
DELEGATION_POINTER = "Delegation decisions and file ownership"
TOKEN = "zebrapool"
STALE = "build_check_result=pass"

_OLD_BLOCK = (
    "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
    f"{START}\n\n## Workflow\n\n1. **Start**: call `trw_session_start()`\n\n"
    f"## Key Learnings\n\n- The {TOKEN} pool exhausts under load\n{END}\n"
)
_ABOVE = "# My project\n\nHand-written rules above.\n\n"
_BELOW = "\n## Below\n\nHand-written rules below.\n"


def _span(text: str) -> list[str]:
    """Non-blank lines strictly between the TRW markers (whole-line match)."""
    lines = text.splitlines()
    start = lines.index(START)
    end = lines.index(END, start)
    return [line for line in lines[start + 1 : end] if line.strip()]


def _assert_link_form(root: Path) -> None:
    span = _span((root / "AGENTS.md").read_text(encoding="utf-8"))
    assert len(span) == 2, span
    assert INSTRUCTIONS in span[0] and "read that file" in span[0]
    assert span[1] == IMPORT_LINE


def _assert_generated_file(root: Path) -> str:
    text = (root / INSTRUCTIONS).read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith(HEADER_PREFIX)
    assert text.count(GATE) == 1
    assert "Key Learnings" not in text and TOKEN not in text and STALE not in text
    return text


def _sync(root: Path, client: str = "claude-code", *, dry_run: bool = False, force: bool = True) -> dict:
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state.claude_md import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    cwd = os.getcwd()
    os.chdir(root)
    _reset_config()
    try:
        return dict(
            execute_claude_md_sync(
                "root", str(root), get_config(), FileStateReader(), None, client, dry_run=dry_run, force=force
            )
        )
    finally:
        os.chdir(cwd)
        _reset_config()


def _project(root: Path) -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".trw").mkdir(exist_ok=True)
    return root


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts and "backups" not in p.parts
    }


# ── FR03 / FR04: every AGENTS.md writer emits the link ─────────────────────────


@pytest.mark.parametrize("writer", ["sync", "init-claude-code", "init-grok", "init-cursor-cli"])
def test_every_agents_md_writer_emits_the_link(writer: str, tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project

    root = _project(tmp_path)
    if writer == "sync":
        _sync(root)
    else:
        assert init_project(root, ide=writer.removeprefix("init-"))["errors"] == []

    _assert_link_form(root)
    _assert_generated_file(root)


_AGENTS_READERS = {"claude-code", "cursor-cli", "grok"}


@pytest.mark.parametrize(
    "ide", ["claude-code", "cursor-cli", "grok", "codex", "opencode", "cursor-ide", "copilot", "antigravity-cli"]
)
def test_per_client_forms(ide: str, tmp_path: Path) -> None:
    """FR04 table: AGENTS.md readers get the link; clients with their own carrier keep the full block."""
    from tests import _protected_blocks as pb
    from trw_mcp.bootstrap import init_project

    root = _project(tmp_path)
    assert init_project(root, ide=ide)["errors"] == []

    agents = root / "AGENTS.md"
    if ide in _AGENTS_READERS:
        _assert_link_form(root)
        _assert_generated_file(root)
        return
    if agents.is_file() and START in agents.read_text(encoding="utf-8"):
        _assert_link_form(root)
    for rel in pb.CARRIERS[ide]:
        text = (root / rel).read_text(encoding="utf-8")
        assert "Key Learnings" not in text, rel
    assert any(GATE in (root / rel).read_text(encoding="utf-8") for rel in pb.CARRIERS[ide])


# ── FR02: the generated file ──────────────────────────────────────────────────


def test_instructions_file_is_the_generated_block(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _sync(root)

    text = _assert_generated_file(root)
    assert "## Workflow" in text
    assert DELEGATION_POINTER in text, "the default claude-code render carries the delegation pointer"


# ── FR05: idempotent, honest dry run ──────────────────────────────────────────


def test_sync_is_idempotent_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "AGENTS.md").write_text(_ABOVE + _OLD_BLOCK + _BELOW, encoding="utf-8")

    before = _tree(root)
    result = _sync(root, dry_run=True)
    assert _tree(root) == before, "a dry run wrote something"
    diffed = {Path(d["file"]).as_posix() for d in result.get("diffs") or []}
    assert any(p.endswith("/AGENTS.md") for p in diffed), diffed
    assert any(p.endswith(INSTRUCTIONS) for p in diffed), diffed

    _sync(root)
    first = {k: v for k, v in _tree(root).items() if k in ("AGENTS.md", INSTRUCTIONS)}
    _sync(root)
    second = {k: v for k, v in _tree(root).items() if k in ("AGENTS.md", INSTRUCTIONS)}
    assert len(first) == 2 and first == second


# ── FR06: migration ───────────────────────────────────────────────────────────


def test_migrates_old_block_and_keeps_hand_written_text(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "AGENTS.md").write_text(_ABOVE + _OLD_BLOCK + _BELOW, encoding="utf-8")
    stale = f"{HEADER_PREFIX} Imported via @x (PRD-CORE-203). -->\n(a) `{STALE}`\n\nMy note below the header.\n"
    (root / INSTRUCTIONS).write_text(stale, encoding="utf-8")

    _sync(root)

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith(_ABOVE), "text above the block changed"
    # The shared merge normalises the file's final newline (pre-existing); every line is kept.
    assert agents.rstrip("\n").endswith(_BELOW.rstrip("\n")), "text below the block changed"
    assert "Key Learnings" not in agents and TOKEN not in agents
    _assert_link_form(root)
    _assert_generated_file(root)
    backups = [p for p in (root / ".trw").rglob("*") if p.is_file() and "backups" in p.parts]
    assert any(p.read_text(encoding="utf-8") == stale for p in backups), "the stale file was overwritten without a copy"
    assert not list((root / ".trw").glob("INSTRUCTIONS.md.retired*"))


# ── FR07 / NFR02: a user-authored file is never overwritten ───────────────────


def test_user_authored_instructions_file_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path)
    agents_text = _ABOVE + _OLD_BLOCK + _BELOW
    (root / "AGENTS.md").write_text(agents_text, encoding="utf-8")
    mine = "# My own notes\n\nNever deploy on Fridays.\n"
    (root / INSTRUCTIONS).write_text(mine, encoding="utf-8")

    result = _sync(root, force=False)

    assert (root / INSTRUCTIONS).read_text(encoding="utf-8") == mine
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == agents_text
    refusals = result.get("refusals") or []
    assert any(r["reason"] == "user_authored" and r["file"].endswith(INSTRUCTIONS) for r in refusals), refusals


# ── FR08: no retire loop ──────────────────────────────────────────────────────


def test_update_project_never_retires_or_undoes_the_instructions_file(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert init_project(tmp_path, ide="claude-code")["errors"] == []
    stale = f"{HEADER_PREFIX} Imported via @x (PRD-CORE-203). -->\n(a) `{STALE}`\n"
    (tmp_path / INSTRUCTIONS).write_text(stale, encoding="utf-8")

    for run in (1, 2):
        assert update_project(tmp_path, ide="claude-code")["errors"] == [], run
        assert not list((tmp_path / ".trw").glob("INSTRUCTIONS.md.retired*")), run
        _assert_generated_file(tmp_path)
        _assert_link_form(tmp_path)


# ── FR09: withdrawal leaves AGENTS.md alone ───────────────────────────────────


def test_withdraw_no_longer_edits_agents_md(tmp_path: Path) -> None:
    from trw_mcp.state.claude_md._withdraw import withdraw_managed_learnings

    root = _project(tmp_path)
    agents_text = _ABOVE + _OLD_BLOCK + _BELOW
    (root / "AGENTS.md").write_text(agents_text, encoding="utf-8")

    withdraw_managed_learnings(root / ".trw", root, TRWConfig(learning_recall_enabled=False))

    assert (root / "AGENTS.md").read_text(encoding="utf-8") == agents_text


# ── FR01 / NFR01: no learning content, store-independent bytes ────────────────


def _instruction_files(root: Path) -> dict[str, bytes]:
    """Every file sync writes for an agent to read as instructions (REVIEW.md is out of scope)."""
    return {
        rel: data
        for rel, data in _tree(root).items()
        if rel != "REVIEW.md" and (not rel.startswith(".trw/") or rel == INSTRUCTIONS)
    }


def test_sync_output_is_store_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon
) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    root = _project(tmp_path)
    trw_dir = root / ".trw"
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    monkeypatch.setenv("HOME", str(root / ".home"))
    for key in ("TRW_DEDUP_ENABLED", "TRW_EMBEDDINGS_ENABLED"):
        monkeypatch.setenv(key, "false")
    attach_checkout(trw_dir, memory_daemon)
    try:
        reload_config()
        _sync(root, client="all")
        empty = _instruction_files(root)

        tags = ["gotcha", "bug", "security", "type-safety", "testing", "pattern"]
        store_learning(trw_dir, "L-probe", f"The {TOKEN} pool exhausts", "Raise it.", tags=tags, impact=0.9)
        _sync(root, client="all")
        populated = _instruction_files(root)
    finally:
        reload_config()

    assert TOKEN in (root / "REVIEW.md").read_text(encoding="utf-8"), "non-vacuity: the store was readable"
    assert INSTRUCTIONS in populated and "AGENTS.md" in populated
    assert populated == empty
    assert not [rel for rel, data in populated.items() if TOKEN.encode() in data]


def test_sub_scope_section_carries_no_shared_learnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from trw_mcp.state.claude_md import _static_sections
    from trw_mcp.state.claude_md._profile_render import render_profile_section

    entry = SimpleNamespace(content=f"org {TOKEN} rule", detail=f"{TOKEN} detail")
    monkeypatch.setattr(_static_sections, "list_org_shared_entries", lambda *_a, **_k: [entry], raising=False)
    root = _project(tmp_path)
    (root / "sub").mkdir()

    section = render_profile_section(root / ".trw", root, TRWConfig(), root / "sub" / "AGENTS.md", scope="sub")

    assert TOKEN not in section
    assert "Shared Learnings" not in section


def test_learning_injection_surface_is_gone() -> None:
    import json
    from importlib.resources import files

    from trw_mcp.models.typed_dicts import _ceremony
    from trw_mcp.state import analytics

    retired_keys = ("agents_md_learning_injection", "agents_md_learning_max", "agents_md_learning_min_impact")
    retired = json.loads((files("trw_mcp.data") / "config-retired-keys.json").read_text(encoding="utf-8"))["retired"]
    for key in retired_keys:
        assert key not in TRWConfig.model_fields, key
        assert key in retired, key
    assert not hasattr(analytics, "mark_promoted")
    annotations = {
        name
        for cls in (_ceremony._ClaudeMdSyncResultRequired, _ceremony.ClaudeMdSyncResultDict)
        for name in cls.__annotations__
    }
    assert "learnings_promoted" not in annotations
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    assert not (src / "state/claude_md/_sidecar_retire.py").exists()
