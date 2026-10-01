"""An upgrade must leave the project consistent: FB-INSTALL-08, -09 and -11 (2026-09-30 second-machine feedback).

08: a retired skill is removed from every client mirror or kept and named, never half-removed by the
    uncommitted-changes guard restoring a copy TRW had just captured into ``.trw/trash``.
09: a git-dirty ``AGENTS.md`` still gets its TRW block refreshed; the user's text outside the markers is
    kept byte for byte.
11: the retired agent-memory list covers every retired ``trw-`` agent, and the removal advice fits what it
    names (a file, an empty directory, a directory of notes).
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from tests._fs_hazards import snapshot_user_bytes
from trw_mcp.bootstrap import init_project, update_project

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_SKILL_ROOTS = (".claude/skills", ".agents/skills", ".github/skills", ".cursor/skills", ".opencode/skills")
_MIRROR_ROOTS = _SKILL_ROOTS[1:]
_RETIRED = "trw-sprint-finish"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _all_client_repo(tmp_path: Path) -> Path:
    """A real git repo with every client installed and committed: the state a prior install left."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="all")["errors"]
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "installed")
    return root


def _plant_old_skill(root: Path, name: str, *, list_in_manifest: bool = True) -> bytes:
    """What a prior TRW version left: the skill in every client's skills dir, recorded as TRW's.

    The manifest records the ``.claude`` key only (``<skill>/SKILL.md``), so the mirrors are proven TRW's by
    the suffix record, exactly the shape of a prior install whose mirror keys were since dropped.
    The ``.claude`` copy and the manifest are committed; the mirrors stay untracked (git-dirty).
    """
    body = (root / ".claude" / "skills" / "trw-reflect" / "SKILL.md").read_bytes()
    for base in _SKILL_ROOTS:
        (root / base / name).mkdir(parents=True)
        (root / base / name / "SKILL.md").write_bytes(body)
    manifest = root / ".trw" / "managed-artifacts.yaml"
    yaml = YAML()
    data = yaml.load(manifest.read_text(encoding="utf-8"))
    if list_in_manifest:
        data["skills"].append(name)
    data["content_hashes"][f"{name}/SKILL.md"] = hashlib.sha256(body).hexdigest()
    with manifest.open("w", encoding="utf-8") as handle:
        yaml.dump(data, handle)
    _git(root, "add", ".claude/skills", ".trw/managed-artifacts.yaml")
    _git(root, "commit", "-qm", f"old install carries {name}")
    return body


def _trashed_paths(root: Path) -> set[str]:
    return {
        json.loads(meta.read_text(encoding="utf-8"))["path"] for meta in (root / ".trw" / "trash").glob("*/meta.json")
    }


# ---- FB-INSTALL-08: retired skills across every client mirror ------------------------------------


def test_a_retired_skill_goes_from_every_client_mirror_even_when_the_mirror_is_git_dirty(tmp_path: Path) -> None:
    root = _all_client_repo(tmp_path)
    body = _plant_old_skill(root, _RETIRED)
    dirty = subprocess.run(["git", "-C", str(root), "status", "--short"], capture_output=True, text=True, check=True)
    assert f"?? .agents/skills/{_RETIRED}/" in dirty.stdout, "fixture: a mirror must be git-dirty"

    result = update_project(root, ide="all")

    assert not result["errors"], result["errors"]
    survivors = [base for base in _SKILL_ROOTS if (root / base / _RETIRED).exists()]
    assert survivors == [], f"retired skill still present in {survivors}"
    assert not [p for p in result["preserved"] if _RETIRED in p and "uncommitted_changes" in p], result["preserved"]
    # HB-2: the removed bytes are recoverable, one capture per client copy.
    assert {f"{base}/{_RETIRED}/SKILL.md" for base in _SKILL_ROOTS} <= _trashed_paths(root)
    assert hashlib.sha256(body).hexdigest() in snapshot_user_bytes(root)
    # A second update neither re-creates a copy nor grows the trash.
    captures = len(list((root / ".trw" / "trash").glob("*/data")))
    assert not update_project(root, ide="all")["errors"]
    assert not [base for base in _SKILL_ROOTS if (root / base / _RETIRED).exists()]
    assert len(list((root / ".trw" / "trash").glob("*/data"))) == captures


def test_an_edited_retired_mirror_is_kept_byte_for_byte_and_named_with_a_remedy(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_retired_artifacts import retired_artifact_row

    root = _all_client_repo(tmp_path)
    _plant_old_skill(root, _RETIRED)
    edited = root / ".github" / "skills" / _RETIRED / "SKILL.md"
    mine = edited.read_bytes() + b"\nMY OWN NOTE: keep this\n"
    edited.write_bytes(mine)

    result = update_project(root, ide="all")

    assert not result["errors"], result["errors"]
    assert edited.read_bytes() == mine, "a user-edited file is never deleted or rewritten"
    # Every other copy went; the edited one is the only survivor.
    assert [base for base in _SKILL_ROOTS if (root / base / _RETIRED).exists()] == [".github/skills"]
    kept = (root / ".github" / "skills" / _RETIRED).resolve()
    (notice,) = [w for w in result["warnings"] if w.startswith("retired_artifact_present:") and _RETIRED in w]
    assert str(kept) in notice, "the notice names the exact path"
    assert shlex.split(notice.split("remove it manually: ", 1)[1]) == ["rm", "-r", str(kept)]
    status, message = retired_artifact_row(root)
    assert status == "WARN" and str(kept) in message
    # And it keeps being named, never re-created elsewhere, on the next update.
    again = update_project(root, ide="all")
    assert edited.read_bytes() == mine
    assert len([w for w in again["warnings"] if w.startswith("retired_artifact_present:") and _RETIRED in w]) == 1
    assert [base for base in _SKILL_ROOTS if (root / base / _RETIRED).exists()] == [".github/skills"]


def test_a_retired_skill_the_project_itself_keeps_is_not_reported_as_dead(tmp_path: Path) -> None:
    """A live ``.claude/skills/<name>`` is the project's own skill; its mirrors follow it and nothing is named."""
    root = _all_client_repo(tmp_path)
    _plant_old_skill(root, _RETIRED)
    own = root / ".claude" / "skills" / _RETIRED / "SKILL.md"
    own.write_bytes(own.read_bytes() + b"\nthis project owns this skill now\n")

    result = update_project(root, ide="all")

    assert own.is_file()
    assert not [w for w in result["warnings"] if w.startswith("retired_artifact_present:") and _RETIRED in w]


def test_trw_decision_is_a_retired_skill_in_every_client_dir() -> None:
    from trw_mcp.bootstrap._version_migration import PREDECESSOR_MAP

    assert "trw-decision" in PREDECESSOR_MAP["skills"]
    assert PREDECESSOR_MAP["skills"]["trw-decision"] is None, "renamed to trw-assess; a clean break, no successor"


def test_trw_decision_goes_from_the_canonical_dir_and_every_mirror_without_a_manifest_listing(tmp_path: Path) -> None:
    root = _all_client_repo(tmp_path)
    _plant_old_skill(root, "trw-decision", list_in_manifest=False)

    result = update_project(root, ide="all")

    assert not result["errors"], result["errors"]
    assert [base for base in _SKILL_ROOTS if (root / base / "trw-decision").exists()] == []


# ---- FB-INSTALL-09: a git-dirty AGENTS.md still gets its TRW block ---------------------------------

_STALE_BLOCK = (
    "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
    "<!-- trw:start -->\n\n"
    "## TRW Behavioral Protocol\n"
    "Tools: trw_session_start, trw_submit_feedback, trw_peers, trw_dispatch, trw_code_search\n"
    + "".join(f"- trw_retired_tool_{i}\n" for i in range(40))
    + "\n<!-- trw:end -->\n"
)
_ABOVE = "# My project\n\nHand-written rules that are mine.\n\n"
_BELOW = "\n## Team notes\nkeep me byte for byte\t \r\nsecond line\n"


@pytest.mark.parametrize("state", ["untracked", "modified"])
def test_a_dirty_agents_md_gets_a_fresh_block_and_keeps_the_users_text_exactly(tmp_path: Path, state: str) -> None:
    root = _all_client_repo(tmp_path)
    agents = root / "AGENTS.md"
    original = (_ABOVE + _STALE_BLOCK + _BELOW).encode("utf-8")
    agents.write_bytes(original)
    if state == "untracked":
        _git(root, "rm", "-q", "--cached", "AGENTS.md")
    else:  # committed with the stale block, then edited again by the user and left uncommitted
        _git(root, "add", "AGENTS.md")
        _git(root, "commit", "-qm", "agents.md with a stale block")
        original += b"<!-- a later uncommitted edit by the user -->\n"
        agents.write_bytes(original)
    porcelain = subprocess.run(
        ["git", "-C", str(root), "status", "--short", "AGENTS.md"], capture_output=True, text=True, check=True
    ).stdout
    assert porcelain.strip(), "fixture: AGENTS.md must be git-dirty"

    result = update_project(root, ide="all")

    assert not result["errors"], result["errors"]
    now = agents.read_bytes()
    assert b"trw_submit_feedback" not in now, "the stale block was not refreshed"
    assert b"@.trw/INSTRUCTIONS.md" in now
    assert "AGENTS.md (uncommitted_changes)" not in result["preserved"]
    # The user's text outside the markers, byte for byte (tab, trailing space and CRLF included).
    head = original.split(b"<!-- TRW AUTO-GENERATED", 1)[0]
    tail = original.rsplit(b"<!-- trw:end -->\n", 1)[1]
    assert now.startswith(head) and now.endswith(tail)
    # HB-2: the dirty pre-run bytes are retained by the instruction guard's backup.
    backups = list((root / ".trw" / "backups" / "instructions").glob("AGENTS.md.*"))
    assert any(b.read_bytes() == original for b in backups), "the pre-run file must be recoverable"


def test_a_dirty_agents_md_the_refresh_would_cost_user_bytes_is_still_restored(tmp_path: Path) -> None:
    """The exemption is for a refresh that changed only TRW's block; any other change is still undone."""
    from trw_mcp.bootstrap._dirty_refresh import refresh_changed_only_trw_block as only_block

    block = "<!-- trw:start -->\nnew\n<!-- trw:end -->\n"
    old = "mine\n<!-- trw:start -->\nold\n<!-- trw:end -->\ntail\n"
    assert only_block(old, "mine\n" + block + "tail\n")
    assert not only_block(old, "MINE\n" + block + "tail\n"), "user text above changed"
    assert not only_block(old, "mine\n" + block + "TAIL\n"), "user text below changed"
    assert not only_block(old, "mine\ntail\n"), "block gone: not provably a refresh"
    assert only_block("mine\n", "mine\n\n" + block), "no block before: a pure append keeps every byte"
    assert not only_block("mine\n", "mi\n\n" + block)
    assert only_block(
        "mine\r\n<!-- trw:start -->\r\nold\r\n<!-- trw:end -->\r\n", "mine\r\n" + block.replace("\n", "\r\n")
    )


# ---- FB-INSTALL-11: retired agent memory ----------------------------------------------------------


def test_every_retired_trw_agent_is_a_retired_memory_dir() -> None:
    from trw_mcp.bootstrap._retired_artifacts import _retired_artifacts

    listed = {rel for rel, _why in _retired_artifacts()}
    for agent in ("trw-code-simplifier", "trw-tester", "trw-requirement-writer", "trw-traceability-checker"):
        assert f".claude/agent-memory/{agent}" in listed, agent
    assert ".claude/agent-memory/trw-implementer" in listed, "a current agent is still listed"
    assert not [rel for rel in listed if "reviewer-" in rel], "a name outside the trw- namespace is the project's"


def test_the_removal_advice_fits_a_file_an_empty_directory_and_a_directory_of_notes(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices

    memory = tmp_path / ".claude" / "agent-memory"
    (memory / "trw-code-simplifier").mkdir(parents=True)  # empty
    notes = memory / "trw-implementer"
    notes.mkdir()
    for index in range(6):
        (notes / f"note-{index}.md").write_text(f"note {index}\n", encoding="utf-8")
    contract = tmp_path / ".opencode" / "skills" / "trw-prd-ready" / "trw-prd-ready-contract.md"
    contract.parent.mkdir(parents=True)
    contract.write_text("legacy\n", encoding="utf-8")

    by_path = {rel: notice for notice in retired_artifact_notices(tmp_path) for rel in [notice.split(" ")[1]]}

    def command(rel: str) -> list[str]:
        return shlex.split(by_path[rel].split("remove it manually: ", 1)[1])

    empty = ".claude/agent-memory/trw-code-simplifier"
    full = ".claude/agent-memory/trw-implementer"
    assert command(empty) == ["rmdir", str((tmp_path / empty).resolve())]
    assert "empty" in by_path[empty]
    assert command(full) == ["rm", "-r", str((tmp_path / full).resolve())]
    assert "holds 6 files" in by_path[full] and "review" in by_path[full], by_path[full]
    assert "holds" not in by_path[".opencode/skills/trw-prd-ready/trw-prd-ready-contract.md"]
    assert command(".opencode/skills/trw-prd-ready/trw-prd-ready-contract.md")[0] == "rm"
    assert all(len(notice.splitlines()) == 1 for notice in by_path.values())


# ---- hazards: ownership signals, unreadable and swapped inputs ------------------------------------


def _skill_dir(root: Path, rel: str, files: dict[str, bytes]) -> Path:
    base = root / rel
    for name, data in files.items():
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        (base / name).write_bytes(data)
    return base


def test_remove_proven_reports_only_the_files_it_actually_captured(tmp_path: Path) -> None:
    """``trashed`` is what keeps the uncommitted-changes guard from restoring a capture; a kept file is not in it."""
    from trw_mcp.bootstrap._version_migration_predecessors import remove_proven

    skill = _skill_dir(tmp_path, ".agents/skills/trw-sprint-init", {"SKILL.md": b"shipped\n", "notes.md": b"mine\n"})
    hashes = {".agents/skills/trw-sprint-init/SKILL.md": hashlib.sha256(b"shipped\n").hexdigest()}
    result: dict[str, list[str]] = {}

    remove_proven(skill, hashes, tmp_path, result)

    assert result["trashed"] == [".agents/skills/trw-sprint-init/SKILL.md"]
    assert (skill / "notes.md").read_bytes() == b"mine\n", "an unrecorded file keeps its bytes and its directory"
    assert any("notes.md" in w and w.endswith(": kept") for w in result["warnings"]), result["warnings"]
    assert not (skill / "SKILL.md").exists()


@pytest.mark.parametrize(
    ("exact_key", "on_disk", "captured"),
    [
        pytest.param(b"shipped\n", b"shipped\n", True, id="exact record matches: captured"),
        pytest.param(
            b"old\n", b"shipped\n", False, id="exact record says the file changed: the suffix record cannot override it"
        ),
        pytest.param(None, b"shipped\n", True, id="no exact record: the suffix record proves it"),
        pytest.param(None, b"edited\n", False, id="no exact record and the suffix digest differs: kept"),
    ],
)
def test_the_most_specific_ownership_record_decides_a_capture(
    tmp_path: Path, exact_key: bytes | None, on_disk: bytes, captured: bool
) -> None:
    from trw_mcp.bootstrap._version_migration_predecessors import remove_proven

    rel = ".github/skills/trw-sprint-init/SKILL.md"
    skill = _skill_dir(tmp_path, ".github/skills/trw-sprint-init", {"SKILL.md": on_disk})
    hashes = {"trw-sprint-init/SKILL.md": hashlib.sha256(b"shipped\n").hexdigest()}
    if exact_key is not None:
        hashes[rel] = hashlib.sha256(exact_key).hexdigest()
    result: dict[str, list[str]] = {}

    remove_proven(skill, hashes, tmp_path, result)

    assert (rel in result.get("trashed", [])) is captured
    assert (not (skill / "SKILL.md").exists()) is captured
    if not captured:
        assert (skill / "SKILL.md").read_bytes() == on_disk


def test_the_agents_md_exemption_refuses_what_it_cannot_prove(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._dirty_refresh import refresh_loses_nothing

    block = b"<!-- trw:start -->\nnew\n<!-- trw:end -->\n"
    good_before = tmp_path / "before.md"
    good_before.write_bytes(b"mine\n<!-- trw:start -->\nold\n<!-- trw:end -->\n")
    good_after = tmp_path / "after.md"
    good_after.write_bytes(b"mine\n" + block)
    assert refresh_loses_nothing("AGENTS.md", good_before, good_after)
    assert not refresh_loses_nothing("CLAUDE.md", good_before, good_after), "only AGENTS.md is exempt"

    not_utf8 = tmp_path / "not-utf8.md"
    not_utf8.write_bytes(b"\xff\xfe mine\n<!-- trw:start -->\nold\n<!-- trw:end -->\n")
    assert not refresh_loses_nothing("AGENTS.md", not_utf8, good_after)

    link = tmp_path / "link.md"
    link.symlink_to(good_after)
    assert not refresh_loses_nothing("AGENTS.md", link, good_after)
    assert not refresh_loses_nothing("AGENTS.md", good_before, link)
    assert not refresh_loses_nothing("AGENTS.md", good_before, tmp_path / "missing.md")


def test_a_user_text_change_the_writer_made_alongside_the_block_keeps_the_guard(tmp_path: Path) -> None:
    """If a refresh ever changed bytes outside the block, the dirty file comes back whole (HB-2)."""
    from trw_mcp.bootstrap._dirty_refresh import refresh_loses_nothing

    before = tmp_path / "before.md"
    before.write_bytes(b"mine\n<!-- trw:start -->\nold\n<!-- trw:end -->\ntail\n")
    after = tmp_path / "after.md"
    after.write_bytes(b"MINE\n<!-- trw:start -->\nnew\n<!-- trw:end -->\ntail\n")
    assert not refresh_loses_nothing("AGENTS.md", before, after)


def test_advice_for_a_symlinked_and_an_unreadable_retired_directory(tmp_path: Path) -> None:
    from tests._fs_hazards import unreadable
    from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices

    memory = tmp_path / ".claude" / "agent-memory"
    memory.mkdir(parents=True)
    elsewhere = tmp_path / "shared-notes"
    elsewhere.mkdir()
    (elsewhere / "keep.md").write_text("not TRW's\n", encoding="utf-8")
    (memory / "trw-tester").symlink_to(elsewhere, target_is_directory=True)
    locked = memory / "trw-requirement-writer"
    locked.mkdir()
    (locked / "note.md").write_text("n\n", encoding="utf-8")

    with unreadable(locked):
        notices = {n.split(" ")[1]: n for n in retired_artifact_notices(tmp_path)}

    link_notice = notices[".claude/agent-memory/trw-tester"]
    assert "symlink" in link_notice and shlex.split(link_notice.split("remove it manually: ", 1)[1])[0] == "rm"
    assert shlex.split(link_notice.split("remove it manually: ", 1)[1]) != ["rm", "-r", str(elsewhere)]
    locked_notice = notices[".claude/agent-memory/trw-requirement-writer"]
    assert "holds" not in locked_notice, "an unreadable directory's contents are not claimed"
    assert shlex.split(locked_notice.split("remove it manually: ", 1)[1])[:2] == ["rm", "-r"]
    assert (elsewhere / "keep.md").read_text(encoding="utf-8") == "not TRW's\n"


def test_remove_proven_leaves_a_symlinked_retired_directory_alone_and_reports_nothing_trashed(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._version_migration_predecessors import remove_proven

    shared = _skill_dir(tmp_path / "shared", "trw-sprint-init", {"SKILL.md": b"shipped\n"})
    link = tmp_path / ".agents" / "skills" / "trw-sprint-init"
    link.parent.mkdir(parents=True)
    link.symlink_to(shared, target_is_directory=True)
    hashes = {"trw-sprint-init/SKILL.md": hashlib.sha256(b"shipped\n").hexdigest()}
    result: dict[str, list[str]] = {}

    remove_proven(link, hashes, tmp_path, result)

    assert link.is_symlink() and (shared / "SKILL.md").read_bytes() == b"shipped\n", "a symlink is never TRW's file"
    assert result.get("trashed", []) == []


@pytest.mark.parametrize(
    ("rel", "start", "end"),
    [
        (".github/copilot-instructions.md", "<!-- trw:copilot:start -->", "<!-- trw:copilot:end -->"),
        ("ANTIGRAVITY.md", "<!-- trw:antigravity:start -->", "<!-- trw:antigravity:end -->"),
    ],
)
def test_the_other_marker_merged_instruction_files_refresh_while_dirty_too(
    tmp_path: Path, rel: str, start: str, end: str
) -> None:
    root = _all_client_repo(tmp_path)
    path = root / rel
    text = path.read_text(encoding="utf-8")
    begin, finish = text.index(start), text.index(end) + len(end)
    above, below = "MY TEXT ABOVE\n\n", "\nMY TEXT BELOW\t\n"
    original = (
        above + text[:begin] + start + "\nSTALE-BLOCK trw_submit_feedback\n" + end + text[finish:] + below
    ).encode()
    path.write_bytes(original)
    _git(root, "rm", "-q", "--cached", rel)

    result = update_project(root, ide="all")

    assert not result["errors"], result["errors"]
    now = path.read_bytes()
    assert b"STALE-BLOCK" not in now, f"{rel}: the stale block was not refreshed"
    assert now.startswith(above.encode()) and now.endswith(below.encode())
    assert f"{rel} (uncommitted_changes)" not in result["preserved"]
    backups = list((root / ".trw" / "backups" / "instructions").glob(f"{path.name}.*"))
    assert any(b.read_bytes() == original for b in backups), "the pre-run file must be recoverable"


def test_a_refresh_that_healed_a_dead_legacy_block_is_not_byte_preserving_and_keeps_the_guard() -> None:
    """Healing the retired ``TRW:BEGIN`` block re-joins the whole file with LF, so it is not provably a block-only refresh."""
    from trw_mcp.bootstrap._dirty_refresh import refresh_changed_only_trw_block as only_block

    block = "<!-- trw:start -->\r\nnew\r\n<!-- trw:end -->\r\n"
    before = (
        "mine\r\n<!-- TRW:BEGIN -->\r\ndead\r\n<!-- TRW:END -->\r\n<!-- trw:start -->\r\nold\r\n<!-- trw:end -->\r\n"
    )
    healed_after = "mine\n\n" + block.replace("\r\n", "\n")
    assert not only_block(before, healed_after)


def test_a_dangling_canonical_skill_link_does_not_hide_a_retained_mirror(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices

    canonical = tmp_path / ".claude" / "skills" / _RETIRED
    canonical.parent.mkdir(parents=True)
    canonical.symlink_to(tmp_path / "gone", target_is_directory=True)  # dangling: no live source
    _skill_dir(tmp_path, f".github/skills/{_RETIRED}", {"SKILL.md": b"edited\n"})

    (notice,) = [n for n in retired_artifact_notices(tmp_path) if _RETIRED in n]

    assert f".github/skills/{_RETIRED}" in notice
