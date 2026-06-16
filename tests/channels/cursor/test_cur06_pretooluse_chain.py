"""Wiring + bootstrap tests for the CUR-06 Cursor preToolUse chain (PRD-DIST-2459 FR-4).

Verifies the distill-hint hook is CHAINED into the existing preToolUse slot
alongside the observer (trw-pre-tool-use.sh) — both run, both stay non-blocking
— without displacing the observer. Also covers manifest entry, shipped-data
gate-off default, and the IP boundary (no trw_distill import).

These exercise the REAL bootstrap path (generate_cursor_ide_hooks) against a
real filesystem and assert the resulting .cursor/hooks.json contents.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from ruamel.yaml import YAML

_SRC = Path(__file__).parent.parent.parent.parent / "src" / "trw_mcp"
_OBSERVER_CMD = ".cursor/hooks/trw-pre-tool-use.sh"
_HINT_CMD = ".cursor/hooks/trw-before-edit-hint.sh"


def _install_ide_hooks(target_dir: Path) -> None:
    from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_hooks

    generate_cursor_ide_hooks(target_dir)


def _read_hooks_json(target_dir: Path) -> dict[str, object]:
    parsed: dict[str, object] = json.loads(
        (target_dir / ".cursor" / "hooks.json").read_text(encoding="utf-8")
    )
    return parsed


def _pretooluse_commands(target_dir: Path) -> list[str]:
    cfg = _read_hooks_json(target_dir)
    hooks = cfg.get("hooks", {})
    assert isinstance(hooks, dict)
    entries = hooks.get("preToolUse", [])
    assert isinstance(entries, list)
    return [e["command"] for e in entries if isinstance(e, dict) and "command" in e]


# ---------------------------------------------------------------------------
# Chain: BOTH the observer AND the hint hook present in preToolUse
# ---------------------------------------------------------------------------


class TestPreToolUseChain:
    def test_both_observer_and_hint_present(self, tmp_path: Path) -> None:
        _install_ide_hooks(tmp_path)
        cmds = _pretooluse_commands(tmp_path)
        assert _OBSERVER_CMD in cmds, "observer hook must remain in preToolUse"
        assert _HINT_CMD in cmds, "distill-hint hook must be chained into preToolUse"

    def test_observer_runs_first(self, tmp_path: Path) -> None:
        """The observer stays at its existing position (first); the hint is
        appended after it — neither displaces the other."""
        _install_ide_hooks(tmp_path)
        cmds = _pretooluse_commands(tmp_path)
        assert cmds.index(_OBSERVER_CMD) < cmds.index(_HINT_CMD)

    def test_hint_entry_non_blocking(self, tmp_path: Path) -> None:
        """The hint entry must declare failClosed=False (never block on crash)."""
        _install_ide_hooks(tmp_path)
        cfg = _read_hooks_json(tmp_path)
        hooks = cfg["hooks"]
        assert isinstance(hooks, dict)
        entries = hooks["preToolUse"]
        hint = next(e for e in entries if e.get("command") == _HINT_CMD)
        assert hint.get("failClosed") is False

    def test_both_hook_scripts_installed(self, tmp_path: Path) -> None:
        _install_ide_hooks(tmp_path)
        assert (tmp_path / ".cursor" / "hooks" / "trw-pre-tool-use.sh").exists()
        assert (tmp_path / ".cursor" / "hooks" / "trw-before-edit-hint.sh").exists()
        assert (tmp_path / ".cursor" / "hooks" / "lib-distill-hint.sh").exists()

    def test_idempotent_no_duplicate_hint(self, tmp_path: Path) -> None:
        """Re-running bootstrap must not duplicate the hint (smart-merge strips
        prior trw- entries by command prefix before re-inserting)."""
        _install_ide_hooks(tmp_path)
        _install_ide_hooks(tmp_path)
        cmds = _pretooluse_commands(tmp_path)
        assert cmds.count(_HINT_CMD) == 1
        assert cmds.count(_OBSERVER_CMD) == 1


# ---------------------------------------------------------------------------
# Shipped-data gate default (public opt-in preserved)
# ---------------------------------------------------------------------------


class TestShippedDataDefaults:
    def test_shipped_manifest_cur06_gate_off(self) -> None:
        data = _SRC / "data" / "cursor" / "channels" / "manifest-cursor.yaml"
        yaml = YAML(typ="safe")
        raw = yaml.load(data.read_text(encoding="utf-8"))
        cur06 = next(c for c in raw["channels"] if c["id"] == "cursor-pretooluse-hint")
        assert cur06["activation_gate"] == "cc03_hook_enabled"
        assert cur06["surface"] == "hook_stdout_ephemeral"
        assert cur06["client"] == "cursor-ide"
        # Nothing in shipped data turns the shared gate on.
        assert "cc03_hook_enabled: true" not in data.read_text(encoding="utf-8")

    def test_cur06_manifest_validates(self) -> None:
        """The CUR-06 entry validates against ChannelEntry (schema parity)."""
        from trw_mcp.channels._manifest_models import ChannelEntry

        data = _SRC / "data" / "cursor" / "channels" / "manifest-cursor.yaml"
        yaml = YAML(typ="safe")
        raw = yaml.load(data.read_text(encoding="utf-8"))
        cur06 = next(c for c in raw["channels"] if c["id"] == "cursor-pretooluse-hint")
        entry = ChannelEntry.model_validate(cur06)
        assert entry.id == "cursor-pretooluse-hint"
        assert entry.activation_gate == "cc03_hook_enabled"


# ---------------------------------------------------------------------------
# Manifest bootstrap merges CUR-06
# ---------------------------------------------------------------------------


class TestManifestBootstrap:
    def test_cur06_entry_merged(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_distill_channels import (
            bootstrap_cursor_channel_manifest,
        )
        from trw_mcp.channels._manifest_loader import load

        bootstrap_cursor_channel_manifest(tmp_path)
        manifest = load(tmp_path / ".trw" / "channels" / "manifest.yaml")
        cur06 = next(c for c in manifest.channels if c.id == "cursor-pretooluse-hint")
        assert cur06.activation_gate == "cc03_hook_enabled"
        assert cur06.client == "cursor-ide"


# ---------------------------------------------------------------------------
# IP boundary: nothing imports trw_distill
# ---------------------------------------------------------------------------


class TestIpBoundary:
    def test_hook_scripts_do_not_reference_trw_distill(self) -> None:
        hooks_dir = _SRC / "data" / "hooks" / "cursor"
        for name in ("trw-before-edit-hint.sh", "lib-distill-hint.sh"):
            assert "trw_distill" not in (hooks_dir / name).read_text(encoding="utf-8")

    def test_cursor_ide_bootstrap_does_not_import_trw_distill(self) -> None:
        """No import statement in _cursor_ide.py pulls in trw_distill."""
        src = _SRC / "bootstrap" / "_cursor_ide.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("trw_distill") for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or not node.module.startswith("trw_distill")
