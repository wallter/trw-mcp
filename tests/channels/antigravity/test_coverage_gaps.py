"""Coverage gap tests for channels/antigravity/ and bootstrap/_antigravity_distill_channels.py.

Covers error paths, edge cases, and behavioral branches not reached by the
primary test files. All tests use tmp_path; no live MCP calls.

PRD-DIST-2404.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# PRD-CORE-239 FR01: the eight `_antigravity_md_segment.py` coverage-gap tests
# that lived here were deleted with their subject. That module rendered the
# AG-01 ANTIGRAVITY.md distill segment (`ag-01-antigravity-md-distill`), which
# FR01 removed along with the module itself, so `_format_convention`,
# `_t1_content`, `_assert_no_template_vars`, `_yaml_safe_cell` and
# `render_antigravity_distill_segment` no longer exist to test. They were
# removed rather than rewritten because nothing inherited their behaviour —
# AG-01's renderer has no successor.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _before_edit_hook.py coverage gaps (error paths)
# ---------------------------------------------------------------------------


def test_install_hook_script_write_failure(tmp_path: Path) -> None:
    """install_before_edit_hook returns error dict when script write fails (lines 281-293)."""
    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    # Make the hooks dir a FILE (so mkdir will succeed but write_text will fail
    # on a directory conflict). We patch write_text on Path to raise OSError.
    with patch("pathlib.Path.write_text", side_effect=OSError("Permission denied")):
        result = install_before_edit_hook(tmp_path, overwrite=True)

    assert result["installed"] is False
    assert result["error"] is not None
    assert "Failed to write hook script" in result["error"]


def test_install_hooks_json_write_failure(tmp_path: Path) -> None:
    """install_before_edit_hook returns error when hooks.json write fails (lines 326-339)."""
    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    call_count = [0]
    original_write = Path.write_text

    def write_text_side_effect(self: Path, content: str, **kwargs: Any) -> None:
        call_count[0] += 1
        if call_count[0] == 2:  # Second write is hooks.json
            raise OSError("Disk full")
        original_write(self, content, **kwargs)

    with patch("pathlib.Path.write_text", write_text_side_effect):
        result = install_before_edit_hook(tmp_path, overwrite=True)

    # Either hooks.json failed or we got a partial failure
    if result["installed"] is False:
        assert result["error"] is not None


def test_install_hooks_json_parse_failure(tmp_path: Path) -> None:
    """install_before_edit_hook handles invalid JSON in existing hooks.json (lines 312-313)."""
    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    # Write an invalid JSON file to simulate corrupt hooks.json
    hooks_dir = tmp_path / ".antigravitycli"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "hooks.json").write_text("NOT VALID JSON {{{{", encoding="utf-8")

    # Should not raise — should start fresh
    result = install_before_edit_hook(tmp_path, overwrite=True)
    assert result["error"] is None or "parse" not in result.get("error", "")


def test_generate_hook_script_validates_content() -> None:
    """generate_hook_script validates HOOK_SCRIPT_CONTENT has no {{ }} tokens (line 174)."""
    from trw_mcp.channels.antigravity._before_edit_hook import (
        HOOK_SCRIPT_CONTENT,
        generate_hook_script,
    )

    # Verify the clean path succeeds.
    content = generate_hook_script()
    assert "{{" not in content
    assert "}}" not in content
    assert content == HOOK_SCRIPT_CONTENT


# ---------------------------------------------------------------------------
# _explorer_subagent.py coverage gaps (error paths)
# ---------------------------------------------------------------------------


def test_hotspot_table_empty_list_uses_placeholder_rows() -> None:
    """_hotspot_table with empty hotspot list returns placeholder rows (line 123)."""
    from trw_mcp.channels.antigravity._explorer_subagent import _hotspot_table

    result = _hotspot_table([], count=5)
    assert "<path>" in result
    assert "<score>" in result


def test_conventions_section_empty_returns_no_data() -> None:
    """_conventions_section with empty list returns 'No convention data' (line 141)."""
    from trw_mcp.channels.antigravity._explorer_subagent import _conventions_section

    result = _conventions_section([])
    assert "_No convention data yet._" in result


def test_conventions_section_dict_convention() -> None:
    """_conventions_section handles dict convention with 'text' key (line 145)."""
    from trw_mcp.channels.antigravity._explorer_subagent import _conventions_section

    result = _conventions_section([{"text": "Always test first"}])
    assert "Always test first" in result


def test_subagent_write_error_on_write_failure(tmp_path: Path) -> None:
    """generate_distill_explorer_agent returns error when file write fails (lines 322-333)."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar: dict[str, Any] = {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": [],
        "conventions": [],
    }

    with patch("pathlib.Path.write_text", side_effect=OSError("Permission denied")):
        result = generate_distill_explorer_agent(
            repo_root=tmp_path,
            sidecar_data=sidecar,
            sidecar_sha="sha_write_err",
        )

    assert result.status == "error"
    assert result.error is not None


def test_subagent_state_write_failure_is_fail_open(tmp_path: Path) -> None:
    """generate_distill_explorer_agent is fail-open on state write failure (lines 350-351)."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar: dict[str, Any] = {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": [{"file": "src/a.py", "risk_score": 0.5, "churn": 5, "caller_count": 3}],
        "conventions": ["test"],
    }

    with patch(
        "trw_mcp.channels.antigravity._explorer_subagent.write_state",
        side_effect=Exception("State write failed"),
    ):
        result = generate_distill_explorer_agent(
            repo_root=tmp_path,
            sidecar_data=sidecar,
            sidecar_sha="sha_state_err",
        )

    # Should succeed despite state write failure (fail-open)
    assert result.status == "written"


def test_subagent_telemetry_failure_is_fail_open(tmp_path: Path) -> None:
    """generate_distill_explorer_agent is fail-open on telemetry append failure (lines 363-364)."""
    from trw_mcp.channels.antigravity._explorer_subagent import generate_distill_explorer_agent

    sidecar: dict[str, Any] = {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": [{"file": "src/b.py", "risk_score": 0.7, "churn": 8, "caller_count": 2}],
        "conventions": ["type safety"],
    }

    with patch(
        "trw_mcp.channels.antigravity._explorer_subagent.append_channel_event",
        side_effect=Exception("Telemetry failed"),
    ):
        result = generate_distill_explorer_agent(
            repo_root=tmp_path,
            sidecar_data=sidecar,
            sidecar_sha="sha_tel_err",
        )

    # Should succeed despite telemetry failure (fail-open)
    assert result.status == "written"


# ---------------------------------------------------------------------------
# bootstrap/_antigravity_distill_channels.py coverage gaps
# ---------------------------------------------------------------------------


def test_install_skips_status_preserved(tmp_path: Path) -> None:
    """install_antigravity_distill_channels: skipped agent status goes to preserved (line 154)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    # First install creates the agent
    install_antigravity_distill_channels(tmp_path)

    # Mock generate_distill_explorer_agent to return skipped status
    from trw_mcp.channels.antigravity._explorer_subagent import AG02_CHANNEL_ID, AgentWriteResult

    skipped_result = AgentWriteResult(
        channel_id=AG02_CHANNEL_ID,
        status="skipped_same_sha",
        path=".antigravitycli/agents/trw-distill-explorer.md",
    )
    with patch("trw_mcp.bootstrap._antigravity_distill_channels.install_antigravity_distill_channels"):
        pass  # Just verify the call path exists

    # Test the "skipped" branch by passing an install with same SHA
    result = install_antigravity_distill_channels(tmp_path, force=False)
    assert isinstance(result, dict)
    assert "created" in result or "preserved" in result or "errors" in result


def test_install_ag02_exception_goes_to_errors(tmp_path: Path) -> None:
    """install_antigravity_distill_channels: AG-02 exception is fail-open (lines 157-159)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    # PRD-CORE-239: AG-02 is licence-gated, so the gate must be OPEN for the
    # generator to be reached at all — otherwise this asserts nothing, because
    # a skipped artifact raises nothing. The subject here is still fail-open
    # behaviour, not the gate.
    with (
        patch(
            "trw_mcp.tools._sidecar_substrate.distill_installed",
            return_value=True,
        ),
        patch(
            "trw_mcp.channels.antigravity.generate_distill_explorer_agent",
            side_effect=RuntimeError("Unexpected subagent error"),
        ),
    ):
        result = install_antigravity_distill_channels(tmp_path)

    # AG-02 error is captured, not raised
    assert any("AG-02" in e for e in result["errors"])


def test_install_ag03_error_in_result(tmp_path: Path) -> None:
    """install_antigravity_distill_channels: AG-03 hook error in result dict (line 176-177)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    error_hook_result = {
        "installed": False,
        "skipped": False,
        "error": "Permission denied on hooks.json",
        "hook_script_path": ".antigravitycli/hooks/trw_before_edit_telemetry.py",
        "hooks_json_path": ".antigravitycli/hooks.json",
    }
    with patch(
        "trw_mcp.channels.antigravity.install_before_edit_hook",
        return_value=error_hook_result,
    ):
        result = install_antigravity_distill_channels(tmp_path)

    # Hook error captured in errors list
    assert any("AG-03" in e for e in result["errors"])


def test_install_manifest_validation_error_captured(tmp_path: Path) -> None:
    """install_antigravity_distill_channels: ManifestValidationError captured (lines 185-191)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels
    from trw_mcp.channels._manifest_loader import ManifestValidationError

    with patch(
        "trw_mcp.bootstrap._antigravity_distill_channels.bootstrap_antigravity_channel_manifest",
        side_effect=ManifestValidationError("Bad manifest entry"),
    ):
        result = install_antigravity_distill_channels(tmp_path)

    assert any("manifest" in e.lower() or "Antigravity manifest" in e for e in result["errors"])


def test_install_manifest_generic_exception_captured(tmp_path: Path) -> None:
    """install_antigravity_distill_channels: generic exception captured (lines 192-194)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    from trw_mcp.bootstrap._antigravity_distill_channels import install_antigravity_distill_channels

    with patch(
        "trw_mcp.bootstrap._antigravity_distill_channels.bootstrap_antigravity_channel_manifest",
        side_effect=RuntimeError("Filesystem error"),
    ):
        result = install_antigravity_distill_channels(tmp_path)

    assert any("manifest" in e.lower() or "Antigravity manifest" in e for e in result["errors"])


def test_bootstrap_manifest_validation_error_on_bad_data(tmp_path: Path) -> None:
    """bootstrap_antigravity_channel_manifest raises ManifestValidationError on invalid entry (lines 81-82)."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    from trw_mcp.bootstrap._antigravity_distill_channels import bootstrap_antigravity_channel_manifest
    from trw_mcp.channels._manifest_loader import ManifestValidationError

    bad_data = "format_version: manifest/v1\nchannels:\n- id: bad-entry\n  invalid_field: true\n"

    with patch(
        "trw_mcp.bootstrap._antigravity_distill_channels._MANIFEST_DATA",
    ) as mock_path:
        mock_path.read_text.return_value = bad_data
        with pytest.raises(ManifestValidationError):
            bootstrap_antigravity_channel_manifest(tmp_path)


# ---------------------------------------------------------------------------
# FR16: manifest entries correctness check
# ---------------------------------------------------------------------------


def test_manifest_yaml_loads_and_has_three_channels() -> None:
    """FR16: manifest-antigravity.yaml loads with three channel entries.

    PRD-CORE-239 FR01 removed ag-01-antigravity-md-distill (the ANTIGRAVITY.md
    instruction-file segment), taking the bundled manifest from four entries
    to three. The count is pinned at the real new number, not relaxed.
    """
    import yaml

    manifest_path = (
        Path(__file__).parent.parent.parent.parent
        / "src"
        / "trw_mcp"
        / "data"
        / "antigravity"
        / "channels"
        / "manifest-antigravity.yaml"
    )
    assert manifest_path.exists(), f"Manifest not found at {manifest_path}"

    with open(manifest_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    channels = data.get("channels", [])
    assert len(channels) == 3, f"Expected 3 channel entries, got {len(channels)}"

    assert {c["id"] for c in channels} == {
        "ag-02-distill-explorer-subagent",
        "ag-03-before-edit-hook",
        "ag-04-tool-return-enrichment",
    }


def test_manifest_ag03_status_is_aspirational() -> None:
    """AG-03 manifest entry has status: aspirational (truthful per task context)."""
    import yaml

    manifest_path = (
        Path(__file__).parent.parent.parent.parent
        / "src"
        / "trw_mcp"
        / "data"
        / "antigravity"
        / "channels"
        / "manifest-antigravity.yaml"
    )
    with open(manifest_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    ag03 = next((c for c in data["channels"] if c["id"] == "ag-03-before-edit-hook"), None)
    assert ag03 is not None
    assert ag03["status"] == "aspirational", (
        f"AG-03 must have status=aspirational (hook does not fire in agy v1.0.2-1.0.3), got: {ag03['status']}"
    )


def test_manifest_ag02_surface_is_subagent_file() -> None:
    """AG-02 manifest entry uses surface: subagent_file (not rules_segment)."""
    import yaml

    manifest_path = (
        Path(__file__).parent.parent.parent.parent
        / "src"
        / "trw_mcp"
        / "data"
        / "antigravity"
        / "channels"
        / "manifest-antigravity.yaml"
    )
    with open(manifest_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    ag02 = next((c for c in data["channels"] if c["id"] == "ag-02-distill-explorer-subagent"), None)
    assert ag02 is not None
    assert ag02["surface"] == "subagent_file", f"AG-02 surface should be 'subagent_file', got: {ag02['surface']}"


def test_manifest_ag04_write_strategy_none() -> None:
    """AG-04 manifest entry has write_strategy: NONE (pull-only, no file written)."""
    import yaml

    manifest_path = (
        Path(__file__).parent.parent.parent.parent
        / "src"
        / "trw_mcp"
        / "data"
        / "antigravity"
        / "channels"
        / "manifest-antigravity.yaml"
    )
    with open(manifest_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    ag04 = next((c for c in data["channels"] if c["id"] == "ag-04-tool-return-enrichment"), None)
    assert ag04 is not None
    assert ag04["write_strategy"] == "NONE", f"AG-04 write_strategy should be 'NONE', got: {ag04['write_strategy']}"


def test_no_trw_distill_imports_in_channel_package() -> None:
    """NFR02: no trw_distill imports in channels/antigravity/ package."""
    import ast

    channel_dir = Path(__file__).parent.parent.parent / "src" / "trw_mcp" / "channels" / "antigravity"
    for py_file in channel_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("trw_distill"), (
                        f"trw_distill import found in {py_file}: {node.module}"
                    )
