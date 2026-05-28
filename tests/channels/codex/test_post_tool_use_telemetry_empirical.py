"""Empirical verification: Codex PostToolUse hook input delivery mechanism.

PRD-DIST-2402 audit finding P0-03.

Verification date: 2026-05-28
Codex version tested: codex-cli 0.133.0
Method: Binary string analysis of Rust binary at
  ~/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/
  codex/node_modules/@openai/codex-linux-x64/vendor/
  x86_64-unknown-linux-musl/bin/codex

Findings:
- CODEX_HOOK_INPUT env var: ABSENT from binary strings — Codex does NOT set it.
- "failed to write hook stdin:": PRESENT in binary strings — Codex uses stdin.
- Known CODEX_ env vars in binary: CODEX_HOME, CODEX_SANDBOX_NETWORK_DISABLED,
  CODEX_THREAD_ID, CODEX_TUI_ROUNDED (none of these are hook-related).

Hook script correction:
- Before: tried CODEX_HOOK_INPUT first, fell back to stdin.
- After: reads stdin as primary, keeps CODEX_HOOK_INPUT as forward-compat fallback.

See scripts/verify-codex-hook-input.sh for the full re-runnable verification.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CODEX_BIN = os.environ.get(
    "CODEX_BIN",
    "/home/wallter/.nvm/versions/node/v24.14.0/bin/codex",
)
CODEX_RUST_BIN = (
    "/home/wallter/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex"
    "/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
)


def _codex_available() -> bool:
    """Return True if the codex binary exists and is executable."""
    return os.path.isfile(CODEX_BIN) and os.access(CODEX_BIN, os.X_OK)


def _strings_available() -> bool:
    """Return True if the `strings` utility is on PATH."""
    return shutil.which("strings") is not None


def _rust_binary_available() -> bool:
    """Return True if the Codex Rust binary exists (for binary analysis)."""
    return os.path.isfile(CODEX_RUST_BIN) and os.access(CODEX_RUST_BIN, os.X_OK)


# ---------------------------------------------------------------------------
# Test 1: CODEX_HOOK_INPUT absent from Codex binary (binary analysis)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_rust_binary_available() and _strings_available()),
    reason=(
        "Codex Rust binary not found at expected path or 'strings' not available. "
        "Install codex-cli 0.133.0 and ensure 'strings' (binutils) is installed."
    ),
)
def test_codex_hook_input_absent_from_binary() -> None:
    """P0-03: CODEX_HOOK_INPUT env var must NOT appear in Codex binary strings.

    If this test fails, Codex has added an env-var-based hook delivery mechanism
    and the hook script should be updated to prefer env var over stdin.
    """
    result = subprocess.run(
        ["strings", CODEX_RUST_BIN],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"strings command failed: {result.stderr}"

    binary_strings = result.stdout
    assert "CODEX_HOOK_INPUT" not in binary_strings, (
        "CODEX_HOOK_INPUT found in Codex binary strings! "
        "Codex may have added env-var hook delivery. "
        "Update hook script to prefer env var over stdin and re-verify."
    )


# ---------------------------------------------------------------------------
# Test 2: stdin delivery string present in Codex binary (binary analysis)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not (_rust_binary_available() and _strings_available()),
    reason="Codex Rust binary not found at expected path or 'strings' not available.",
)
def test_codex_hook_stdin_delivery_confirmed_in_binary() -> None:
    """P0-03: 'failed to write hook stdin:' must appear in Codex binary strings.

    This error string in the hook_runtime.rs module confirms that Codex writes
    hook input to stdin. If this test fails, Codex may have changed its delivery
    mechanism and re-verification is required.
    """
    result = subprocess.run(
        ["strings", CODEX_RUST_BIN],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"strings command failed: {result.stderr}"

    binary_strings = result.stdout
    assert "failed to write hook stdin" in binary_strings, (
        "'failed to write hook stdin:' NOT found in Codex binary strings. "
        "Codex may have changed its hook delivery mechanism. "
        "Re-run scripts/verify-codex-hook-input.sh to determine current mechanism."
    )


# ---------------------------------------------------------------------------
# Test 3: Hook script prefers stdin over env var (behavior test)
# ---------------------------------------------------------------------------


def test_hook_script_reads_stdin_as_primary(tmp_path: Path) -> None:
    """Empirical: hook script reads stdin as primary delivery mechanism.

    Verified 2026-05-28: Codex delivers hook input via stdin.
    The hook must read stdin first, not CODEX_HOOK_INPUT env var.
    """
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    assert hook_path.exists(), "Hook script not installed"

    # Deliver input via stdin (the confirmed Codex delivery mechanism)
    stdin_payload = json.dumps({
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-old\n+new"
        },
        "turn_id": "t-empirical-001",
        "tool_use_id": "u-empirical-001",
    })

    # Explicitly do NOT set CODEX_HOOK_INPUT — simulate actual Codex behavior
    env = {"PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"Hook exited non-zero: {result.returncode}"

    output = result.stdout.strip()
    data = json.loads(output)
    assert data.get("continue") is True, f"Unexpected hook output: {data}"

    # Verify telemetry was written (stdin was processed successfully)
    telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    assert telemetry_path.exists(), (
        "Telemetry not written when hook received input via stdin. "
        "This means stdin delivery is not working — check hook _read_hook_input()."
    )
    events = [json.loads(line) for line in telemetry_path.read_text().splitlines() if line.strip()]
    assert len(events) == 1, f"Expected 1 event, got {len(events)}"
    assert events[0]["turn_id"] == "t-empirical-001", (
        f"turn_id not captured from stdin: {events[0]}"
    )


def test_hook_script_does_not_require_codex_hook_input_env_var(tmp_path: Path) -> None:
    """Empirical: hook works correctly WITHOUT CODEX_HOOK_INPUT env var.

    Codex 0.133.0 never sets CODEX_HOOK_INPUT. The hook must function using
    stdin alone — the env var fallback is forward-compat only.
    """
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    stdin_payload = json.dumps({"tool_name": "Bash", "turn_id": "t-no-env-001"})

    # Confirm CODEX_HOOK_INPUT is NOT in env (mimics real Codex invocation)
    env = {"PATH": "/usr/bin:/bin"}
    assert "CODEX_HOOK_INPUT" not in env

    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout.strip())
    assert data.get("continue") is True


def test_hook_script_env_var_fallback_still_works(tmp_path: Path) -> None:
    """Fallback: CODEX_HOOK_INPUT env var path still works (forward compat).

    Kept for forward compatibility in case a future Codex version uses env var.
    When stdin is empty AND CODEX_HOOK_INPUT is set, the env var should be used.
    """
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    env_payload = json.dumps({
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-old\n+new"
        },
        "turn_id": "t-env-fallback-001",
        "tool_use_id": "u-env-fallback-001",
    })

    # No stdin, but set env var — tests the fallback path
    env = {"PATH": "/usr/bin:/bin", "CODEX_HOOK_INPUT": env_payload}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input="",  # empty stdin
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout.strip())
    assert data.get("continue") is True

    # Verify telemetry was written via env var fallback
    telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    assert telemetry_path.exists(), "Telemetry not written via env var fallback"
    events = [json.loads(line) for line in telemetry_path.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["turn_id"] == "t-env-fallback-001"


# ---------------------------------------------------------------------------
# Test 4: Hook script docstring documents confirmed mechanism
# ---------------------------------------------------------------------------


def test_hook_script_documents_stdin_as_primary() -> None:
    """Verified: hook script docstring documents stdin as primary mechanism.

    Ensures the hook's docstring accurately describes the confirmed delivery
    mechanism (not the pre-verification assumption of env var first).
    """
    from trw_mcp.channels.codex._post_tool_use_telemetry import HOOK_SCRIPT_CONTENT

    assert "stdin" in HOOK_SCRIPT_CONTENT.lower(), (
        "Hook script content must mention stdin as the delivery mechanism."
    )
    # Verify the ordering: stdin mentioned before CODEX_HOOK_INPUT in docstring
    stdin_idx = HOOK_SCRIPT_CONTENT.lower().find("primary: reads stdin")
    fallback_idx = HOOK_SCRIPT_CONTENT.lower().find("fallback: codex_hook_input")
    assert stdin_idx < fallback_idx, (
        "Hook script must document stdin as primary, CODEX_HOOK_INPUT as fallback. "
        f"stdin_idx={stdin_idx}, fallback_idx={fallback_idx}"
    )


# ---------------------------------------------------------------------------
# Test 5: Codex binary version detection (skip guard for future codex updates)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _codex_available(),
    reason="codex binary not available — skipping version check.",
)
def test_codex_version_matches_verified_version() -> None:
    """Guard: if Codex version changes, flag for re-verification.

    This test captures the version at verification time (0.133.0).
    If the installed version changes significantly (major/minor bump),
    re-run scripts/verify-codex-hook-input.sh to confirm the mechanism
    hasn't changed.
    """
    result = subprocess.run(
        [CODEX_BIN, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"codex --version failed: {result.stderr}"
    version_output = result.stdout.strip()

    # Verified at 0.133.0. Warn (don't fail) on version change.
    verified_version = "0.133.0"
    if verified_version not in version_output:
        pytest.warns(
            UserWarning,
            match=f"Codex version changed from {verified_version}",
        )
        # Not a hard failure — mechanism may be unchanged — but flag for re-verification
        import warnings
        warnings.warn(
            f"Codex version changed from verified {verified_version} to "
            f"'{version_output}'. Re-run scripts/verify-codex-hook-input.sh "
            f"to confirm hook input delivery mechanism is still stdin.",
            UserWarning,
            stacklevel=1,
        )
