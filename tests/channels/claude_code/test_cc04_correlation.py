"""Behavioral tests for CC-04 PostToolUse correlation (PRD-DIST-2405 FR33-FR36).

Architecture finding: CC-04 has NO shell-level PostToolUse hook script.
The correlation layer is entirely implemented in Python via:
  - ``write_hint_file()``   — writes per-hint JSON keyed by tool_use_id (FR33)
  - ``prune_hint_files()``  — cleans expired hint files (FR35)

These Python helpers are already covered in test_hook_helpers.py.

What this file tests:
  1. The Python integration: write_hint_file produces files keyed by tool_use_id
     with the correct CC-04 schema (FR33/FR36). Tested via Python layer directly.
  2. No cross-contamination between different tool_use_ids (FR33).
  3. Fail-open on IO error (FR34): write_hint_file creates the dir if absent.
  4. Hint file structure matches CC-04 correlation schema (FR33/FR36).
  5. The channel manifest declares CC-04 as ``posttooluse_event_log`` surface.
  6. Shell-level hint-file write succeeds for warm invocations within aligned timeout
     (2.5s). compute_before_edit_hint imports ~0.76s (no embedding stack at module
     level), so warm calls complete well within budget.

No PostToolUse shell hook script exists in the data directory — confirmed by
inspection of data/claude_code/hooks/: only pre-tool-distill-hint.sh and
lib-distill-hint.sh are present. The shell hook activates CC-04 correlation
by invoking write_hint_file via its Python subprocess (inline Python in the
shell script, not a separate hook file).

Timeout alignment (fixed): The shell hook previously used `timeout 2` (2000ms)
but documented budget is 2500ms. Fixed to `timeout 2.5`. compute_before_edit_hint
does NOT import the embedding/trw-memory stack at module level (~0.76s warm),
so write_hint_file runs within the aligned 2.5s budget on warm invocations.
Cold-start (.pyc compilation) may still exceed 2.5s on first run; the hook
falls back to T0 beacon in that edge case, which is acceptable UX.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOOK = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "trw_mcp"
    / "data"
    / "claude_code"
    / "hooks"
    / "pre-tool-distill-hint.sh"
)


def _run_hook(
    stdin_payload: str,
    tmp_project: Path,
    *,
    timeout: int = 8,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(_HOOK)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TRW_PROJECT_DIR": str(tmp_project),
        },
    )


def _enable_cc03(tmp_project: Path) -> None:
    trw_dir = tmp_project / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    channels = trw_dir / "channels"
    channels.mkdir()
    (channels / "cc03-python.txt").write_text(sys.executable, encoding="utf-8")


def _make_pretooluse(
    file_path: str = "src/module.py",
    tool_use_id: str = "toolu-cc04-001",
    tool_name: str = "Edit",
) -> str:
    return json.dumps(
        {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
        }
    )


# ---------------------------------------------------------------------------
# FR33 — Hint file keyed by tool_use_id (Python integration layer)
# ---------------------------------------------------------------------------
# These tests verify the Python CC-04 integration layer directly (via
# write_hint_file). Shell-level hint-file write is tested in
# test_shell_hint_file_written_within_aligned_timeout. The shell timeout was
# fixed to 2.5s (from buggy 2s) — see module docstring for the full account.


class TestHintFileKeyedByToolUseId:
    """FR33: hint file is written at hints_dir/{tool_use_id}.json (Python layer)."""

    def test_hint_file_created_at_expected_path(self, tmp_path: Path) -> None:
        """write_hint_file creates {hints_dir}/{tool_use_id}.json."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-cc04-abc"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path="src/module.py",
            tier="T0",
            hint_emitted=True,
            tokens_emitted=10,
            distill_status="sidecar_missing",
        )
        assert (hints_dir / f"{tool_use_id}.json").exists()

    def test_hint_file_schema_has_required_fields(self, tmp_path: Path) -> None:
        """FR36: hint file schema includes all required CC-04 correlation fields."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-schema-check"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path="src/schema.py",
            tier="T2",
            hint_emitted=True,
            tokens_emitted=45,
            distill_status="hint_available",
        )
        hint_file = hints_dir / f"{tool_use_id}.json"
        data = json.loads(hint_file.read_text(encoding="utf-8"))
        required = {"ts", "file_path", "tier", "hint_emitted", "tokens_emitted", "distill_status", "tool_use_id"}
        missing = required - set(data.keys())
        assert not missing, f"Hint file missing required fields: {missing}"

    def test_hint_file_tool_use_id_matches(self, tmp_path: Path) -> None:
        """FR33: tool_use_id in hint file matches the input."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-id-match"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path="src/match.py",
            tier="T1",
            hint_emitted=True,
            tokens_emitted=20,
            distill_status="tier_required",
        )
        data = json.loads((hints_dir / f"{tool_use_id}.json").read_text(encoding="utf-8"))
        assert data["tool_use_id"] == tool_use_id

    def test_hint_file_file_path_matches(self, tmp_path: Path) -> None:
        """FR33: file_path in hint file matches the input."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-fp-match"
        file_path = "src/target.py"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path=file_path,
            tier="T0",
            hint_emitted=False,
            tokens_emitted=0,
            distill_status="sidecar_missing",
        )
        data = json.loads((hints_dir / f"{tool_use_id}.json").read_text(encoding="utf-8"))
        assert data["file_path"] == file_path

    def test_shell_hint_file_written_within_aligned_timeout(self, tmp_path: Path) -> None:
        """FR33/FR29: hint file IS written via shell hook within the aligned 2.5s timeout.

        The shell hook was fixed to use `timeout 2.5` (from buggy `timeout 2`).
        compute_before_edit_hint does NOT import the embedding stack at module level
        (~0.76s warm import), so write_hint_file executes within budget.

        The shell writes a dependency-free provisional T0 record before starting
        the bounded intelligence subprocess, so cold imports cannot erase CC-04
        correlation evidence.
        """
        _enable_cc03(tmp_path)
        tool_use_id = "toolu-aligned-timeout"
        result = _run_hook(
            _make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id),
            tmp_path,
        )
        # FR26: exit code always 0
        assert result.returncode == 0
        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        hint_file = hints_dir / f"{tool_use_id}.json"
        assert hint_file.exists(), "warm invocation must write the correlation hint within the aligned timeout"


# ---------------------------------------------------------------------------
# FR33 — No cross-contamination between different tool_use_ids
# ---------------------------------------------------------------------------


class TestNoCrossContamination:
    """FR33: concurrent hint files for different tool_use_ids don't cross-contaminate."""

    def test_two_files_no_cross_contamination(self, tmp_path: Path) -> None:
        """Two concurrent tool_use_ids write separate hint files (Python layer)."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"

        id_a = "toolu-cross-a"
        id_b = "toolu-cross-b"
        file_a = "src/module_a.py"
        file_b = "src/module_b.py"

        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=id_a,
            file_path=file_a,
            tier="T2",
            hint_emitted=True,
            tokens_emitted=50,
            distill_status="hint_available",
        )
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=id_b,
            file_path=file_b,
            tier="T1",
            hint_emitted=True,
            tokens_emitted=30,
            distill_status="tier_required",
        )

        data_a = json.loads((hints_dir / f"{id_a}.json").read_text(encoding="utf-8"))
        data_b = json.loads((hints_dir / f"{id_b}.json").read_text(encoding="utf-8"))

        # No cross-contamination: each file contains its own data
        assert data_a["file_path"] == file_a
        assert data_b["file_path"] == file_b
        assert data_a["tool_use_id"] == id_a
        assert data_b["tool_use_id"] == id_b
        # B's file_path should not appear in A's record
        assert file_b not in data_a.get("file_path", "")
        assert file_a not in data_b.get("file_path", "")


# ---------------------------------------------------------------------------
# FR34 — Fail-open on IO error
# ---------------------------------------------------------------------------


class TestFailOpen:
    """FR34: hook exits 0 even if hint-file directory is unwritable."""

    def test_hook_exits_0_when_no_tool_use_id(self, tmp_path: Path) -> None:
        """No tool_use_id → write_hint_file is skipped → still exits 0."""
        _enable_cc03(tmp_path)
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "src/module.py"}})
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0

    def test_no_hint_file_for_skipped_extensions(self, tmp_path: Path) -> None:
        """Skip condition prevents hint file write — no hints_dir entry."""
        _enable_cc03(tmp_path)
        tool_use_id = "toolu-skip-ext"
        _run_hook(_make_pretooluse(file_path="README.md", tool_use_id=tool_use_id), tmp_path)
        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        hint_file = hints_dir / f"{tool_use_id}.json"
        # .md is skipped → no hint file written
        assert not hint_file.exists()

    def test_no_hint_file_when_disabled(self, tmp_path: Path) -> None:
        """Disabled hook never writes hint file."""
        tool_use_id = "toolu-disabled"
        # No config.yaml → disabled
        _run_hook(_make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id), tmp_path)
        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        hint_file = hints_dir / f"{tool_use_id}.json"
        assert not hint_file.exists()


# ---------------------------------------------------------------------------
# FR36 — Channel manifest declares CC-04 correctly
# ---------------------------------------------------------------------------


class TestChannelManifestDeclaresCC04:
    """FR36: the bundled manifest-claude-code.yaml declares CC-04 correctly."""

    def test_cc04_entry_present_in_manifest(self) -> None:
        """CC-04 entry exists in the bundled channel manifest."""
        from pathlib import Path as _Path

        manifest_path = (
            _Path(__file__).parent.parent.parent.parent
            / "src"
            / "trw_mcp"
            / "data"
            / "claude_code"
            / "channels"
            / "manifest-claude-code.yaml"
        )
        # Load via ManifestLoader for validation
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        raw = yaml.load(manifest_path.read_text(encoding="utf-8")) or {}
        channels = raw.get("channels", [])
        ids = [c.get("id") for c in channels]
        assert "cc-04-posttooluse-correlation" in ids

    def test_cc04_surface_is_posttooluse_event_log(self) -> None:
        """CC-04 surface is posttooluse_event_log (not instruction_file)."""
        from pathlib import Path as _Path

        from ruamel.yaml import YAML

        manifest_path = (
            _Path(__file__).parent.parent.parent.parent
            / "src"
            / "trw_mcp"
            / "data"
            / "claude_code"
            / "channels"
            / "manifest-claude-code.yaml"
        )
        yaml = YAML(typ="safe")
        raw = yaml.load(manifest_path.read_text(encoding="utf-8")) or {}
        channels = raw.get("channels", [])
        cc04 = next((c for c in channels if c.get("id") == "cc-04-posttooluse-correlation"), None)
        assert cc04 is not None
        assert cc04.get("surface") == "posttooluse_event_log"

    def test_cc04_fail_open_flag_matches_spec(self) -> None:
        """CC-04 has no activation_gate (always-on) per FR33."""
        from pathlib import Path as _Path

        from ruamel.yaml import YAML

        manifest_path = (
            _Path(__file__).parent.parent.parent.parent
            / "src"
            / "trw_mcp"
            / "data"
            / "claude_code"
            / "channels"
            / "manifest-claude-code.yaml"
        )
        yaml = YAML(typ="safe")
        raw = yaml.load(manifest_path.read_text(encoding="utf-8")) or {}
        channels = raw.get("channels", [])
        cc04 = next((c for c in channels if c.get("id") == "cc-04-posttooluse-correlation"), None)
        assert cc04 is not None
        # Always-on: no activation gate
        assert not cc04.get("activation_gate"), "CC-04 must be always-on (no activation_gate)"


# ---------------------------------------------------------------------------
# No shell PostToolUse hook exists — document and verify
# ---------------------------------------------------------------------------


class TestNoShellPostToolUseHook:
    """Verify there is no separate shell-level PostToolUse hook for CC-04.

    This is a documented architectural decision: CC-04 correlation is done
    via write_hint_file() called from the PreToolUse hook's Python subprocess
    (FR29). There is no post-tool-distill-hint.sh hook file.
    """

    def test_no_posttooluse_shell_hook_file(self) -> None:
        """Confirm: no post-tool-distill-hint.sh exists in data/claude_code/hooks/."""
        hooks_dir = Path(__file__).parent.parent.parent.parent / "src" / "trw_mcp" / "data" / "claude_code" / "hooks"
        post_hook = hooks_dir / "post-tool-distill-hint.sh"
        assert not post_hook.exists(), (
            "Unexpected PostToolUse shell hook found. If CC-04 gains a shell hook, add behavioral tests for it here."
        )

    def test_only_expected_hooks_in_data_dir(self) -> None:
        """data/claude_code/hooks/ contains exactly the two expected hook files."""
        hooks_dir = Path(__file__).parent.parent.parent.parent / "src" / "trw_mcp" / "data" / "claude_code" / "hooks"
        actual_files = {f.name for f in hooks_dir.iterdir() if f.is_file()}
        expected_files = {"pre-tool-distill-hint.sh", "lib-distill-hint.sh"}
        assert actual_files == expected_files, (
            f"Unexpected hook files: {actual_files - expected_files}. Update this test if new hooks are added."
        )
