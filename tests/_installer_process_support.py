"""Shared replicated installer helpers for installer process tests."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

TRW_VERSION = "0.15.1"  # test fixture version


def _run_quiet(cmd: list[str], timeout: int = 120) -> bool:
    """Run a command silently, return True if exit code == 0.

    *timeout* (seconds, default 120) prevents hangs when pip stalls on
    PEP 668 externally-managed system Pythons without a venv.

    ``KeyboardInterrupt`` is intentionally not caught — it propagates to
    the caller so the user can abort the entire installer with Ctrl-C.
    """
    try:
        return (
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            ).returncode
            == 0
        )
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
    ui: MagicMock,
    fallback_msg: str,
    cmd: list[str],
    timeout: int = 180,
) -> bool:
    """Simplified run_with_progress for testing (no ANSI/spinner deps)."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
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
            pass
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
        watchdog.cancel()
        watchdog.join()
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    if killed_by_watchdog:
        ui.step_warn(f"{fallback_msg} timed out after {timeout}s")

    return proc.returncode == 0


_TIPS = [
    "Use trw_recall('topic') to search prior session learnings",
    "Every trw_learn() call compounds across all future sessions",
    "Call trw_session_start() at the beginning of every session",
    "Use focused helpers for multi-file implementations when your harness supports them — focused context wins",
    "Run /trw-project-health to check your installation's vitals",
    "Use trw_checkpoint() before large operations to save progress",
    "Run trw_deliver() at session end to persist your discoveries",
    "Use /trw-audit PRD-XXX for adversarial spec-vs-code verification",
    "Export learnings anytime: trw-mcp export . learnings --format csv",
    "Your learnings auto-decay — high-impact ones persist longest",
    "Choose full profiles for local IDEs and light profiles for CI-oriented CLIs",
    "TRW hooks run automatically — no setup needed after install",
]

_SUPPORTED_IDES = [
    "claude-code",
    "cursor-ide",
    "cursor-cli",
    "opencode",
    "codex",
    "copilot",
    "gemini",
    "aider",
    "antigravity-cli",
]


def _normalize_ide_targets(ides: list[str]) -> list[str]:
    normalized = [ide.strip() for ide in ides if ide.strip()]
    if not normalized:
        return []
    if "all" in normalized:
        return _SUPPORTED_IDES.copy()
    invalid = [ide for ide in normalized if ide not in _SUPPORTED_IDES]
    if invalid:
        supported = ", ".join([*_SUPPORTED_IDES, "all"])
        raise ValueError(f"Unknown --ide value(s): {', '.join(invalid)}. Supported values: {supported}")
    return list(dict.fromkeys(normalized))


def _load_prior_config(target_dir: Path) -> dict[str, object]:
    config_path = target_dir / ".trw" / "config.yaml"
    if not config_path.is_file():
        return {}
    prior: dict[str, object] = {}
    try:
        raw_text = config_path.read_text(encoding="utf-8")
        in_target_platforms = False
        in_platform_urls = False
        target_platforms: list[str] = []
        platform_urls: list[str] = []
        for line in raw_text.splitlines():
            line = line.strip()
            if line.startswith("platform_urls:"):
                in_platform_urls = True
                in_target_platforms = False
                continue
            if in_platform_urls:
                if line.startswith("- "):
                    platform_urls.append(line[2:].strip().strip('"').strip("'"))
                    continue
                if line and not line.startswith("#"):
                    in_platform_urls = False
            if in_target_platforms:
                if line.startswith("- "):
                    target_platforms.append(line[2:].strip().strip('"').strip("'"))
                    continue
                if line and not line.startswith("#"):
                    in_target_platforms = False
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
            elif key == "target_platforms":
                in_target_platforms = True
                in_platform_urls = False
        if platform_urls:
            prior["platform_urls"] = [url for url in platform_urls if url]
        if target_platforms:
            prior["target_platforms"] = _normalize_ide_targets(target_platforms)
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
    target_platforms: list[str] | None = None,
    rewrite_platform_urls: bool = True,
) -> bool:
    if not config_path.is_file():
        return False
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    platform_url = "https://api.trwframework.com"
    effective_target_platforms = target_platforms or None
    updated: set[str] = set()
    out: list[str] = []
    replacing_platform_urls = False
    replacing_target_platforms = False
    for line in lines:
        normalized_line = line if line.endswith("\n") else line + "\n"
        s = normalized_line.lstrip()
        stripped = s.strip()
        if replacing_target_platforms:
            if not stripped or stripped.startswith(("#", "- ")):
                continue
            replacing_target_platforms = False
        if replacing_platform_urls:
            if not stripped or stripped.startswith(("#", "- ")):
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
        if s.startswith("target_platforms:") and effective_target_platforms is not None:
            out.append("target_platforms:\n")
            out.extend(f'  - "{ide}"\n' for ide in effective_target_platforms)
            updated.add("target_platforms")
            replacing_target_platforms = True
            continue
        if s.startswith("platform_urls:"):
            updated.add("platform_urls")
            if rewrite_platform_urls and (api_key or telemetry_enabled):
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
    if rewrite_platform_urls and (api_key or telemetry_enabled) and "platform_urls_written" not in updated:
        out.append("platform_urls:\n")
        out.append(f'  - "{platform_url}"\n')
    if embeddings_enabled and "embeddings_enabled" not in updated:
        out.append("embeddings_enabled: true\n")
    if sqlite_vec_enabled and "sqlite_vec_enabled" not in updated:
        out.append("sqlite_vec_enabled: true\n")
    if effective_target_platforms and "target_platforms" not in updated:
        out.append("target_platforms:\n")
        out.extend(f'  - "{ide}"\n' for ide in effective_target_platforms)
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
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
