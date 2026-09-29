"""`.cursor/rules/trw-ceremony.mdc` is one file serving two clients.

``generate_cursor_rules_mdc`` documents that it "always rewrites the file" while
being called with two different bodies: the cursor-ide body (shared protocol +
``_CURSOR_IDE_APPENDIX``) and the cursor-cli body (a light section, no
appendix). On ``init-project --ide all`` the cursor-cli write landed second, so
the shipped ``alwaysApply: true`` carrier was 50 lines instead of 158 — the
trigger-phrase table, verification-pass gate, drift-recovery hints, Plan Mode
note and pre-compaction reminder were all gone, and BOTH writes were reported as
success. One ``update-project`` restored it, because the update path has only
the cursor-ide writer.

Two layers are pinned here, deliberately: the caller no longer issues the
redundant write, and the writer refuses to be the operation that removes the
richer body whatever a future caller does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._cursor import _CURSOR_IDE_APPENDIX, generate_cursor_rules_mdc
from trw_mcp.bootstrap._init_project_ide import _install_cursor_artifacts
from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH, LINK_BODY

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_RULES_REL = ".cursor/rules/trw-ceremony.mdc"


def _rules_path(root: Path) -> Path:
    return root / ".cursor" / "rules" / "trw-ceremony.mdc"


@pytest.mark.integration
def test_dual_surface_install_keeps_the_cursor_ide_appendix(tmp_path: Path) -> None:
    """`--ide all` must leave the IDE body on disk, not the CLI body."""
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    _install_cursor_artifacts(
        tmp_path,
        force=False,
        result=result,
        ide_targets=["cursor-ide", "cursor-cli"],
    )

    content = _rules_path(tmp_path).read_text(encoding="utf-8")
    assert "TRW Trigger Phrases" in content
    assert "Verification Pass" in content
    assert "trw_checkpoint(pre_compact=True)" in content
    # The measured regression was 50 lines where 158 belonged; assert the
    # appendix arrived whole rather than pinning a line count that legitimate
    # protocol edits would churn.
    assert _CURSOR_IDE_APPENDIX.strip() in content
    # The duplicate write also double-reported the same path as created.
    assert result["created"].count(_RULES_REL) == 1
    # And the redundant call is not made at all: nothing about this file was
    # skipped or preserved, because only one caller ever wrote it.
    assert _RULES_REL not in result["skipped"]
    # The IDE body is cursor-ide's carrier; the cursor-cli retirement must leave it.
    assert _RULES_REL not in result.get("removed", [])


@pytest.mark.integration
def test_cli_only_install_carries_the_protocol_behind_the_agents_md_link_only(tmp_path: Path) -> None:
    """PRD-CORE-301-FR14: cursor-cli auto-loads .cursor/rules AND AGENTS.md, so it gets one carrier.

    Before FR14 a cursor-cli-only install wrote the protocol into both (about 5,400
    tokens per session). AGENTS.md is kept: cursor documents that its CLI reads it.
    Since PRD-CORE-341 AGENTS.md holds the link and ``.trw/INSTRUCTIONS.md`` the protocol.
    """
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    _install_cursor_artifacts(tmp_path, force=False, result=result, ide_targets=["cursor-cli"])

    assert not _rules_path(tmp_path).exists()
    assert _RULES_REL not in result["created"]
    assert LINK_BODY in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "trw_session_start" in (tmp_path / INSTRUCTIONS_RELPATH).read_text(encoding="utf-8")


def _pre_fr14_cli_rule(root: Path) -> str:
    """Write the rule file exactly as a pre-FR14 cursor-cli install did; return its text."""
    from trw_mcp.bootstrap._cursor_cli import _cursor_cli_trw_section

    generate_cursor_rules_mdc(root, _cursor_cli_trw_section(), client_id="cursor-cli")
    return _rules_path(root).read_text(encoding="utf-8")


_NOTICE = f"retired_artifact_present: {_RULES_REL}"


def _cursor_project(root: Path, ide: str) -> Path:
    from trw_mcp.bootstrap import init_project

    (root / ".git").mkdir(parents=True)
    assert init_project(root, ide=ide)["errors"] == []
    return root


@pytest.mark.integration
def test_update_reports_but_never_deletes_a_pre_fr14_cli_rule(tmp_path: Path) -> None:
    """An older cursor-cli copy is named with its removal command; the file is left exactly as it was."""
    from trw_mcp.bootstrap import update_project
    from trw_mcp.bootstrap._retired_artifacts import retired_artifact_row

    repo = _cursor_project(tmp_path, "cursor-cli")
    before = _pre_fr14_cli_rule(repo)

    notices = [w for w in update_project(repo, ide="cursor-cli")["warnings"] if _NOTICE in w]

    assert len(notices) == 1 and "AGENTS.md" in notices[0] and "rm " in notices[0]
    assert _rules_path(repo).read_text(encoding="utf-8") == before
    status, message = retired_artifact_row(repo)
    assert status == "WARN" and _RULES_REL in message


@pytest.mark.integration
def test_the_cursor_ide_rule_file_is_never_reported(tmp_path: Path) -> None:
    """cursor-ide's carrier is not retired: not when recorded, and not by its appendix alone."""
    from trw_mcp.bootstrap import update_project
    from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices

    ide_repo = _cursor_project(tmp_path / "ide", "cursor-ide")
    assert not [w for w in update_project(ide_repo, ide="cursor-ide")["warnings"] if _NOTICE in w]

    # A cursor-cli project holding the IDE body (cursor-ide was used once, never recorded) keeps it too.
    cli_repo = _cursor_project(tmp_path / "cli", "cursor-cli")
    generate_cursor_rules_mdc(cli_repo, "SHARED SECTION", client_id="cursor-ide")
    assert not [n for n in retired_artifact_notices(cli_repo) if _NOTICE in n]

    # Nor after a CRLF conversion (core.autocrlf on Windows) of that IDE body.
    rules = _rules_path(cli_repo)
    rules.write_bytes(rules.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert not [n for n in retired_artifact_notices(cli_repo) if _NOTICE in n]


@pytest.mark.integration
def test_writer_refuses_to_drop_an_existing_ide_appendix(tmp_path: Path) -> None:
    """The mechanism guard: a CLI-body write can never strip the IDE body."""
    generate_cursor_rules_mdc(tmp_path, "SHARED SECTION", client_id="cursor-ide")
    before = _rules_path(tmp_path).read_text(encoding="utf-8")

    result = generate_cursor_rules_mdc(tmp_path, "CLI SECTION", client_id="cursor-cli")

    assert _rules_path(tmp_path).read_text(encoding="utf-8") == before
    assert _RULES_REL in result.get("preserved", [])
    assert _RULES_REL not in result.get("updated", [])
    assert _RULES_REL not in result.get("created", [])


@pytest.mark.integration
def test_cli_write_still_lands_when_there_is_no_appendix_to_lose(tmp_path: Path) -> None:
    """Non-vacuity for the guard: it fires on downgrade only, not on every write."""
    rules_dir = tmp_path / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "trw-ceremony.mdc").write_text("stale CLI body", encoding="utf-8")

    result = generate_cursor_rules_mdc(tmp_path, "FRESH CLI SECTION", client_id="cursor-cli")

    content = _rules_path(tmp_path).read_text(encoding="utf-8")
    assert "FRESH CLI SECTION" in content
    assert "stale CLI body" not in content
    assert _RULES_REL in result.get("updated", [])


@pytest.mark.integration
def test_ide_write_still_refreshes_an_existing_ide_file(tmp_path: Path) -> None:
    """Precision: the guard must not freeze the file it protects.

    Refusing every write to a file carrying the appendix would stop
    ``update-project`` from ever refreshing the protocol — the failure mode the
    ``_managed_client_artifacts`` docstring calls "frozen at first-installed
    content forever".
    """
    generate_cursor_rules_mdc(tmp_path, "OLD SECTION", client_id="cursor-ide")

    result = generate_cursor_rules_mdc(tmp_path, "NEW SECTION", client_id="cursor-ide")

    content = _rules_path(tmp_path).read_text(encoding="utf-8")
    assert "NEW SECTION" in content
    assert "OLD SECTION" not in content
    assert _CURSOR_IDE_APPENDIX.strip() in content
    assert _RULES_REL in result.get("updated", [])
