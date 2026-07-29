from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def _import_pipeline() -> Any:
    """Import TelemetryPipeline, skipping the test if the module is absent."""
    try:
        mod = importlib.import_module("trw_mcp.telemetry.pipeline")
        return mod.TelemetryPipeline
    except ModuleNotFoundError:
        pytest.skip("trw_mcp.telemetry.pipeline not yet implemented")


@pytest.fixture
def pipeline_cls(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Return the TelemetryPipeline class, stopping every instance the test builds.

    ``enqueue()`` lazily calls ``start()`` (pipeline.py) and registers an
    ``atexit`` drain, so *any* pipeline a test constructs owns a live daemon
    flush thread plus an interpreter-shutdown hook. Neither is bound to the
    test's lifetime: the timer's first tick and the atexit drain both fire after
    ``monkeypatch`` has reverted the path/config patches that made the pipeline's
    environment fake, so they resolve and write against whatever is real at that
    moment. That is how fixture events (``duration_ms: 42``,
    ``framework_version: "v99.0_TEST"``) reached the repository's own
    ``.trw/logs/pipeline-events.jsonl``.

    ``tests/_path_isolation`` is what now keeps those late writes off the real
    repo. This teardown closes the leak itself: an unstopped pipeline is still a
    thread leak, still holds an atexit hook, and still writes into the *next*
    test's tmp dir.

    Tracking is done by wrapping ``__init__`` rather than by returning a factory,
    so callers keep the real class and ``pipeline_cls.get_instance()`` /
    ``.reset()`` / ``._instance = ...`` continue to work — and singletons built
    by production code during the test are tracked too.
    """
    cls = _import_pipeline()
    built: list[Any] = []
    original_init = cls.__init__

    def _tracking_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        built.append(self)

    monkeypatch.setattr(cls, "__init__", _tracking_init)
    # Exposed so the isolation guard can assert tracking actually happens rather
    # than assert the wrapper merely exists (tests/test_trw_dir_isolation_guard.py).
    monkeypatch.setattr(cls, "_test_tracked_instances", built, raising=False)
    yield cls
    for instance in built:
        try:
            instance.stop(drain=False, timeout=5.0)
        except Exception:  # justified: teardown must not mask the test's own failure
            pass


def _patch_trw_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Route telemetry path resolution into the test tmp_path."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("trw_mcp.telemetry.pipeline.resolve_trw_dir", lambda: trw_dir, raising=False)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    return trw_dir


def _make_fake_cfg(
    *,
    telemetry_enabled: bool = True,
    platform_telemetry_enabled: bool = True,
    effective_platform_urls: list[str] | None = None,
    platform_api_key: str = "",
    installation_id: str = "test-install-id",
    framework_version: str = "v99.0_TEST",
    logs_dir: str = "logs",
    telemetry_file: str = "pipeline-events.jsonl",
) -> MagicMock:
    """Build the minimal config mock needed by TelemetryPipeline tests."""
    fake_cfg = MagicMock()
    fake_cfg.telemetry_enabled = telemetry_enabled
    fake_cfg.platform_telemetry_enabled = platform_telemetry_enabled
    fake_cfg.effective_platform_urls = effective_platform_urls or []
    fake_cfg.platform_api_key.get_secret_value.return_value = platform_api_key
    fake_cfg.installation_id = installation_id
    fake_cfg.framework_version = framework_version
    fake_cfg.logs_dir = logs_dir
    fake_cfg.telemetry_file = telemetry_file
    return fake_cfg


def make_configured_pipeline(
    pipeline_cls: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cfg: Any | None = None,
    pipeline_kwargs: dict[str, Any] | None = None,
) -> tuple[Any, Path]:
    """Create a pipeline with patched paths and config, returning (pipeline, trw_dir)."""
    trw_dir = _patch_trw_dir(monkeypatch, tmp_path)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg or _make_fake_cfg(), raising=False)
    return pipeline_cls(**(pipeline_kwargs or {})), trw_dir


@pytest.fixture
def fast_pipeline(pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Pipeline with fast flush interval and JSONL routed to tmp_path."""
    pipeline, _ = make_configured_pipeline(
        pipeline_cls,
        tmp_path,
        monkeypatch,
        pipeline_kwargs={
            "flush_interval_secs": 0.1,
            "batch_size": 100,
            "max_retries": 1,
            "backoff_base": 0.0,
        },
    )
    return pipeline


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read all non-empty lines from a JSONL file."""
    if not path.exists():
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [json.loads(line) for line in lines if line]


def _make_event(**kwargs: object) -> dict[str, object]:
    """Build a minimal event dict, merging any extra kwargs."""
    base: dict[str, object] = {
        "event_type": "tool_invocation",
        "tool_name": "trw_learn",
        "duration_ms": 42,
    }
    base.update(kwargs)
    return base
