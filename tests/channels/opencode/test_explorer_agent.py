"""Tests for channels/opencode/_explorer_agent.py.

PRD-DIST-2403 FR20-FR24.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch


def test_explorer_agent_content_is_valid_yaml_frontmatter() -> None:
    """FR20: Explorer agent file has valid YAML frontmatter."""
    from trw_mcp.channels.opencode._explorer_agent import get_explorer_agent_content

    content = get_explorer_agent_content()
    assert content.startswith("---\n"), "Must start with YAML frontmatter"
    assert "---" in content[4:], "Must have closing frontmatter delimiter"

    # Extract and parse frontmatter
    parts = content.split("---\n", maxsplit=2)
    assert len(parts) >= 3, "Must have opening ---, frontmatter, closing ---"
    frontmatter_text = parts[1]

    from ruamel.yaml import YAML
    yaml = YAML(typ="safe")
    fm = yaml.load(frontmatter_text)

    assert fm["name"] == "trw-distill-explorer"
    assert "description" in fm
    assert fm["mode"] == "subagent"


def test_explorer_agent_permissions_deny_write_edit() -> None:
    """FR20: Permissions deny bash, edit, write; allow read."""
    from trw_mcp.channels.opencode._explorer_agent import get_explorer_agent_content

    from ruamel.yaml import YAML

    content = get_explorer_agent_content()
    parts = content.split("---\n", maxsplit=2)
    fm = YAML(typ="safe").load(parts[1])

    perms = fm.get("permissions", {})
    assert perms.get("bash") == "deny"
    assert perms.get("edit") == "deny"
    assert perms.get("write") == "deny"
    assert perms.get("read") == "allow"


def test_explorer_agent_three_modes_in_body() -> None:
    """FR21: Body describes all three invocation modes."""
    from trw_mcp.channels.opencode._explorer_agent import get_explorer_agent_content

    content = get_explorer_agent_content()
    # Mode A — single-file
    assert "trw_before_edit_hint" in content
    # Mode B — hotspots
    assert "trw_codebase_risk_report" in content
    # Mode C — conventions (single trw_recall call — P2-09)
    assert "trw_recall" in content


def test_explorer_agent_tools_no_write_edit() -> None:
    """FR22: Body must NOT suggest write or edit operations."""
    from trw_mcp.channels.opencode._explorer_agent import get_explorer_agent_content

    content = get_explorer_agent_content()
    body = content.split("---\n", maxsplit=2)[2] if "---\n" in content else content
    # Body should explicitly say NOT to edit
    assert "Do NOT suggest edits" in body or "Do not suggest edits" in body


def test_explorer_agent_under_quota() -> None:
    """FR24: Explorer agent content is under 8192 bytes."""
    from trw_mcp.channels.opencode._explorer_agent import (
        EXPLORER_AGENT_QUOTA_BYTES,
        get_explorer_agent_content,
    )

    content = get_explorer_agent_content()
    assert len(content.encode("utf-8")) < EXPLORER_AGENT_QUOTA_BYTES


def test_install_explorer_agent_writes_file(tmp_path: Path) -> None:
    """FR20: install_explorer_agent writes file at correct path."""
    from trw_mcp.channels.opencode._explorer_agent import (
        EXPLORER_AGENT_RELPATH,
        install_explorer_agent,
    )

    result = install_explorer_agent(tmp_path)
    assert result["status"] == "written"

    target = tmp_path / EXPLORER_AGENT_RELPATH
    assert target.exists()
    assert target.read_text(encoding="utf-8").startswith("---\n")


def test_install_explorer_agent_user_modified_preserved(tmp_path: Path) -> None:
    """FR23: User-modified explorer agent is preserved (SHA check)."""
    from trw_mcp.channels.opencode._explorer_agent import (
        EXPLORER_AGENT_RELPATH,
        get_explorer_agent_content,
        install_explorer_agent,
    )

    # Install once to get the hash
    result = install_explorer_agent(tmp_path)
    original_sha = str(result["sha256"])

    # Simulate user edit
    target = tmp_path / EXPLORER_AGENT_RELPATH
    user_content = "# User edited this file\nCustom content here\n"
    target.write_text(user_content, encoding="utf-8")

    # Re-install with original SHA — should preserve user edits
    result2 = install_explorer_agent(tmp_path, existing_sha256=original_sha)
    assert result2["status"] == "preserved"
    assert target.read_text(encoding="utf-8") == user_content


def test_install_explorer_agent_no_existing_sha_overwrites(tmp_path: Path) -> None:
    """FR23: When no existing_sha256, file is (re)written."""
    from trw_mcp.channels.opencode._explorer_agent import (
        EXPLORER_AGENT_RELPATH,
        install_explorer_agent,
    )

    # Write some content manually
    target = tmp_path / EXPLORER_AGENT_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old content", encoding="utf-8")

    # Install without hash — should overwrite
    result = install_explorer_agent(tmp_path, existing_sha256=None)
    assert result["status"] == "written"
    assert "old content" not in target.read_text(encoding="utf-8")


def test_install_explorer_agent_write_error_returns_error_status(tmp_path: Path) -> None:
    """FR20: OSError during write returns error status (fail-open)."""
    from trw_mcp.channels.opencode._explorer_agent import install_explorer_agent

    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        result = install_explorer_agent(tmp_path)

    assert result["status"] == "error"
    assert "error" in result
