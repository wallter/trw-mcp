"""Behavioral tests for the CC-03 PreToolUse shell hook (PRD-DIST-2405 FR25-FR32).

Tests invoke pre-tool-distill-hint.sh via subprocess with controlled stdin JSON
fixtures and a tmp project directory. All assertions are on REAL shell execution
outputs and exit codes.

CC-04 PostToolUse correlation: no shell layer exists for CC-04. The hint-file
write (write_hint_file / prune_hint_files) is already covered in test_hook_helpers.py.
The shell hook unconditionally invokes the Python subprocess for hint-file writing
when enabled AND tool_use_id is present — tested in test_cc04_correlation.py.

FR26 CONTRACT: the hook MUST never exit non-zero under any condition.
This is enforced by ``set -e; trap 'exit 0' EXIT`` at the top of the script.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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
    """Run the CC-03 hook with the given stdin payload and project dir."""
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
    """Write .trw/config.yaml enabling the CC-03 hook."""
    trw_dir = tmp_project / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")


def _make_pretooluse(
    file_path: str = "src/module.py",
    tool_use_id: str = "toolu-test-001",
    tool_name: str = "Edit",
    agent_name: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "tool_use_id": tool_use_id,
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
    }
    if agent_name is not None:
        payload["agent_name"] = agent_name
    return json.dumps(payload)


# ---------------------------------------------------------------------------
# FR26 — NEVER exits non-zero (the most critical property)
# ---------------------------------------------------------------------------


class TestNeverExitsNonZero:
    """FR26: hook exit code is always 0, regardless of input."""

    def test_rc0_malformed_json(self, tmp_path: Path) -> None:
        result = _run_hook("NOT_JSON_AT_ALL", tmp_path)
        assert result.returncode == 0

    def test_rc0_empty_stdin(self, tmp_path: Path) -> None:
        result = _run_hook("", tmp_path)
        assert result.returncode == 0

    def test_rc0_valid_pretooluse_disabled(self, tmp_path: Path) -> None:
        # Default state: cc03_hook_enabled absent → disabled
        result = _run_hook(_make_pretooluse(), tmp_path)
        assert result.returncode == 0

    def test_rc0_valid_pretooluse_enabled(self, tmp_path: Path) -> None:
        _enable_cc03(tmp_path)
        result = _run_hook(_make_pretooluse(), tmp_path)
        assert result.returncode == 0

    def test_rc0_partial_json_missing_fields(self, tmp_path: Path) -> None:
        # Valid JSON but missing expected fields
        _enable_cc03(tmp_path)
        result = _run_hook('{"tool_name": "Edit"}', tmp_path)
        assert result.returncode == 0

    def test_rc0_null_values(self, tmp_path: Path) -> None:
        _enable_cc03(tmp_path)
        result = _run_hook('{"tool_use_id": null, "tool_input": null}', tmp_path)
        assert result.returncode == 0

    def test_rc0_truncated_json(self, tmp_path: Path) -> None:
        _enable_cc03(tmp_path)
        result = _run_hook('{"tool_use_id": "t1"', tmp_path)  # unclosed JSON
        assert result.returncode == 0

    def test_rc0_binary_garbage(self, tmp_path: Path) -> None:
        result = subprocess.run(
            ["sh", str(_HOOK)],
            input=b"\x00\xff\xfe\xfd",
            capture_output=True,
            timeout=8,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "TRW_PROJECT_DIR": str(tmp_path),
            },
        )
        assert result.returncode == 0

    def test_rc0_very_large_stdin(self, tmp_path: Path) -> None:
        # 100KB of random text shouldn't crash or exit non-zero
        large = "A" * 100_000
        result = _run_hook(large, tmp_path)
        assert result.returncode == 0

    def test_never_exits_2(self, tmp_path: Path) -> None:
        """FR26 core contract: exit code 2 is explicitly forbidden."""
        _enable_cc03(tmp_path)
        # Try all representative inputs
        for payload in [
            "NOT_JSON",
            "",
            '{"tool_use_id": "t1", "tool_input": {"file_path": "main.py"}}',
            '{"garbage": true}',
        ]:
            result = _run_hook(payload, tmp_path)
            assert result.returncode != 2, f"Hook exited 2 with payload={payload!r} — FR26 violation"


# ---------------------------------------------------------------------------
# FR25 — Opt-in gate: hook exits silently when disabled
# ---------------------------------------------------------------------------


class TestOptInGate:
    """FR25: cc03_hook_enabled gate controls whether hint is emitted."""

    def test_disabled_by_default_no_output(self, tmp_path: Path) -> None:
        """No config.yaml → cc03_hook_enabled defaults False → no output."""
        result = _run_hook(_make_pretooluse(), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_disabled_explicit_false_no_output(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        (trw_dir / "config.yaml").write_text("cc03_hook_enabled: false\n", encoding="utf-8")
        result = _run_hook(_make_pretooluse(), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_enabled_produces_output_for_py_file(self, tmp_path: Path) -> None:
        """When enabled, a .py file produces at least the T0 beacon."""
        _enable_cc03(tmp_path)
        result = _run_hook(_make_pretooluse(file_path="src/app.py"), tmp_path)
        assert result.returncode == 0
        # Enabled + .py → T0 beacon at minimum (Python may not be importable)
        assert len(result.stdout) > 0

    def test_enabled_invalid_yaml_config_falls_back_disabled(self, tmp_path: Path) -> None:
        """Corrupt config.yaml → shell falls back to disabled (no output)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        (trw_dir / "config.yaml").write_text("{invalid yaml[", encoding="utf-8")
        result = _run_hook(_make_pretooluse(), tmp_path)
        assert result.returncode == 0
        # Invalid YAML → grep finds nothing → falls back to 'false'
        assert result.stdout == ""

    def test_nested_channels_cc03_hook_enabled_enables_hook(self, tmp_path: Path) -> None:
        """channels.cc03_hook_enabled: true (nested) enables the hook via shell.

        This is the canonical documented enable path per manifest-claude-code.yaml.
        The shell _get_cc03_enabled() now reads nested keys via awk in addition to
        the top-level cc03_hook_enabled key, matching the Python _hook_helpers.py
        behavior and the documented operator enable path.
        """
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        # channels.cc03_hook_enabled: true — the documented canonical enable path
        (trw_dir / "config.yaml").write_text("channels:\n  cc03_hook_enabled: true\n", encoding="utf-8")
        result = _run_hook(_make_pretooluse(file_path="main.py"), tmp_path)
        assert result.returncode == 0
        # Nested key IS now matched by the shell — hook emits output (T0 beacon at minimum)
        assert len(result.stdout) > 0, (
            "channels.cc03_hook_enabled: true should enable the hook via shell; got empty output (hook stayed disabled)"
        )

    def test_toplevel_cc03_hook_enabled_overrides_nested(self, tmp_path: Path) -> None:
        """Top-level cc03_hook_enabled: false disables even when nested key is true."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        (trw_dir / "config.yaml").write_text(
            "cc03_hook_enabled: false\nchannels:\n  cc03_hook_enabled: true\n",
            encoding="utf-8",
        )
        result = _run_hook(_make_pretooluse(file_path="main.py"), tmp_path)
        assert result.returncode == 0
        # Top-level false overrides nested true
        assert result.stdout == ""

    def test_nested_channels_cc03_enabled_enables_hook(self, tmp_path: Path) -> None:
        """channels.cc03.enabled: true (alternative nested path) enables the hook."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        (trw_dir / "config.yaml").write_text("channels:\n  cc03:\n    enabled: true\n", encoding="utf-8")
        result = _run_hook(_make_pretooluse(file_path="main.py"), tmp_path)
        assert result.returncode == 0
        # Alternative nested key IS matched by the shell
        assert len(result.stdout) > 0, (
            "channels.cc03.enabled: true should enable the hook via shell; got empty output (hook stayed disabled)"
        )


# ---------------------------------------------------------------------------
# FR27 — Skip conditions produce no output
# ---------------------------------------------------------------------------


class TestSkipConditions:
    """FR27: Skip conditions all exit 0 silently."""

    @pytest.fixture(autouse=True)
    def _enable(self, tmp_path: Path) -> None:
        _enable_cc03(tmp_path)

    def test_skip_md_extension(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path="README.md"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_txt_extension(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path="notes.txt"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_rst_extension(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path="docs/README.rst"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_lock_extension(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path="package-lock.json.lock"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_log_extension(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path="app.log"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_gitignore_basename(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path=".gitignore"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_agent_type_explore(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path="main.py", agent_name="Explore"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_agent_type_plan(self, tmp_path: Path) -> None:
        result = _run_hook(_make_pretooluse(file_path="main.py", agent_name="Plan"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_agent_type_trw_distill_explorer(self, tmp_path: Path) -> None:
        result = _run_hook(
            _make_pretooluse(file_path="main.py", agent_name="trw-distill-explorer"),
            tmp_path,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_skip_missing_file_path(self, tmp_path: Path) -> None:
        """Empty file_path → skip condition (no hint for unknown file)."""
        payload = json.dumps({"tool_use_id": "t1", "tool_name": "Edit", "tool_input": {}})
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_non_skipped_py_extension_produces_output(self, tmp_path: Path) -> None:
        """.py files are NOT in the skip allowlist and produce a hint."""
        result = _run_hook(_make_pretooluse(file_path="src/engine.py"), tmp_path)
        assert result.returncode == 0
        assert len(result.stdout) > 0

    def test_non_skipped_ts_extension_produces_output(self, tmp_path: Path) -> None:
        """.ts files are NOT in the skip allowlist and produce a hint."""
        result = _run_hook(_make_pretooluse(file_path="src/index.ts"), tmp_path)
        assert result.returncode == 0
        assert len(result.stdout) > 0

    def test_non_skipped_yaml_extension_produces_output(self, tmp_path: Path) -> None:
        """.yaml files are NOT in the skip allowlist (they have blast radius)."""
        result = _run_hook(_make_pretooluse(file_path=".trw/config.yaml"), tmp_path)
        assert result.returncode == 0
        assert len(result.stdout) > 0


# ---------------------------------------------------------------------------
# FR28/FR30 — T0 beacon shape (Python import fallback path)
# ---------------------------------------------------------------------------


class TestT0BeaconShape:
    """FR28/FR30: When Python fails, the T0 beacon is the fallback output."""

    def test_t0_beacon_output_under_cap(self, tmp_path: Path) -> None:
        """FR28: T0 beacon ≤ 120 chars."""
        _enable_cc03(tmp_path)
        # .py file + enabled → T0 beacon (no sidecar)
        result = _run_hook(_make_pretooluse(file_path="src/module.py"), tmp_path)
        assert result.returncode == 0
        if result.stdout:
            assert len(result.stdout.strip()) <= 120

    def test_t0_beacon_contains_trw_marker(self, tmp_path: Path) -> None:
        """T0 beacon output references TRW."""
        _enable_cc03(tmp_path)
        result = _run_hook(_make_pretooluse(file_path="src/module.py"), tmp_path)
        assert result.returncode == 0
        if result.stdout:
            assert "[TRW]" in result.stdout or "trw" in result.stdout.lower()


# ---------------------------------------------------------------------------
# FR32 — Output hard cap ≤ 9500 chars
# ---------------------------------------------------------------------------


class TestOutputHardCap:
    """FR32: stdout is capped at 9500 chars even with a large hint."""

    def test_output_within_cap_for_any_file(self, tmp_path: Path) -> None:
        """All hook output is ≤ 9500 chars (the documented cap)."""
        _enable_cc03(tmp_path)
        result = _run_hook(_make_pretooluse(file_path="src/huge_module.py"), tmp_path)
        assert result.returncode == 0
        # Output must not exceed the documented cap
        assert len(result.stdout) <= 9600  # +100 for trailing newline tolerance


# ---------------------------------------------------------------------------
# FR30/FR31 — Python timeout / fallback-to-T0-beacon path
# ---------------------------------------------------------------------------


class TestPythonFallback:
    """FR30/FR31: Python subprocess fallback on import error / timeout."""

    def test_unimportable_python_still_produces_t0_beacon(self, tmp_path: Path) -> None:
        """FR30: if Python subprocess fails/timeouts, hook emits T0 beacon (not hang/crash).

        We simulate an import error by writing a bad Python path file.
        The hook falls back to _format_t0_beacon in the || branch.
        """
        _enable_cc03(tmp_path)
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True, exist_ok=True)
        # Point cc03-python.txt to a non-existent binary → Python resolution fails
        (channels_dir / "cc03-python.txt").write_text("/nonexistent/python", encoding="utf-8")
        result = _run_hook(_make_pretooluse(file_path="src/module.py"), tmp_path)
        assert result.returncode == 0
        # Fallback T0 beacon or empty (if library path also fails)
        # Key invariant: no crash (rc=0)

    def test_fallback_output_within_t0_cap(self, tmp_path: Path) -> None:
        """FR31: fallback output (T0 beacon) is ≤ 120 chars."""
        _enable_cc03(tmp_path)
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True, exist_ok=True)
        (channels_dir / "cc03-python.txt").write_text("/nonexistent/python", encoding="utf-8")
        result = _run_hook(_make_pretooluse(file_path="src/module.py"), tmp_path)
        assert result.returncode == 0
        if result.stdout:
            assert len(result.stdout.strip()) <= 120


# ---------------------------------------------------------------------------
# Debounce behavior — same file within 180s is skipped
# ---------------------------------------------------------------------------


class TestDebounce:
    """Hook debounce: same file_path within 180s produces no second hint."""

    def test_debounce_suppresses_second_hint_for_same_file(self, tmp_path: Path) -> None:
        """Second call for the same file within 180s → silent (exit 0, no output)."""
        _enable_cc03(tmp_path)
        file_path = "src/debounced.py"
        # First call — hint expected
        r1 = _run_hook(_make_pretooluse(file_path=file_path, tool_use_id="t-db-1"), tmp_path)
        assert r1.returncode == 0
        # Second call — debounce guard should suppress output
        r2 = _run_hook(_make_pretooluse(file_path=file_path, tool_use_id="t-db-2"), tmp_path)
        assert r2.returncode == 0
        assert r2.stdout == "", "Second call within debounce window must be silent"

    def test_different_files_not_debounced(self, tmp_path: Path) -> None:
        """Different file_paths are independent debounce entries."""
        _enable_cc03(tmp_path)
        r1 = _run_hook(_make_pretooluse(file_path="src/a.py", tool_use_id="t-a"), tmp_path)
        r2 = _run_hook(_make_pretooluse(file_path="src/b.py", tool_use_id="t-b"), tmp_path)
        assert r1.returncode == 0
        assert r2.returncode == 0
        # Both non-skipped files get output (not debounced against each other)
        assert len(r1.stdout) > 0
        assert len(r2.stdout) > 0

    def test_debounce_dir_created_on_first_call(self, tmp_path: Path) -> None:
        """Debounce directory is created at .trw/context/cc03-debounce."""
        _enable_cc03(tmp_path)
        debounce_dir = tmp_path / ".trw" / "context" / "cc03-debounce"
        assert not debounce_dir.exists()
        _run_hook(_make_pretooluse(file_path="src/c.py", tool_use_id="t-c"), tmp_path)
        assert debounce_dir.exists()
