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
    assert "trw_pre_compact_checkpoint" in content
    # The measured regression was 50 lines where 158 belonged; assert the
    # appendix arrived whole rather than pinning a line count that legitimate
    # protocol edits would churn.
    assert _CURSOR_IDE_APPENDIX.strip() in content
    # The duplicate write also double-reported the same path as created.
    assert result["created"].count(_RULES_REL) == 1
    # And the redundant call is not made at all: nothing about this file was
    # skipped or preserved, because only one caller ever wrote it.
    assert _RULES_REL not in result["skipped"]
    assert any("cursor-cli shares the cursor-ide rule body" in line for line in result.get("info", []))


@pytest.mark.integration
def test_cli_only_install_still_writes_the_rule_file(tmp_path: Path) -> None:
    """Non-vacuity: skipping the CLI write on dual-surface must not delete it.

    A cursor-cli-only project has no IDE writer, so this is the only thing that
    puts a rule file in ``.cursor/rules`` — the surface PRD-CORE-137 added after
    the profile had wrongly described AGENTS.md as the CLI's only carrier.
    """
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    _install_cursor_artifacts(tmp_path, force=False, result=result, ide_targets=["cursor-cli"])

    content = _rules_path(tmp_path).read_text(encoding="utf-8")
    assert "alwaysApply: true" in content
    assert "trw_session_start" in content
    assert "TRW Trigger Phrases" not in content
    assert _RULES_REL in result["created"]


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
