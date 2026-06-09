"""Tests for shared Cursor MCP config and rules bootstrap helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.integration
def test_cursor_mcp_config_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_mcp_config creates .cursor/mcp.json on first call."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    result = generate_cursor_mcp_config(tmp_path)
    mcp_file = tmp_path / ".cursor" / "mcp.json"

    assert mcp_file.is_file()
    data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "trw" in data["mcpServers"]
    assert ".cursor/mcp.json" in result.get("created", [])


@pytest.mark.integration
def test_cursor_mcp_config_smart_merge_preserves_user_servers(tmp_path: Path) -> None:
    """generate_cursor_mcp_config preserves existing non-TRW server entries."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    mcp_file = cursor_dir / "mcp.json"
    mcp_file.write_text(
        json.dumps({"mcpServers": {"my-server": {"command": "my-server-bin"}}}),
        encoding="utf-8",
    )

    result = generate_cursor_mcp_config(tmp_path)
    data = json.loads(mcp_file.read_text(encoding="utf-8"))

    assert "trw" in data["mcpServers"]
    assert "my-server" in data["mcpServers"]
    assert ".cursor/mcp.json" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_mcp_config_malformed_json_overwrites(tmp_path: Path) -> None:
    """generate_cursor_mcp_config overwrites malformed mcp.json with fresh content."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text("not valid {{{", encoding="utf-8")

    result = generate_cursor_mcp_config(tmp_path)
    data = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))

    assert "trw" in data["mcpServers"]
    assert ".cursor/mcp.json" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_mcp_config_non_object_json_overwrites(tmp_path: Path) -> None:
    """A mcp.json whose top level is an array (not an object) no longer crashes.

    Before the read_json_object seam, ``existing.get("mcpServers", {})`` raised
    AttributeError on a list top level (only JSONDecodeError/KeyError were caught).
    """
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    result = generate_cursor_mcp_config(tmp_path)
    data = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))

    assert "trw" in data["mcpServers"]
    assert ".cursor/mcp.json" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_mcp_config_non_utf8_overwrites(tmp_path: Path) -> None:
    """A non-UTF-8 mcp.json no longer raises UnicodeDecodeError; it is overwritten fresh."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_bytes(b"\xff\xfe\x00 invalid")

    result = generate_cursor_mcp_config(tmp_path)
    data = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))

    assert "trw" in data["mcpServers"]
    assert ".cursor/mcp.json" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_mcp_config_tolerates_non_dict_mcpservers(tmp_path: Path) -> None:
    """A ``mcpServers`` value that is not a dict is replaced rather than crashing."""
    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    (cursor_dir / "mcp.json").write_text(
        json.dumps({"mcpServers": ["not", "a", "dict"], "keepKey": True}),
        encoding="utf-8",
    )

    result = generate_cursor_mcp_config(tmp_path)
    data = json.loads((cursor_dir / "mcp.json").read_text(encoding="utf-8"))

    assert "trw" in data["mcpServers"]
    # Unrelated top-level keys survive the merge.
    assert data.get("keepKey") is True
    assert ".cursor/mcp.json" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_mcp_config_no_raw_payload_in_logs(tmp_path: Path) -> None:
    """Structural diagnostics must not leak the malformed file's raw bytes.

    A malformed mcp.json that happens to hold a secret must produce only a
    content-free category diagnostic from the shared seam.
    """
    import structlog

    from trw_mcp.bootstrap._cursor import generate_cursor_mcp_config

    cursor_dir = tmp_path / ".cursor"
    cursor_dir.mkdir()
    secret = "sk-super-secret-token-1234567890"
    (cursor_dir / "mcp.json").write_text("not valid json " + secret, encoding="utf-8")

    with structlog.testing.capture_logs() as logs:
        generate_cursor_mcp_config(tmp_path)

    rendered = json.dumps(logs)
    assert secret not in rendered
    # The seam still records that a malformed config was seen, by category only.
    assert any(entry.get("reason") == "malformed_json" for entry in logs)


@pytest.mark.integration
def test_cursor_rules_mdc_fresh_write(tmp_path: Path) -> None:
    """generate_cursor_rules_mdc creates .cursor/rules/trw-ceremony.mdc on first call."""
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    result = generate_cursor_rules_mdc(tmp_path, "TRW ceremony content", client_id="cursor-ide")
    rules_file = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"

    assert rules_file.is_file()
    content = rules_file.read_text(encoding="utf-8")
    assert "alwaysApply: true" in content
    assert "TRW ceremony content" in content
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("created", [])


@pytest.mark.integration
def test_cursor_rules_mdc_updates_existing(tmp_path: Path) -> None:
    """generate_cursor_rules_mdc updates the file when it already exists."""
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    rules_dir = tmp_path / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "trw-ceremony.mdc").write_text("old content", encoding="utf-8")

    result = generate_cursor_rules_mdc(tmp_path, "new content")
    content = (rules_dir / "trw-ceremony.mdc").read_text(encoding="utf-8")

    assert "new content" in content
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("updated", [])


@pytest.mark.integration
def test_cursor_rules_mdc_client_id_cursor_cli(tmp_path: Path) -> None:
    """generate_cursor_rules_mdc accepts cursor-cli as client_id.

    cursor-cli callers do NOT get the IDE-specific appendix (trigger phrases,
    verification-pass, drift-recovery, Plan Mode) — the rule file should
    contain only the shared trw_section for that path.
    """
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    result = generate_cursor_rules_mdc(tmp_path, "CLI content", client_id="cursor-cli")
    rules_file = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"

    assert rules_file.is_file()
    content = rules_file.read_text(encoding="utf-8")
    assert "CLI content" in content
    assert "TRW Trigger Phrases" not in content
    assert "Verification Pass" not in content
    assert "If the Agent Drifts" not in content
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("created", [])


@pytest.mark.integration
def test_cursor_rules_mdc_cursor_ide_includes_appendix(tmp_path: Path) -> None:
    """cursor-ide appendix adds trigger phrases, verification-pass, drift recovery, Plan Mode.

    Per the C3/C7/C8/C10 customizations documented in
    docs/research/providers/cursor/cursor-ide/eval-and-customizations-2026-04-13.md,
    the cursor-ide-rendered rule must contain these sections; cursor-cli must not.
    """
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    generate_cursor_rules_mdc(tmp_path, "SHARED SECTION", client_id="cursor-ide")
    content = (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").read_text(encoding="utf-8")

    assert "SHARED SECTION" in content
    assert "TRW Trigger Phrases" in content
    assert "trw_session_start" in content
    assert "trw_checkpoint" in content
    assert "trw_deliver" in content
    assert "trw_build_check" in content
    assert "trw_review" in content
    assert "Verification Pass" in content
    assert 'trw_build_check(scope="full")' in content
    assert "If the Agent Drifts" in content
    assert "Follow the TRW ceremony protocol" in content
    assert "Planning" in content
    assert "Plan Mode" in content
    assert "trw_pre_compact_checkpoint" in content


@pytest.mark.integration
def test_cursor_rules_mdc_force_overwriting_existing_still_reports_updated(
    tmp_path: Path,
) -> None:
    """Forced overwrite of an existing file reports 'updated', not 'created'.

    Regression test for the previously-inverted ``if existed and not force``
    classification logic. When ``force=True`` and the file existed, the earlier
    branch reported "created" (wrong — the file was actually updated).
    """
    from trw_mcp.bootstrap._cursor import generate_cursor_rules_mdc

    rules_dir = tmp_path / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "trw-ceremony.mdc").write_text("original", encoding="utf-8")

    result = generate_cursor_rules_mdc(tmp_path, "replacement", client_id="cursor-ide", force=True)
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("updated", [])
    assert ".cursor/rules/trw-ceremony.mdc" not in result.get("created", [])


@pytest.mark.integration
def test_cursor_rules_alias_delegates_to_mdc(tmp_path: Path) -> None:
    """generate_cursor_rules (alias) produces the same output as generate_cursor_rules_mdc."""
    from trw_mcp.bootstrap._cursor import generate_cursor_rules

    result = generate_cursor_rules(tmp_path, "alias content")
    rules_file = tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc"

    assert rules_file.is_file()
    assert "alias content" in rules_file.read_text(encoding="utf-8")
    assert ".cursor/rules/trw-ceremony.mdc" in result.get("created", [])
