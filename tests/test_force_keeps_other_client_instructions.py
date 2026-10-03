"""``init-project --force`` refreshes only TRW's block in other clients' instruction files (FORCE-WHOLESALE-OTHER-CLIENTS).

CLAUDE-MD S1 made ``--force`` refresh only TRW's block in ``CLAUDE.md`` and ``AGENTS.md``. The shared writer behind
``.github/copilot-instructions.md`` and ``ANTIGRAVITY.md`` still replaced the whole file under ``--force``, so a
user's own instructions there were lost (a backup was taken, but the live file held TRW's block alone).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ._copilot_test_support import fake_git_repo  # noqa: F401

_USER_PROSE = "# Team rules\r\nAlways run make check.\r\nNever push to main.\r\n"

_CLIENTS = [
    pytest.param("copilot", ".github/copilot-instructions.md", "<!-- trw:copilot:start -->", id="copilot"),
    pytest.param("antigravity-cli", "ANTIGRAVITY.md", "<!-- trw:antigravity:start -->", id="antigravity"),
]


@pytest.mark.parametrize(("ide", "rel_path", "start_marker"), _CLIENTS)
def test_force_keeps_the_users_text_and_adds_one_trw_block(
    fake_git_repo: Path, ide: str, rel_path: str, start_marker: str
) -> None:
    from trw_mcp.bootstrap import init_project

    target = fake_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_USER_PROSE.encode())

    result = init_project(fake_git_repo, force=True, ide=ide)

    assert not [e for e in result["errors"] if rel_path in e], result["errors"]
    text = target.read_bytes()
    assert text.startswith(_USER_PROSE.encode()), "every byte the user wrote stays, line endings included"
    assert text.count(start_marker.encode()) == 1


@pytest.mark.parametrize(("ide", "rel_path", "start_marker"), _CLIENTS)
def test_force_replaces_an_old_trw_block_and_keeps_text_around_it(
    fake_git_repo: Path, ide: str, rel_path: str, start_marker: str
) -> None:
    from trw_mcp.bootstrap import init_project

    end_marker = start_marker.replace(":start", ":end")
    before, after = "# Mine above\n\n", "\n# Mine below\nKeep this.\n"
    target = fake_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{before}{start_marker}\nold TRW text\n{end_marker}{after}", encoding="utf-8")

    result = init_project(fake_git_repo, force=True, ide=ide)

    assert not [e for e in result["errors"] if rel_path in e], result["errors"]
    text = target.read_text(encoding="utf-8")
    assert text.startswith(before) and text.endswith(after)
    assert "old TRW text" not in text, "TRW's own block is TRW's to refresh"
    assert text.count(start_marker) == 1 and text.count(end_marker) == 1


@pytest.mark.parametrize(("ide", "rel_path", "start_marker"), _CLIENTS)
@pytest.mark.parametrize(
    "layout",
    [
        "{s}\nmine\n",
        "mine\n{e}\n",
        "{s}\na\n{e}\nmine\n{s}\nb\n{e}\n",
        "{s}\nmy section\n{s}\nb\n{e}\nmy tail\n",
        "```\n{s}\nexample\n{e}\n```\n",
        "Example:\n\n    {s}\n    my example\n    {e}\n",
    ],
    ids=["start-only", "end-only", "two-blocks", "nested", "fenced-example", "indented-example"],
)
def test_force_leaves_a_file_with_ambiguous_markers_as_found(
    fake_git_repo: Path, ide: str, rel_path: str, start_marker: str, layout: str
) -> None:
    """Under --force too: TRW cannot tell its block from the user's text, so it refuses and writes nothing."""
    from trw_mcp.bootstrap import init_project

    original = layout.format(s=start_marker, e=start_marker.replace(":start", ":end")).encode()
    target = fake_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(original)

    result = init_project(fake_git_repo, force=True, ide=ide)

    assert target.read_bytes() == original
    assert [e for e in result["errors"] if rel_path in e and "ambiguous_markers" in e], result["errors"]


@pytest.mark.parametrize(("ide", "rel_path", "start_marker"), _CLIENTS)
def test_force_keeps_mixed_line_endings_byte_for_byte(
    fake_git_repo: Path, ide: str, rel_path: str, start_marker: str
) -> None:
    from trw_mcp.bootstrap import init_project

    original = b"# Mine\r\nline two\nline three\r\nno final newline"
    target = fake_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(original)

    result = init_project(fake_git_repo, force=True, ide=ide)

    assert not [e for e in result["errors"] if rel_path in e], result["errors"]
    text = target.read_bytes()
    assert text.startswith(original), "the user's bytes are a prefix of the result, mixed endings untouched"
    assert text.count(start_marker.encode()) == 1


@pytest.mark.parametrize(("ide", "rel_path", "start_marker"), _CLIENTS)
def test_force_never_writes_through_a_symlinked_instruction_file(
    fake_git_repo: Path, tmp_path_factory: pytest.TempPathFactory, ide: str, rel_path: str, start_marker: str
) -> None:
    from trw_mcp.bootstrap import init_project

    outside = tmp_path_factory.mktemp("outside") / "shared-instructions.md"
    outside.write_bytes(b"# Shared team file\n")
    target = fake_git_repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)

    init_project(fake_git_repo, force=True, ide=ide)

    assert outside.read_bytes() == b"# Shared team file\n", "the file the link points at is never written"
    assert target.is_symlink(), "the link itself is left in place"


def test_claude_md_with_an_indented_marker_example_is_left_as_found(fake_git_repo: Path) -> None:
    """CLAUDE-MD-S1-r2 known issue (_template_claude_md.py): an indented example was replaced as TRW's block."""
    from trw_mcp.bootstrap import init_project

    original = b"# Mine\n\nHow TRW marks its block:\n\n    <!-- trw:start -->\n    my example\n    <!-- trw:end -->\n"
    claude_md = fake_git_repo / "CLAUDE.md"
    claude_md.write_bytes(original)

    result = init_project(fake_git_repo, force=True, ide="claude-code")

    assert claude_md.read_bytes() == original
    assert [w for w in result["errors"] + result["warnings"] if "CLAUDE.md" in w and "indented" in w], result
