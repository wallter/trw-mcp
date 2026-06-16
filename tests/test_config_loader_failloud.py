"""PRD-QUAL-110-FR01: fail-loud config loader.

A malformed ``.trw/config.yaml`` must no longer be swallowed at DEBUG and
silently reverted to defaults — that path discards every operator hardening
override without a trace. The loader now:

  * logs at WARNING (not DEBUG) on any config-load failure, and
  * writes a one-line notice to stderr, and
  * optionally fails closed (re-raises) when ``TRW_CONFIG_STRICT=1`` is set,
    so security-relevant overrides are never silently dropped.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from trw_mcp.models.config import _loader, get_config, reload_config


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    (tmp_path / "proj" / ".trw").mkdir(parents=True)
    return tmp_path / "proj"


@pytest.fixture(autouse=True)
def _isolate(project_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project_dir)
    monkeypatch.delenv("TRW_CONFIG_STRICT", raising=False)
    reload_config()
    yield
    reload_config()


def _write_malformed(project_dir: Path) -> None:
    # An unparseable scalar/mapping mix that makes the safe YAML loader raise,
    # OR a mapping whose value coerces to a type TRWConfig rejects. We use a
    # type that pydantic validation rejects so the *build* fails loudly even if
    # the YAML itself parses.
    (project_dir / ".trw" / "config.yaml").write_text("build_check_coverage_min: not-a-float\n", encoding="utf-8")


def test_malformed_config_emits_warning_not_debug(project_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A malformed config logs at WARNING and writes a stderr notice."""
    _write_malformed(project_dir)
    with capture_logs() as logs:
        cfg = get_config()
    # Falls back to a usable default config (fail-open default posture).
    assert cfg is not None
    levels = {entry.get("log_level") for entry in logs}
    events = {entry.get("event") for entry in logs}
    assert "config_load_failed" in events
    assert "warning" in levels
    # No DEBUG-only swallow: the failure must be a warning, not a debug line.
    failure = next(e for e in logs if e.get("event") == "config_load_failed")
    assert failure.get("log_level") == "warning"
    # Loud stderr notice for operators tailing the process, not just logs.
    captured = capsys.readouterr()
    assert "config" in captured.err.lower()


def test_strict_mode_fails_closed_on_malformed(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With TRW_CONFIG_STRICT=1 a malformed config raises instead of reverting."""
    _write_malformed(project_dir)
    monkeypatch.setenv("TRW_CONFIG_STRICT", "1")
    reload_config()
    with pytest.raises(Exception):
        get_config()


def test_valid_config_loads_without_warning(
    project_dir: Path,
) -> None:
    """A valid config does not emit the fail-load warning (no regression)."""
    (project_dir / ".trw" / "config.yaml").write_text("task_root: from-project\n", encoding="utf-8")
    with capture_logs() as logs:
        cfg = get_config()
    assert cfg.task_root == "from-project"
    events = {entry.get("event") for entry in logs}
    assert "config_load_failed" not in events


def test_strict_mode_valid_config_still_loads(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Strict mode must not reject a VALID config (RISK-001 guard)."""
    (project_dir / ".trw" / "config.yaml").write_text("task_root: from-project\n", encoding="utf-8")
    monkeypatch.setenv("TRW_CONFIG_STRICT", "1")
    reload_config()
    cfg = get_config()
    assert cfg.task_root == "from-project"


def test_loader_module_has_warning_signal() -> None:
    """Regression guard: the loader emits a warning, not only a debug line."""
    src = Path(_loader.__file__).read_text(encoding="utf-8")
    assert "logger.warning" in src
    assert structlog is not None
