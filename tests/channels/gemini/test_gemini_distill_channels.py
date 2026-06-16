"""Integration tests for install_gemini_distill_channels (PRD-DIST-2459 FR-3).

Calls install_gemini_distill_channels(tmp_path) against a real filesystem and
asserts actual file contents/structure — not just existence.

- Hook scripts written to .gemini/hooks/.
- hooks.BeforeTool merged into .gemini/settings.json (idempotent, preserving).
- gm-01 channel entry merged into .trw/channels/manifest.yaml.
- Shipped manifest-gemini.yaml ships gate OFF by default.
- IP boundary: hook + bootstrap never import trw_distill.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ruamel.yaml import YAML


def _call_install(target_dir: Path, force: bool = False) -> dict[str, list[str]]:
    from trw_mcp.bootstrap._gemini_distill_channels import (
        install_gemini_distill_channels,
    )

    return install_gemini_distill_channels(target_dir, force=force)


def _read_settings(target_dir: Path) -> dict[str, object]:
    return json.loads((target_dir / ".gemini" / "settings.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Hook script install
# ---------------------------------------------------------------------------


class TestHookScriptsInstalled:
    def test_before_tool_hook_written(self, tmp_path: Path) -> None:
        _call_install(tmp_path)
        hook = tmp_path / ".gemini" / "hooks" / "trw-before-tool-hint.sh"
        assert hook.exists()
        assert hook.read_text(encoding="utf-8").startswith("#!/bin/sh")

    def test_lib_hook_written(self, tmp_path: Path) -> None:
        _call_install(tmp_path)
        lib = tmp_path / ".gemini" / "hooks" / "lib-distill-hint.sh"
        assert lib.exists()
        assert "_get_cc03_enabled" in lib.read_text(encoding="utf-8")

    def test_hook_is_executable(self, tmp_path: Path) -> None:
        _call_install(tmp_path)
        hook = tmp_path / ".gemini" / "hooks" / "trw-before-tool-hint.sh"
        assert hook.stat().st_mode & 0o111


# ---------------------------------------------------------------------------
# settings.json BeforeTool merge
# ---------------------------------------------------------------------------


class TestBeforeToolMerge:
    def test_before_tool_entry_merged(self, tmp_path: Path) -> None:
        _call_install(tmp_path)
        settings = _read_settings(tmp_path)
        before_tool = settings["hooks"]["BeforeTool"]  # type: ignore[index,call-overload]
        assert isinstance(before_tool, list)
        names = [
            h.get("name")
            for block in before_tool
            for h in block.get("hooks", [])
        ]
        assert "trw-distill-before-edit-hint" in names

    def test_before_tool_command_points_at_hook(self, tmp_path: Path) -> None:
        _call_install(tmp_path)
        settings = _read_settings(tmp_path)
        block = settings["hooks"]["BeforeTool"][0]  # type: ignore[index,call-overload]
        cmd = block["hooks"][0]["command"]
        assert ".gemini/hooks/trw-before-tool-hint.sh" in cmd

    def test_preserves_existing_settings(self, tmp_path: Path) -> None:
        """Existing mcpServers and unrelated keys survive the merge."""
        settings_path = tmp_path / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "mcpServers": {"trw": {"command": "trw-mcp", "args": ["serve"]}},
                    "context": {"fileName": "GEMINI.md"},
                }
            ),
            encoding="utf-8",
        )
        _call_install(tmp_path)
        settings = _read_settings(tmp_path)
        assert settings["mcpServers"] == {"trw": {"command": "trw-mcp", "args": ["serve"]}}
        assert settings["context"] == {"fileName": "GEMINI.md"}
        assert "BeforeTool" in settings["hooks"]  # type: ignore[operator]

    def test_preserves_existing_non_trw_before_tool_hook(self, tmp_path: Path) -> None:
        settings_path = tmp_path / ".gemini" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "BeforeTool": [
                            {
                                "matcher": "run_shell_command",
                                "hooks": [{"type": "command", "name": "user-guard", "command": "x.sh"}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        _call_install(tmp_path)
        settings = _read_settings(tmp_path)
        names = [
            h.get("name")
            for block in settings["hooks"]["BeforeTool"]  # type: ignore[index,call-overload]
            for h in block.get("hooks", [])
        ]
        assert "user-guard" in names
        assert "trw-distill-before-edit-hint" in names


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_run_no_duplicate_before_tool_entry(self, tmp_path: Path) -> None:
        _call_install(tmp_path)
        _call_install(tmp_path)
        settings = _read_settings(tmp_path)
        managed = [
            block
            for block in settings["hooks"]["BeforeTool"]  # type: ignore[index,call-overload]
            if any(h.get("name") == "trw-distill-before-edit-hint" for h in block.get("hooks", []))
        ]
        assert len(managed) == 1

    def test_second_run_reports_preserved(self, tmp_path: Path) -> None:
        _call_install(tmp_path)
        result = _call_install(tmp_path)
        rel = ".gemini/settings.json"
        # Settings + both hook scripts are unchanged on the second run.
        assert rel in result["preserved"]

    def test_second_run_no_duplicate_manifest_entry(self, tmp_path: Path) -> None:
        from trw_mcp.channels._manifest_loader import load

        _call_install(tmp_path)
        _call_install(tmp_path)
        manifest = load(tmp_path / ".trw" / "channels" / "manifest.yaml")
        gm01 = [c for c in manifest.channels if c.id == "gm-01-before-tool-hint"]
        assert len(gm01) == 1


# ---------------------------------------------------------------------------
# Manifest entry
# ---------------------------------------------------------------------------


class TestManifestEntry:
    def test_gm01_entry_present(self, tmp_path: Path) -> None:
        from trw_mcp.channels._manifest_loader import load

        _call_install(tmp_path)
        manifest = load(tmp_path / ".trw" / "channels" / "manifest.yaml")
        gm01 = next(c for c in manifest.channels if c.id == "gm-01-before-tool-hint")
        assert gm01.activation_gate == "cc03_hook_enabled"
        assert gm01.surface == "hook_stdout_ephemeral"
        assert gm01.client == "gemini"


# ---------------------------------------------------------------------------
# Shipped-data gate default + IP boundary
# ---------------------------------------------------------------------------


class TestShippedDataDefaults:
    def test_shipped_manifest_gate_off_by_default(self) -> None:
        """The shipped manifest-gemini.yaml must ship the gate OFF (activation_gate
        present but the gate flag is never enabled in shipped data)."""
        data = (
            Path(__file__).parent.parent.parent.parent
            / "src"
            / "trw_mcp"
            / "data"
            / "gemini"
            / "channels"
            / "manifest-gemini.yaml"
        )
        yaml = YAML(typ="safe")
        raw = yaml.load(data.read_text(encoding="utf-8"))
        gm01 = next(c for c in raw["channels"] if c["id"] == "gm-01-before-tool-hint")
        # Activation gate is the shared opt-in flag — present but OFF by default.
        assert gm01["activation_gate"] == "cc03_hook_enabled"
        # Nothing in shipped data sets cc03_hook_enabled true.
        assert "cc03_hook_enabled: true" not in data.read_text(encoding="utf-8")

    def test_hook_scripts_do_not_import_trw_distill(self) -> None:
        """IP boundary: shipped hook scripts never reference trw_distill."""
        hooks_dir = (
            Path(__file__).parent.parent.parent.parent
            / "src"
            / "trw_mcp"
            / "data"
            / "gemini"
            / "hooks"
        )
        for sh in hooks_dir.glob("*.sh"):
            assert "trw_distill" not in sh.read_text(encoding="utf-8")

    def test_bootstrap_module_does_not_import_trw_distill(self) -> None:
        """IP boundary: no import statement pulls in trw_distill (prose mentions ok)."""
        import ast

        src = (
            Path(__file__).parent.parent.parent.parent
            / "src"
            / "trw_mcp"
            / "bootstrap"
            / "_gemini_distill_channels.py"
        )
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("trw_distill") for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or not node.module.startswith("trw_distill")


# ---------------------------------------------------------------------------
# Dispatcher wiring — init_project end-to-end
# ---------------------------------------------------------------------------


class TestInitProjectWiring:
    def test_init_project_gemini_installs_distill_channels(self, tmp_path: Path) -> None:
        """init_project(ide='gemini') triggers the gemini distill channel install
        through the central dispatcher (proves the _init_project.py wiring edit)."""
        from trw_mcp.bootstrap._init_project import init_project

        subprocess.run(["git", "init", str(tmp_path)], capture_output=True, check=False)
        init_project(tmp_path, ide="gemini")

        hook = tmp_path / ".gemini" / "hooks" / "trw-before-tool-hint.sh"
        assert hook.exists(), "init_project did not install the gemini BeforeTool hook"

        settings = json.loads((tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8"))
        names = [
            h.get("name")
            for block in settings.get("hooks", {}).get("BeforeTool", [])
            for h in block.get("hooks", [])
        ]
        assert "trw-distill-before-edit-hint" in names

        manifest = (tmp_path / ".trw" / "channels" / "manifest.yaml").read_text(encoding="utf-8")
        assert "gm-01-before-tool-hint" in manifest
