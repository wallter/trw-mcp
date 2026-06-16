"""Adapter-chain + bootstrap tests for the C5 Copilot preToolUse hint (PRD-DIST-2459 FR-5).

Verifies the trw-distill before-edit hint is CHAINED alongside the existing
ceremony deliver-gate inside Copilot's single preToolUse slot, via
trw-copilot-adapter.sh — and that the deliver-gate decision stays AUTHORITATIVE:

  - deliver-gate ALLOW + hint present  -> {"permissionDecision":"allow", reason}
  - deliver-gate BLOCK (exit 2)        -> {"permissionDecision":"deny"} (hint never consulted)
  - hint that exits 2 / prints text    -> cannot flip a gate-allow into a deny
  - cc03 OFF / empty hint              -> behaves exactly as today (clean allow)

These exercise the REAL adapter shell script against stub deliver-gate / hint
scripts (behavior, not existence), the REAL bootstrap installer
(install_copilot_distill_channels), the shipped-data gate-off default, and the
IP boundary (no trw_distill import).
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from ruamel.yaml import YAML

_SRC = Path(__file__).parent.parent.parent.parent / "src" / "trw_mcp"
_HOOKS_DATA = _SRC / "data" / "copilot" / "hooks"
_ADAPTER = _HOOKS_DATA / "trw-copilot-adapter.sh"
_HINT = _HOOKS_DATA / "trw-copilot-distill-hint.sh"
_LIB = _HOOKS_DATA / "lib-copilot-distill-hint.sh"
_MANIFEST = _SRC / "data" / "copilot" / "channels" / "manifest-copilot.yaml"

_PAYLOAD = '{"toolName":"str_replace_editor","toolArgs":{"filePath":"src/foo.py"}}'


# ---------------------------------------------------------------------------
# Harness: install the adapter + a stub deliver-gate + a stub hint into a repo,
# then drive the REAL adapter and capture its stdout.
# ---------------------------------------------------------------------------


def _setup_adapter(tmp_path: Path, *, gate_exit: int, hint_stdout: str | None, hint_exit: int = 0) -> Path:
    """Install the real adapter + a stub gate + a stub hint; return the adapter path.

    ``gate_exit`` is the deliver-gate stub's exit code (2 = block).
    ``hint_stdout`` None means "no hint script installed"; otherwise the stub
    hint prints that text and exits ``hint_exit``.
    """
    hooks_dir = tmp_path / ".github" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    adapter_dest = hooks_dir / "trw-copilot-adapter.sh"
    shutil.copy2(_ADAPTER, adapter_dest)

    # Stub deliver-gate.
    gate = tmp_path / "gate.sh"
    gate.write_text(f"#!/bin/sh\nexit {gate_exit}\n", encoding="utf-8")
    gate.chmod(0o755)

    # Stub hint (installed next to the adapter, where the adapter looks for it).
    if hint_stdout is not None:
        hint = hooks_dir / "trw-copilot-distill-hint.sh"
        # Use printf so embedded escapes are honored exactly.
        escaped = hint_stdout.replace("\\", "\\\\").replace('"', '\\"')
        hint.write_text(f'#!/bin/sh\nprintf "{escaped}"\nexit {hint_exit}\n', encoding="utf-8")
        hint.chmod(0o755)

    return adapter_dest


def _run_adapter(adapter: Path, gate: Path, event: str = "preToolUse") -> str:
    proc = subprocess.run(
        ["/bin/sh", str(adapter), str(gate), event],
        input=_PAYLOAD,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.stdout


# ---------------------------------------------------------------------------
# Chaining: deliver-gate authoritative, hint advisory
# ---------------------------------------------------------------------------


class TestAdapterChain:
    def test_gate_allow_with_hint_emits_allow_and_reason(self, tmp_path: Path) -> None:
        adapter = _setup_adapter(tmp_path, gate_exit=0, hint_stdout="high-risk: run tests")
        gate = tmp_path / "gate.sh"
        out = json.loads(_run_adapter(adapter, gate))
        assert out["permissionDecision"] == "allow"
        assert out["permissionDecisionReason"] == "high-risk: run tests"

    def test_gate_block_stays_deny_even_with_hint(self, tmp_path: Path) -> None:
        """A deliver-gate BLOCK must remain a deny — the hint is never consulted
        and cannot flip it (FR-5: deliver-gate authoritative)."""
        adapter = _setup_adapter(tmp_path, gate_exit=2, hint_stdout="would-be-advice")
        gate = tmp_path / "gate.sh"
        out = json.loads(_run_adapter(adapter, gate))
        assert out["permissionDecision"] == "deny"
        # The block carries NO advisory reason — the hint did not run.
        assert "permissionDecisionReason" not in out

    def test_hint_exiting_2_cannot_flip_allow_to_deny(self, tmp_path: Path) -> None:
        """Even a hint that exits 2 (a deny signal in other contracts) cannot
        deny — the adapter ignores the hint exit code, using only its stdout."""
        adapter = _setup_adapter(tmp_path, gate_exit=0, hint_stdout="x", hint_exit=2)
        gate = tmp_path / "gate.sh"
        out = json.loads(_run_adapter(adapter, gate))
        assert out["permissionDecision"] == "allow"

    def test_gate_allow_empty_hint_is_clean_allow(self, tmp_path: Path) -> None:
        """Empty hint output (cc03 off / no sidecar) => behaves exactly as today:
        a clean allow with no advisory reason."""
        adapter = _setup_adapter(tmp_path, gate_exit=0, hint_stdout="")
        gate = tmp_path / "gate.sh"
        out = json.loads(_run_adapter(adapter, gate))
        assert out == {"permissionDecision": "allow"}

    def test_no_hint_script_installed_is_clean_allow(self, tmp_path: Path) -> None:
        """If the hint script is absent (older install), the adapter still emits
        a clean allow — the chain degrades to deliver-gate-only behavior."""
        adapter = _setup_adapter(tmp_path, gate_exit=0, hint_stdout=None)
        gate = tmp_path / "gate.sh"
        out = json.loads(_run_adapter(adapter, gate))
        assert out == {"permissionDecision": "allow"}

    def test_gate_block_with_no_hint_script_stays_deny(self, tmp_path: Path) -> None:
        adapter = _setup_adapter(tmp_path, gate_exit=2, hint_stdout=None)
        gate = tmp_path / "gate.sh"
        out = json.loads(_run_adapter(adapter, gate))
        assert out["permissionDecision"] == "deny"

    def test_multiline_quoted_hint_is_valid_json(self, tmp_path: Path) -> None:
        """A hint with quotes / newlines / tabs must be JSON-escaped into a
        single valid permissionDecisionReason string."""
        adapter = _setup_adapter(tmp_path, gate_exit=0, hint_stdout='line1 "q"\nline2\ttab')
        gate = tmp_path / "gate.sh"
        raw = _run_adapter(adapter, gate)
        out = json.loads(raw)  # must not raise
        assert out["permissionDecision"] == "allow"
        assert out["permissionDecisionReason"] == 'line1 "q"\nline2\ttab'


# ---------------------------------------------------------------------------
# Bootstrap: install_copilot_distill_channels writes both C5 hook scripts
# ---------------------------------------------------------------------------


class TestBootstrapInstall:
    def test_install_writes_c5_hook_and_lib(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._copilot_distill_channels import install_copilot_distill_channels

        install_copilot_distill_channels(tmp_path)
        hint = tmp_path / ".github" / "hooks" / "trw-copilot-distill-hint.sh"
        lib = tmp_path / ".github" / "hooks" / "lib-copilot-distill-hint.sh"
        assert hint.exists(), "C5 hint hook must be installed to .github/hooks/"
        assert lib.exists(), "C5 hook lib must be installed to .github/hooks/"
        # The hint hook is executable.
        assert hint.stat().st_mode & 0o111

    def test_install_is_idempotent(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._copilot_distill_channels import install_copilot_distill_channels

        install_copilot_distill_channels(tmp_path)
        result = install_copilot_distill_channels(tmp_path)
        rel = ".github/hooks/trw-copilot-distill-hint.sh"
        # Second run reports the unchanged hook as preserved, never duplicated/errored.
        assert rel in result["preserved"]
        assert not result["errors"]

    def test_installed_hint_chains_under_real_adapter(self, tmp_path: Path) -> None:
        """End-to-end: install via bootstrap, then drive the installed adapter
        with a gate-allow stub. With no .trw/config.yaml the gate is off, so the
        real installed hint emits nothing and the adapter returns a clean allow."""
        from trw_mcp.bootstrap._copilot_distill_channels import install_copilot_distill_channels

        install_copilot_distill_channels(tmp_path)
        # Install the adapter (normally done by generate_copilot_hooks).
        hooks_dir = tmp_path / ".github" / "hooks"
        shutil.copy2(_ADAPTER, hooks_dir / "trw-copilot-adapter.sh")
        gate = tmp_path / "gate.sh"
        gate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        gate.chmod(0o755)

        proc = subprocess.run(
            ["/bin/sh", str(hooks_dir / "trw-copilot-adapter.sh"), str(gate), "preToolUse"],
            input=_PAYLOAD,
            capture_output=True,
            text=True,
            timeout=20,
            env={"TRW_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        )
        out = json.loads(proc.stdout)
        # cc03 gate is OFF (no config) => no hint => clean allow (today's behavior).
        assert out == {"permissionDecision": "allow"}


# ---------------------------------------------------------------------------
# Shipped-data gate default (public opt-in preserved)
# ---------------------------------------------------------------------------


class TestShippedDataDefaults:
    def test_shipped_manifest_c5_gate_off(self) -> None:
        yaml = YAML(typ="safe")
        raw = yaml.load(_MANIFEST.read_text(encoding="utf-8"))
        c5 = next(c for c in raw["channels"] if c["id"] == "copilot-pretooluse-hint")
        assert c5["activation_gate"] == "cc03_hook_enabled"
        assert c5["surface"] == "hook_stdout_ephemeral"
        assert c5["client"] == "copilot"
        # Nothing in shipped data turns the shared gate on.
        assert "cc03_hook_enabled: true" not in _MANIFEST.read_text(encoding="utf-8")

    def test_c5_manifest_validates(self) -> None:
        """The C5 entry validates against ChannelEntry (schema parity with C1-C4)."""
        from trw_mcp.channels._manifest_models import ChannelEntry

        yaml = YAML(typ="safe")
        raw = yaml.load(_MANIFEST.read_text(encoding="utf-8"))
        c5 = next(c for c in raw["channels"] if c["id"] == "copilot-pretooluse-hint")
        entry = ChannelEntry.model_validate(c5)
        assert entry.id == "copilot-pretooluse-hint"
        assert entry.activation_gate == "cc03_hook_enabled"


# ---------------------------------------------------------------------------
# Manifest bootstrap merges C5 into .trw/channels/manifest.yaml
# ---------------------------------------------------------------------------


class TestManifestBootstrap:
    def test_c5_entry_merged(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._copilot_distill_channels import bootstrap_copilot_channel_manifest
        from trw_mcp.channels._manifest_loader import load

        bootstrap_copilot_channel_manifest(tmp_path)
        manifest = load(tmp_path / ".trw" / "channels" / "manifest.yaml")
        c5 = next(c for c in manifest.channels if c.id == "copilot-pretooluse-hint")
        assert c5.activation_gate == "cc03_hook_enabled"
        assert c5.client == "copilot"


# ---------------------------------------------------------------------------
# IP boundary: nothing imports trw_distill
# ---------------------------------------------------------------------------


class TestIpBoundary:
    def test_hook_scripts_do_not_reference_trw_distill(self) -> None:
        for path in (_HINT, _LIB, _ADAPTER):
            assert "trw_distill" not in path.read_text(encoding="utf-8")

    def test_copilot_distill_bootstrap_does_not_import_trw_distill(self) -> None:
        """No import statement in _copilot_distill_channels.py pulls in trw_distill."""
        src = _SRC / "bootstrap" / "_copilot_distill_channels.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("trw_distill") for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or not node.module.startswith("trw_distill")


# ---------------------------------------------------------------------------
# Shell hooks are POSIX-valid and never exit 2 from the hint path
# ---------------------------------------------------------------------------


class TestShellContract:
    @pytest.mark.parametrize("script", [_ADAPTER, _HINT, _LIB])
    def test_sh_n_clean(self, script: Path) -> None:
        proc = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_hint_never_exits_2(self, tmp_path: Path) -> None:
        """The hint hook always exits 0 (never the deny signal), even with no
        config, no sidecar, and a code-file target."""
        proc = subprocess.run(
            ["/bin/sh", str(_HINT)],
            input=_PAYLOAD,
            capture_output=True,
            text=True,
            timeout=15,
            env={"TRW_PROJECT_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        )
        assert proc.returncode == 0
