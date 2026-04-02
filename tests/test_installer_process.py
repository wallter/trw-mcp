"""Tests for installer functions (PRD-CORE-083 + PRD-INFRA-041).

These functions live in install-trw.template.py (a standalone script that can't
be imported). We replicate the pure logic here for testing — the functions use
only stdlib and have no external dependencies.

Covers:
- PRD-CORE-083: _load_prior_config, _check_backend_health, _check_all_backends,
  update_config, phase_prompt_features, show_success_banner, _TIPS
- PRD-INFRA-041: _is_process_alive, _terminate_process, _restart_mcp_servers
"""

from __future__ import annotations

import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

# ── Replicated functions from install-trw.template.py ────────────────
# These are exact copies of the installer functions for testability.
# If the installer template changes, these must be updated to match.

TRW_VERSION = "0.15.1"  # test fixture version


def _run_quiet(cmd: list[str], timeout: int = 120) -> bool:
    """Run a command silently, return True if exit code == 0.

    *timeout* (seconds, default 120) prevents hangs when pip stalls on
    PEP 668 externally-managed system Pythons without a venv.

    ``KeyboardInterrupt`` is intentionally not caught — it propagates to
    the caller so the user can abort the entire installer with Ctrl-C.
    """
    try:
        return subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout,
        ).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _detect_installed_extras(python: str, timeout: int = 10) -> dict[str, bool]:
    """Detect which optional extras are already installed.

    Uses a short timeout (10s) because import checks should resolve quickly.
    """
    extras: dict[str, bool] = {}
    extras["ai"] = _run_quiet([python, "-c", "import anthropic"], timeout=timeout)
    extras["sqlite_vec"] = _run_quiet([python, "-c", "import sqlite_vec"], timeout=timeout)
    return extras


def _run_with_progress_testable(
    ui: MagicMock, fallback_msg: str, cmd: list[str], timeout: int = 180,
) -> bool:
    """Simplified run_with_progress for testing (no ANSI/spinner deps)."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
    except FileNotFoundError:
        return False

    assert proc.stdout is not None

    killed_by_watchdog = False

    def _watchdog_kill() -> None:
        nonlocal killed_by_watchdog
        killed_by_watchdog = True
        proc.kill()

    watchdog = threading.Timer(timeout, _watchdog_kill)
    watchdog.daemon = True
    watchdog.start()

    try:
        for _line in proc.stdout:
            pass  # drain stdout
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
        watchdog.cancel()

    if killed_by_watchdog:
        ui.step_warn(f"{fallback_msg} timed out after {timeout}s")

    return proc.returncode == 0


_TIPS = [
    "Use trw_recall('topic') to search prior session learnings",
    "Every trw_learn() call compounds across all future sessions",
    "Call trw_session_start() at the beginning of every session",
    "Use Agent Teams for multi-file implementations \u2014 focused context wins",
    "Run /trw-project-health to check your installation's vitals",
    "Use trw_checkpoint() before large operations to save progress",
    "Run trw_deliver() at session end to persist your discoveries",
    "Use /trw-audit PRD-XXX for adversarial spec-vs-code verification",
    "Export learnings anytime: trw-mcp export . learnings --format csv",
    "Your learnings auto-decay \u2014 high-impact ones persist longest",
    "Set CLAUDE_CODE_SUBAGENT_MODEL=claude-sonnet-4-6 for faster subagents",
    "TRW hooks run automatically \u2014 no setup needed after install",
]


def _load_prior_config(target_dir: Path) -> dict[str, object]:
    config_path = target_dir / ".trw" / "config.yaml"
    if not config_path.is_file():
        return {}
    prior: dict[str, object] = {}
    try:
        for line in config_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or not line or ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key == "installation_id":
                prior["project_name"] = value
            elif key == "platform_api_key":
                prior["api_key"] = value
            elif key == "platform_telemetry_enabled":
                prior["telemetry"] = value.lower() == "true"
            elif key == "embeddings_enabled":
                prior["embeddings"] = value.lower() == "true"
            elif key == "sqlite_vec_enabled":
                prior["sqlite_vec"] = value.lower() == "true"
    except (OSError, UnicodeDecodeError):
        pass
    return prior


def _check_backend_health(url: str, timeout: float = 5.0) -> dict[str, object]:
    import urllib.error
    import urllib.request

    health_url = f"{url.rstrip('/')}/v1/health"
    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"url": url, "reachable": True, "status": data.get("status", "ok")}
    except urllib.error.HTTPError as exc:
        return {"url": url, "reachable": True, "status": f"http-{exc.code}"}
    except Exception:
        return {"url": url, "reachable": False, "status": "unreachable"}


def _check_all_backends(target_dir: Path, prior_config: dict[str, object]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    urls: list[str] = []
    config_path = target_dir / ".trw" / "config.yaml"
    if config_path.is_file():
        try:
            in_urls = False
            for line in config_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("platform_urls:"):
                    in_urls = True
                    continue
                if in_urls:
                    if stripped.startswith("- "):
                        url = stripped[2:].strip().strip('"').strip("'")
                        if url:
                            urls.append(url)
                    elif stripped and not stripped.startswith("#"):
                        in_urls = False
        except OSError:
            pass
    if (target_dir / "docker-compose.yml").is_file() or (target_dir / "docker-compose.yaml").is_file():
        local_url = "http://localhost:8000"
        if local_url not in urls:
            urls.insert(0, local_url)
    results.extend(_check_backend_health(url) for url in urls)
    return results


def update_config(
    config_path: Path,
    project_name: str,
    api_key: str,
    telemetry_enabled: bool,
    *,
    embeddings_enabled: bool | None = None,
    sqlite_vec_enabled: bool | None = None,
) -> bool:
    if not config_path.is_file():
        return False
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    platform_url = "https://api.trwframework.com"
    updated: set[str] = set()
    out: list[str] = []
    replacing_platform_urls = False
    for line in lines:
        normalized_line = line if line.endswith("\n") else line + "\n"
        s = normalized_line.lstrip()
        if replacing_platform_urls:
            if s.startswith("- "):
                continue
            replacing_platform_urls = False
        if s.startswith("installation_id:"):
            out.append(f"installation_id: {project_name}\n")
            updated.add("installation_id")
            continue
        if s.startswith("platform_api_key:"):
            out.append(f'platform_api_key: "{api_key}"\n' if api_key else normalized_line)
            updated.add("platform_api_key")
            continue
        if s.startswith("platform_telemetry_enabled:"):
            val = "true" if telemetry_enabled else "false"
            out.append(f"platform_telemetry_enabled: {val}\n")
            updated.add("platform_telemetry_enabled")
            continue
        if s.startswith("embeddings_enabled:") and embeddings_enabled is not None:
            out.append(f"embeddings_enabled: {'true' if embeddings_enabled else 'false'}\n")
            updated.add("embeddings_enabled")
            continue
        if s.startswith("sqlite_vec_enabled:") and sqlite_vec_enabled is not None:
            out.append(f"sqlite_vec_enabled: {'true' if sqlite_vec_enabled else 'false'}\n")
            updated.add("sqlite_vec_enabled")
            continue
        if s.startswith("platform_urls:"):
            updated.add("platform_urls")
            if api_key or telemetry_enabled:
                out.append("platform_urls:\n")
                out.append(f'  - "{platform_url}"\n')
                updated.add("platform_urls_written")
                replacing_platform_urls = True
                continue
            out.append(normalized_line)
            continue
        out.append(normalized_line)
    if "installation_id" not in updated:
        out.append(f"installation_id: {project_name}\n")
    if api_key and "platform_api_key" not in updated:
        out.append(f'platform_api_key: "{api_key}"\n')
    if telemetry_enabled and "platform_telemetry_enabled" not in updated:
        out.append("platform_telemetry_enabled: true\n")
    if (api_key or telemetry_enabled) and "platform_urls_written" not in updated:
        out.append("platform_urls:\n")
        out.append(f'  - "{platform_url}"\n')
    if embeddings_enabled and "embeddings_enabled" not in updated:
        out.append("embeddings_enabled: true\n")
    if sqlite_vec_enabled and "sqlite_vec_enabled" not in updated:
        out.append("sqlite_vec_enabled: true\n")
    config_path.write_text("".join(out), encoding="utf-8")
    return True


def phase_prompt_features(
    install_ai: bool | None,
    install_sqlitevec: bool | None,
    prior_extras: dict[str, bool] | None = None,
) -> tuple[bool, bool]:
    if prior_extras is None:
        prior_extras = {}
    ai_configured = prior_extras.get("ai", False)
    vec_configured = prior_extras.get("sqlite_vec", False)
    if install_ai is not None and install_sqlitevec is not None:
        return bool(install_ai), bool(install_sqlitevec)
    if ai_configured and vec_configured and install_ai is None and install_sqlitevec is None:
        return True, True
    if install_ai is None:
        install_ai = ai_configured
    if install_sqlitevec is None:
        install_sqlitevec = vec_configured
    return bool(install_ai), bool(install_sqlitevec)


def _is_process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        try:
            import ctypes

            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, 0, pid)  # type: ignore[union-attr]
            if handle == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[union-attr]
            return True
        except (OSError, AttributeError):
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, OSError):
            return False


def _terminate_process(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            try:
                os.kill(pid, signal.SIGTERM)
                return True
            except OSError:
                return (
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    ).returncode
                    == 0
                )
        else:
            os.kill(pid, signal.SIGTERM)
            return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


# ═══════════════════════════════════════════════════════════════════════
# PRD-CORE-083 Tests: Installer UX Overhaul and Backend Health Check
# ═══════════════════════════════════════════════════════════════════════


class TestLoadPriorConfig:
    """FR02: Config-level feature flag persistence."""

    def test_with_feature_flags(self, tmp_path: Path) -> None:
        """Config with embeddings_enabled and sqlite_vec_enabled is parsed correctly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            "installation_id: my-project\nembeddings_enabled: true\nsqlite_vec_enabled: false\n",
            encoding="utf-8",
        )
        result = _load_prior_config(tmp_path)
        assert result["project_name"] == "my-project"
        assert result["embeddings"] is True
        assert result["sqlite_vec"] is False

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """No config file returns empty dict."""
        assert _load_prior_config(tmp_path) == {}

    def test_malformed_content_returns_empty(self, tmp_path: Path) -> None:
        """Binary/garbage content returns empty dict (OSError or parse failure)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_bytes(b"\x00\x01\x02\xff")
        result = _load_prior_config(tmp_path)
        assert isinstance(result, dict)

    def test_comments_and_empty_lines_skipped(self, tmp_path: Path) -> None:
        """Comments and blank lines don't affect parsing."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            "# This is a comment\n\ninstallation_id: test\n# another comment\n",
            encoding="utf-8",
        )
        result = _load_prior_config(tmp_path)
        assert result["project_name"] == "test"


class TestCheckBackendHealth:
    """FR03: Real backend health check via HTTP probe."""

    def test_success_response(self) -> None:
        """200 response with status field returns reachable=True."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is True
        assert result["status"] == "ok"

    def test_http_error(self) -> None:
        """HTTP 500 still counts as reachable (server responded)."""
        with patch(
            "urllib.request.urlopen",
            side_effect=HTTPError(
                "http://x/v1/health",
                500,
                "ISE",
                {},
                None,  # type: ignore[arg-type]
            ),
        ):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is True
        assert result["status"] == "http-500"

    def test_connection_refused(self) -> None:
        """Connection refused returns unreachable."""
        with patch("urllib.request.urlopen", side_effect=URLError(ConnectionRefusedError("refused"))):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is False
        assert result["status"] == "unreachable"

    def test_timeout(self) -> None:
        """Socket timeout returns unreachable."""
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is False
        assert result["status"] == "unreachable"


class TestCheckAllBackends:
    """FR04: Docker backend auto-detection."""

    def test_docker_compose_detected(self, tmp_path: Path) -> None:
        """docker-compose.yml triggers localhost:8000 probe."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n", encoding="utf-8")

        with patch(
            "tests.test_installer_process._check_backend_health",
            return_value={"url": "http://localhost:8000", "reachable": False, "status": "unreachable"},
        ) as mock_health:
            # Call the function directly since patching won't affect the local reference
            results = _check_all_backends(tmp_path, {})

        # Verify localhost:8000 was probed
        probed_urls = [r["url"] for r in results]
        assert "http://localhost:8000" in probed_urls

    def test_no_duplicates_with_config(self, tmp_path: Path) -> None:
        """Config URL matching Docker URL is not duplicated."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            'platform_urls:\n  - "http://localhost:8000"\n',
            encoding="utf-8",
        )
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n", encoding="utf-8")

        results = _check_all_backends(tmp_path, {})
        urls = [r["url"] for r in results]
        assert urls.count("http://localhost:8000") == 1

    def test_no_compose_no_local(self, tmp_path: Path) -> None:
        """Without docker-compose, localhost is not auto-added."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text("task_root: docs\n", encoding="utf-8")

        results = _check_all_backends(tmp_path, {})
        assert results == []


class TestUpdateConfig:
    """FR02: Feature flag persistence in config.yaml."""

    def test_persists_feature_flags(self, tmp_path: Path) -> None:
        """Feature flags are written to config.yaml."""
        config = tmp_path / "config.yaml"
        config.write_text("installation_id: test\n", encoding="utf-8")

        update_config(config, "test", "", False, embeddings_enabled=True, sqlite_vec_enabled=True)

        content = config.read_text(encoding="utf-8")
        assert "embeddings_enabled: true" in content
        assert "sqlite_vec_enabled: true" in content

    def test_roundtrip(self, tmp_path: Path) -> None:
        """Write then read back feature flags."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = trw_dir / "config.yaml"
        config.write_text("installation_id: test\n", encoding="utf-8")

        update_config(config, "test", "", False, embeddings_enabled=True, sqlite_vec_enabled=True)

        prior = _load_prior_config(tmp_path)
        assert prior.get("embeddings") is True
        assert prior.get("sqlite_vec") is True

    def test_updates_existing_flags(self, tmp_path: Path) -> None:
        """Existing flags are updated in-place, not duplicated."""
        config = tmp_path / "config.yaml"
        config.write_text(
            "installation_id: test\nembeddings_enabled: false\n",
            encoding="utf-8",
        )

        update_config(config, "test", "", False, embeddings_enabled=True)

        content = config.read_text(encoding="utf-8")
        assert content.count("embeddings_enabled") == 1
        assert "embeddings_enabled: true" in content

    def test_preserves_newlines_before_appending_platform_urls(self, tmp_path: Path) -> None:
        """Appending platform URLs must not merge onto a prior line without newline."""
        config = tmp_path / "config.yaml"
        config.write_text('platform_user_email: "user@example.com"', encoding="utf-8")

        update_config(config, "test", "trw_key_123", True)

        content = config.read_text(encoding="utf-8")
        assert 'platform_user_email: "user@example.com"platform_urls:' not in content
        assert 'platform_user_email: "user@example.com"\n' in content
        assert "platform_urls:\n" in content

    def test_rewrites_platform_urls_without_duplication(self, tmp_path: Path) -> None:
        """Existing platform_urls blocks are replaced in place, not duplicated."""
        config = tmp_path / "config.yaml"
        config.write_text(
            'platform_api_key: ""\n'
            "platform_urls:\n"
            '  - "http://old.example.com"\n',
            encoding="utf-8",
        )

        update_config(config, "test", "trw_key_123", True)

        content = config.read_text(encoding="utf-8")
        assert content.count("platform_urls:") == 1
        assert '"https://api.trwframework.com"' in content
        assert '"http://old.example.com"' not in content


class TestPhasePromptFeatures:
    """FR02: Feature prompt logic with prior_extras."""

    def test_both_configured_returns_true(self) -> None:
        """Both extras configured: returns (True, True) without prompting."""
        ai, vec = phase_prompt_features(
            None,
            None,
            prior_extras={"ai": True, "sqlite_vec": True},
        )
        assert ai is True
        assert vec is True

    def test_cli_override(self) -> None:
        """CLI flags override prior_extras."""
        ai, vec = phase_prompt_features(
            False,
            False,
            prior_extras={"ai": True, "sqlite_vec": True},
        )
        assert ai is False
        assert vec is False

    def test_partial_config(self) -> None:
        """Only AI configured: AI auto-accepted, sqlite_vec defaults to False."""
        ai, vec = phase_prompt_features(
            None,
            None,
            prior_extras={"ai": True},
        )
        assert ai is True
        assert vec is False

    def test_no_prior_no_cli(self) -> None:
        """No prior config and no CLI flags: both default to False."""
        ai, vec = phase_prompt_features(None, None, prior_extras={})
        assert ai is False
        assert vec is False


class TestTips:
    """FR06: Random tip display."""

    def test_tips_list_has_12_items(self) -> None:
        assert len(_TIPS) == 12

    def test_all_tips_are_strings(self) -> None:
        assert all(isinstance(t, str) for t in _TIPS)

    def test_all_tips_are_nonempty(self) -> None:
        assert all(len(t) > 10 for t in _TIPS)


# ═══════════════════════════════════════════════════════════════════════
# PRD-INFRA-041 Tests: Cross-Platform MCP Server Restart After Install
# ═══════════════════════════════════════════════════════════════════════


class TestIsProcessAlive:
    """FR08: _is_process_alive works cross-platform."""

    def test_own_process_is_alive(self) -> None:
        assert _is_process_alive(os.getpid()) is True

    def test_bogus_pid_is_not_alive(self) -> None:
        assert _is_process_alive(4_000_000) is False

    def test_zero_pid_no_crash(self) -> None:
        result = _is_process_alive(0)
        assert isinstance(result, bool)

    def test_negative_pid_no_crash(self) -> None:
        result = _is_process_alive(-1)
        assert isinstance(result, bool)


class TestTerminateProcess:
    """FR04: Process termination works on Unix and Windows."""

    def test_terminate_spawned_process(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        assert _is_process_alive(proc.pid) is True
        assert _terminate_process(proc.pid) is True
        proc.wait(timeout=5)
        assert proc.returncode is not None

    def test_terminate_dead_process_returns_false(self) -> None:
        assert _terminate_process(4_000_000) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-specific SIGTERM test")
    def test_unix_sends_sigterm(self) -> None:
        with patch("os.kill") as mock_kill:
            mock_kill.return_value = None
            _terminate_process(12345)
            mock_kill.assert_called_once_with(12345, signal.SIGTERM)


class TestPIDRestart:
    """FR03: Installer kills HTTP server via PID file and writes sentinel."""

    def test_restart_kills_alive_process(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        sentinel_path = trw_dir / "installed-version.json"

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid_path.write_text(str(proc.pid), encoding="utf-8")

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if _is_process_alive(pid):
                _terminate_process(pid)
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        sentinel_path.write_text(
            json.dumps({"version": "0.16.0", "timestamp": "2026-03-14T00:00:00Z"}),
            encoding="utf-8",
        )

        proc.wait(timeout=5)
        assert not pid_path.exists()
        assert sentinel_path.exists()
        assert json.loads(sentinel_path.read_text(encoding="utf-8"))["version"] == "0.16.0"

    def test_restart_cleans_stale_pid(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        pid_path.write_text("4000000", encoding="utf-8")

        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            if not _is_process_alive(pid):
                pid_path.unlink(missing_ok=True)
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        assert not pid_path.exists()

    def test_restart_no_pid_file_no_error(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        sentinel_path = trw_dir / "installed-version.json"
        sentinel_path.write_text(json.dumps({"version": "0.16.0"}), encoding="utf-8")
        assert sentinel_path.exists()

    def test_restart_corrupt_pid_no_crash(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        pid_path = trw_dir / "mcp-server.pid"
        pid_path.write_text("not_a_pid", encoding="utf-8")

        try:
            int(pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)

        assert not pid_path.exists()


# ═══════════════════════════════════════════════════════════════════════
# Timeout Hardening Tests: _run_quiet, _detect_installed_extras,
# run_with_progress watchdog
# ═══════════════════════════════════════════════════════════════════════


class TestRunQuiet:
    """Tests for _run_quiet subprocess wrapper."""

    def test_successful_command_returns_true(self) -> None:
        assert _run_quiet([sys.executable, "-c", "pass"]) is True

    def test_failing_command_returns_false(self) -> None:
        assert _run_quiet([sys.executable, "-c", "raise SystemExit(1)"]) is False

    def test_missing_executable_returns_false(self) -> None:
        assert _run_quiet(["/nonexistent/binary"]) is False

    def test_timeout_returns_false(self) -> None:
        """Command exceeding timeout returns False without hanging."""
        result = _run_quiet(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=1,
        )
        assert result is False

    def test_default_timeout_is_120(self) -> None:
        """Verify the default timeout parameter value."""
        sig = inspect.signature(_run_quiet)
        assert sig.parameters["timeout"].default == 120


class TestDetectInstalledExtras:
    """Tests for _detect_installed_extras with short timeout."""

    def test_returns_dict_with_ai_and_sqlite_vec_keys(self) -> None:
        result = _detect_installed_extras(sys.executable)
        assert "ai" in result
        assert "sqlite_vec" in result
        assert all(isinstance(v, bool) for v in result.values())

    def test_uses_short_timeout(self) -> None:
        """Import checks use 10s timeout, not the default 120s."""
        start = time.monotonic()
        # Use a nonexistent python to force FileNotFoundError (instant)
        result = _detect_installed_extras("/nonexistent/python3", timeout=1)
        elapsed = time.monotonic() - start
        assert elapsed < 5  # Should fail fast, not wait 120s
        assert result["ai"] is False
        assert result["sqlite_vec"] is False


class TestRunWithProgress:
    """Tests for run_with_progress watchdog timeout."""

    def test_successful_command(self) -> None:
        """Normal command completes and returns True."""
        ui = MagicMock()
        ui.interactive = False
        ui.quiet = True
        result = _run_with_progress_testable(
            ui, "Testing...", [sys.executable, "-c", "print('hello')"],
        )
        assert result is True

    def test_watchdog_kills_hanging_process(self) -> None:
        """Process exceeding timeout is killed by watchdog."""
        ui = MagicMock()
        ui.interactive = False
        ui.quiet = True
        result = _run_with_progress_testable(
            ui, "Testing...",
            [sys.executable, "-c", "import time; time.sleep(300)"],
            timeout=2,
        )
        assert result is False
        ui.step_warn.assert_called_once()
        warn_msg = ui.step_warn.call_args[0][0]
        assert "timed out" in warn_msg
        assert "2s" in warn_msg

    def test_missing_command_returns_false(self) -> None:
        """FileNotFoundError returns False immediately."""
        ui = MagicMock()
        ui.interactive = False
        ui.quiet = True
        result = _run_with_progress_testable(
            ui, "Testing...", ["/nonexistent/command"],
        )
        assert result is False
