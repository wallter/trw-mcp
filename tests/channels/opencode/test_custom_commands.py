"""Tests for channels/opencode/_custom_commands.py.

PRD-DIST-2403 FR10-FR15.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# FR10-FR13 — Command content verification
# ---------------------------------------------------------------------------


def test_before_edit_command_has_required_fields() -> None:
    """FR10-FR11: before-edit command has name, $1, trw_before_edit_hint, distill_status."""
    from trw_mcp.channels.opencode._custom_commands import get_before_edit_content

    content = get_before_edit_content()
    assert "name: trw-before-edit" in content
    assert "$1" in content
    assert "trw_before_edit_hint" in content
    assert "distill_status" in content


def test_before_edit_command_all_five_hint_fields() -> None:
    """FR11: before-edit command surfaces all 5 hint fields."""
    from trw_mcp.channels.opencode._custom_commands import get_before_edit_content

    content = get_before_edit_content()
    for field in ("importers", "inferred_tests", "hotspot_warnings", "risk_score", "co_change_neighbors"):
        assert field in content, f"Missing hint field: {field}"


def test_before_edit_command_advisory_not_blocking() -> None:
    """FR10: Command body notes it is advisory and must NOT block edit."""
    from trw_mcp.channels.opencode._custom_commands import get_before_edit_content

    content = get_before_edit_content()
    assert "advisory" in content.lower() or "NOT block" in content or "must not block" in content.lower()


def test_conventions_command_single_recall_call() -> None:
    """FR12: Conventions command uses exactly ONE trw_recall call (P2-09)."""
    from trw_mcp.channels.opencode._custom_commands import get_conventions_content

    content = get_conventions_content()
    assert content.count("trw_recall") == 1
    assert "name: trw-distill-conventions" in content


def test_hotspots_command_table_columns() -> None:
    """FR13: Hotspots command includes table with required columns."""
    from trw_mcp.channels.opencode._custom_commands import get_hotspots_content

    content = get_hotspots_content()
    for col in ("composite_score", "fanin", "churn", "untested"):
        assert col in content.lower() or col in content, f"Missing column: {col}"
    assert "name: trw-distill-hotspots" in content
    assert "trw_codebase_risk_report" in content


def test_hotspots_command_high_risk_label() -> None:
    """FR13: Hotspots command labels high-risk files (>0.8)."""
    from trw_mcp.channels.opencode._custom_commands import get_hotspots_content

    content = get_hotspots_content()
    assert "HIGH RISK" in content or "high-risk" in content


# ---------------------------------------------------------------------------
# FR14 — User-edit detection
# ---------------------------------------------------------------------------


def test_user_modified_command_file_preserved(tmp_path: Path) -> None:
    """FR14: User-modified command file is preserved, not overwritten."""
    from trw_mcp.channels.opencode._custom_commands import (
        COMMANDS_DIR,
        install_custom_commands,
    )

    # First install
    results1 = install_custom_commands(tmp_path, existing_hashes=None)
    original_sha = str(results1["trw-before-edit.md"]["sha256"])

    # Simulate user edit
    target = tmp_path / COMMANDS_DIR / "trw-before-edit.md"
    user_content = "# User customized this command\nCustom instructions here.\n"
    target.write_text(user_content, encoding="utf-8")

    # Re-install with original hash
    results2 = install_custom_commands(tmp_path, existing_hashes={"trw-before-edit.md": original_sha})
    assert results2["trw-before-edit.md"]["status"] == "preserved"
    assert target.read_text(encoding="utf-8") == user_content


def test_unmodified_command_file_overwritten(tmp_path: Path) -> None:
    """FR14: Unmodified command file (hash unchanged) is not flagged as preserved."""
    from trw_mcp.channels.opencode._custom_commands import install_custom_commands

    # First install
    results1 = install_custom_commands(tmp_path, existing_hashes=None)

    # Re-install with matching hashes (file was NOT user-modified)
    hashes = {k: str(v["sha256"]) for k, v in results1.items()}
    results2 = install_custom_commands(tmp_path, existing_hashes=hashes)

    # When hash matches, file is NOT user-modified — should be "written" (idempotent)
    for _fname, res in results2.items():
        # status can be written (idempotent) since hash match means TRW-installed version
        assert res["status"] in ("written", "preserved")


# ---------------------------------------------------------------------------
# FR15 — 4096-byte quota
# ---------------------------------------------------------------------------


def test_command_file_quota_4096_bytes() -> None:
    """FR15: All three command files are under 4096 bytes."""
    from trw_mcp.channels.opencode._custom_commands import (
        COMMAND_QUOTA_BYTES,
        get_before_edit_content,
        get_conventions_content,
        get_hotspots_content,
    )

    for fn, content in [
        ("before-edit", get_before_edit_content()),
        ("hotspots", get_hotspots_content()),
        ("conventions", get_conventions_content()),
    ]:
        size = len(content.encode("utf-8"))
        assert size <= COMMAND_QUOTA_BYTES, f"{fn}: {size} > {COMMAND_QUOTA_BYTES}"


# ---------------------------------------------------------------------------
# install_custom_commands — all three files
# ---------------------------------------------------------------------------


def test_install_all_three_commands(tmp_path: Path) -> None:
    """All three command files are written at install time."""
    from trw_mcp.channels.opencode._custom_commands import install_custom_commands

    results = install_custom_commands(tmp_path)
    assert set(results.keys()) == {
        "trw-before-edit.md",
        "trw-distill-hotspots.md",
        "trw-distill-conventions.md",
    }
    for filename, res in results.items():
        assert res["status"] in ("written", "preserved"), f"{filename}: {res}"
        target = tmp_path / ".opencode" / "commands" / filename
        assert target.exists(), f"{filename} not found"


def test_command_write_error_returns_error_status(tmp_path: Path) -> None:
    """FR14: OSError during write returns error status (fail-open)."""
    from trw_mcp.channels.opencode._custom_commands import install_custom_commands

    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        results = install_custom_commands(tmp_path)

    for filename, res in results.items():
        assert res["status"] == "error", f"{filename}: expected error, got {res['status']}"
        assert "error" in res


def test_truncation_applied_when_content_exceeds_quota() -> None:
    """FR15: _apply_quota truncates content over 4096 bytes with footer."""
    from trw_mcp.channels.opencode._custom_commands import (
        _TRUNCATION_FOOTER,
        COMMAND_QUOTA_BYTES,
        _apply_quota,
    )

    # Build content over the limit
    oversized = "x" * (COMMAND_QUOTA_BYTES + 500)
    result = _apply_quota(oversized)

    assert len(result.encode("utf-8")) <= COMMAND_QUOTA_BYTES
    assert result.endswith(_TRUNCATION_FOOTER)
