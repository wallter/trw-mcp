"""MEAS-001 FR-11 config E2E resolution tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state._paths import pin_active_run, unpin_active_run
from trw_mcp.telemetry.tool_call_timing import clear_pricing_cache


def _get_production_tool_fn(tool_name: str) -> Any:
    import trw_mcp.server._tools  # noqa: F401
    from trw_mcp.server._app import mcp

    components = getattr(getattr(mcp, "_local_provider"), "_components", {})
    for key, component in components.items():
        if key.startswith(f"tool:{tool_name}@"):
            fn = getattr(component, "fn", None) or getattr(component, "func", None)
            if callable(fn):
                return fn
    pytest.fail(f"Production MCP tool {tool_name!r} not found.")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def meas_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    run_dir = trw_dir / "runs" / "task" / "run-123"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (meta_dir / "run.yaml").write_text(
        "\n".join(
            (
                "run_id: run-123",
                "status: active",
                "phase: implement",
                "task: task",
                "owner_session_id: sess-123",
                "surface_snapshot_id: snap-123",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (meta_dir / "run_surface_snapshot.yaml").write_text("snapshot_id: snap-123\nartifacts: []\n")

    monkeypatch.setenv("TRW_SESSION_ID", "sess-123")
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.build._registration.resolve_trw_dir", lambda: trw_dir)
    pin_active_run(run_dir, session_id="sess-123")
    try:
        yield run_dir
    finally:
        unpin_active_run(session_id="sess-123")
        _reset_config(None)
        clear_pricing_cache()


def test_pricing_table_path_override_reflected_in_tool_call_event(
    meas_run: Path,
) -> None:
    """Non-default pricing table path must affect the observed ToolCallEvent."""
    custom_pricing = meas_run.parent.parent.parent / "custom-pricing.yaml"
    custom_pricing.write_text(
        "\n".join(
            (
                "version: custom-2026-04-24",
                "models:",
                "  claude-opus-4-7:",
                "    input_per_1k: 0.123",
                "    output_per_1k: 0.456",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = TRWConfig()
    object.__setattr__(cfg, "pricing_table_path", str(custom_pricing))
    _reset_config(cfg)
    clear_pricing_cache()

    tool_fn = _get_production_tool_fn("trw_build_check")
    tool_fn(
        tests_passed=True,
        test_count=1,
        coverage_pct=99.0,
        mypy_clean=True,
        run_path=str(meas_run),
    )

    events_file = next((meas_run / "meta").glob("events-*.jsonl"))
    tool_rows = [row for row in _read_jsonl(events_file) if row["event_type"] == "tool_call"]
    assert tool_rows
    assert tool_rows[-1]["payload"]["pricing_version"] == "custom-2026-04-24"
