"""Shared test fixtures for TRW MCP test suite.

Test Tiering Philosophy
-----------------------
Tests are auto-assigned markers based on their filename:

- **unit**: Pure logic tests — no filesystem I/O, no multi-tool interaction,
  no ``tmp_path`` usage.  Target: <30s for the full unit tier.
- **integration**: Tests that write files, call multiple tools, or use
  ``tmp_path`` / ``tmp_project`` fixtures.
- **e2e**: End-to-end workflows covering full phase sequences.
- **slow**: Tests that individually take >5s (model loading, bootstrap).

To classify a new test file:
  1. If it uses ``tmp_path``/``tmp_project`` → integration (default).
  2. If it only patches/mocks and tests pure functions → add to ``_UNIT_FILES``.
  3. If it loads heavy models or creates 100+ files → add to ``_SLOW_FILES``.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

# Prefer monorepo sources over stale site-packages when tests run from the checkout.
_TESTS_DIR = Path(__file__).resolve().parent
_TRW_MCP_SRC = _TESTS_DIR.parent / "src"
_MONOREPO_ROOT = _TESTS_DIR.parent.parent
_TRW_MEMORY_SRC = _MONOREPO_ROOT / "trw-memory" / "src"
for _path in (str(_TRW_MEMORY_SRC), str(_TRW_MCP_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync context, handling nested loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def get_tools_sync(server: FastMCP) -> dict[str, Any]:
    """Synchronously list tools from a FastMCP server.

    Replaces the broken ``server._tool_manager._tools`` internal API
    with the public ``server.list_tools()`` async method.
    """
    tools = _run_async(server.list_tools())
    return {t.name: t for t in tools}


def get_resources_sync(server: FastMCP) -> dict[str, Any]:
    """Synchronously list resources from a FastMCP server.

    Replaces the broken ``server._resource_manager`` internal API.
    """
    resources = _run_async(server.list_resources())
    return {str(r.uri): r for r in resources}


def get_prompts_sync(server: FastMCP) -> dict[str, Any]:
    """Synchronously list prompts from a FastMCP server.

    Replaces the broken ``server._prompt_manager`` internal API.
    """
    prompts = _run_async(server.list_prompts())
    return {p.name: p for p in prompts}


# --- Shared server/tool factories ---
#
# These replace the repetitive 3-step pattern found in 30+ test files:
#   srv = FastMCP("test"); register_X_tools(srv); tools = get_tools_sync(srv)


# Registry mapping short group name -> (module_path, function_name).
# Imports are deferred so conftest doesn't eagerly pull in all tool modules.
_TOOL_GROUPS: dict[str, tuple[str, str]] = {
    "build": ("trw_mcp.tools.build", "register_build_tools"),
    "ceremony": ("trw_mcp.tools.ceremony", "register_ceremony_tools"),
    "ceremony_feedback": ("trw_mcp.tools.ceremony_feedback", "register_ceremony_feedback_tools"),
    "checkpoint": ("trw_mcp.tools.checkpoint", "register_checkpoint_tools"),
    "knowledge": ("trw_mcp.tools.knowledge", "register_knowledge_tools"),
    "learning": ("trw_mcp.tools.learning", "register_learning_tools"),
    "meta_tune": ("trw_mcp.tools.meta_tune_ops", "register_meta_tune_tools"),
    "orchestration": ("trw_mcp.tools.orchestration", "register_orchestration_tools"),
    "report": ("trw_mcp.tools.report", "register_report_tools"),
    "requirements": ("trw_mcp.tools.requirements", "register_requirements_tools"),
    "review": ("trw_mcp.tools.review", "register_review_tools"),
    "usage": ("trw_mcp.tools.usage", "register_usage_tools"),
}


def make_test_server(*groups: str) -> FastMCP:
    """Create a FastMCP server with the specified tool groups registered.

    Args:
        *groups: Tool group names to register (e.g. ``"ceremony"``,
            ``"orchestration"``).  If no groups are given, a bare server
            is returned (same as ``FastMCP("test")``).

    Returns:
        A ``FastMCP`` instance with the requested tool groups registered.

    Raises:
        KeyError: If an unknown group name is passed.

    Example::

        server = make_test_server("ceremony", "checkpoint")
        tools = get_tools_sync(server)
        deliver_fn = tools["trw_deliver"].fn
    """
    import importlib

    server = FastMCP("test")
    for group in groups:
        module_path, func_name = _TOOL_GROUPS[group]
        mod = importlib.import_module(module_path)
        register_fn = getattr(mod, func_name)
        register_fn(server)
    return server


def extract_tool_fn(server: FastMCP, tool_name: str) -> Any:
    """Extract a tool's callable function from a FastMCP server by name.

    This is the shared replacement for the ``_extract_tool()`` /
    ``_get_tool_fn()`` local helpers found across test files.

    Args:
        server: A FastMCP server with tools already registered.
        tool_name: The registered tool name (e.g. ``"trw_session_start"``).

    Returns:
        The raw callable (``tool.fn``) for the named tool.

    Raises:
        KeyError: If the tool name is not found on the server.
    """
    tools = get_tools_sync(server)
    if tool_name not in tools:
        raise KeyError(f"Tool {tool_name!r} not found. Available: {sorted(tools.keys())}")
    return tools[tool_name].fn


# --- Marker auto-assignment ---

_UNIT_FILES: frozenset[str] = frozenset(
    {
        "test_models.py",
        "test_scoring.py",
        "test_scoring_branches.py",
        "test_scoring_edge_cases.py",
        "test_scoring_properties.py",
        "test_bayesian_calibration.py",
        "test_clients_llm.py",
        "test_llm_helpers.py",
        "test_middleware_ceremony.py",
        "test_middleware_context_budget.py",
        "test_middleware_compression.py",
        "test_middleware_response_optimizer.py",
        "test_prompts_messaging.py",
        "test_telemetry_embeddings.py",
        "test_telemetry_remote_recall.py",
        "test_validation_v2.py",
        "test_prd_utils_edge.py",
        "test_fix055_traceability_lang.py",
        "test_core080_template_variants.py",
        "test_response_optimizer.py",
        "test_scoring_q_preseed.py",
        # Pure model/config validation — no filesystem I/O
        "test_client_profile.py",
        "test_sprint44_models.py",
        "test_api_import.py",
        "test_fix044_module_config.py",
        "test_fix056_status_integrity.py",
        # PRD-CORE-099: Pure env-var detection — no filesystem I/O
        "test_source_detection_unit.py",
        # PRD-CORE-104: Delivery metrics — pure scoring, no I/O
        "test_composite_score.py",
        "test_sigmoid.py",
        "test_rework_rate.py",
        # PRD-CORE-116: Enhanced recall scoring — pure scoring, no I/O
        "test_core_116_recall_scoring.py",
        # PRD-INFRA-054: Import guard regression test — pure source scanning, no I/O
        "test_no_intelligence_imports.py",
        # PRD-FIX-061: Layer boundary enforcement — pure source scanning, no I/O
        "test_layer_boundaries.py",
        "test_scoring_layer_boundary.py",
        # PRD-QUAL-056: PRD scoring dimensions — pure scoring, no I/O
        "test_prd_quality_flywheel.py",
        "test_prd_file_path_coverage.py",
        "test_user_prompt_submit_hook.py",
        # PRD-CORE-125: Surface area control — pure config/model, no I/O
        "test_tool_presets.py",
        "test_surface_area_flags.py",
    }
)

_SLOW_FILES: frozenset[str] = frozenset(
    {
        "test_consolidation.py",
        "test_bootstrap.py",
        "test_bootstrap_branches.py",
        # PRD-CORE-146-NFR01: 1000-iteration latency benchmark (~1-3s)
        "test_nudge_performance.py",
    }
)

_E2E_FILES: frozenset[str] = frozenset()


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Auto-assign unit/integration/e2e/slow markers to tests without explicit markers."""
    for item in items:
        has_tier = any(m.name in ("unit", "integration", "e2e") for m in item.iter_markers())
        if has_tier:
            continue

        filename = Path(item.fspath).name

        # Assign slow marker (additive — a test can be both integration and slow)
        if filename in _SLOW_FILES:
            item.add_marker(pytest.mark.slow)

        if filename in _UNIT_FILES:
            item.add_marker(pytest.mark.unit)
        elif filename in _E2E_FILES:
            item.add_marker(pytest.mark.e2e)
        else:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _reset_config_singleton() -> Iterator[None]:
    """Reset TRWConfig singleton for test isolation."""
    _reset_config()
    yield
    _reset_config()


@pytest.fixture(autouse=True)
def _reset_run_pin() -> Iterator[None]:
    """Reset active run pin + pin-store cache for test isolation.

    PRD-CORE-141: the 1-second TTL cache in ``_pin_store`` can carry state across
    tests. Without invalidating it, a prior test's empty-dict read caches into
    the next test's malformed-file scenario and the warning never fires.
    """
    from trw_mcp.state._paths import _pinned_runs  # type: ignore[attr-defined]
    from trw_mcp.state._pin_store import invalidate_pin_store_cache

    _pinned_runs.clear()
    invalidate_pin_store_cache()
    yield
    _pinned_runs.clear()
    invalidate_pin_store_cache()


def _join_and_reset_deferred() -> None:
    """Wait for any background deliver thread, then clear the reference.

    Prevents use-after-close segfaults when conftest resets the SQLite
    backend while a deferred thread is mid-query.
    """
    try:
        import trw_mcp.tools._deferred_state as _ds

        with _ds._deferred_lock:
            t = _ds._deferred_thread
        if t is not None and t.is_alive():
            t.join(timeout=15)
        # Clear the reference so the next test starts fresh
        with _ds._deferred_lock:
            _ds._deferred_thread = None
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_memory_backend() -> Iterator[None]:
    """Reset memory adapter singleton for test isolation.

    Joins any running deferred-deliver thread first to prevent
    use-after-close segfaults on the SQLite backend.
    """
    from trw_mcp.state.memory_adapter import reset_backend

    _join_and_reset_deferred()
    reset_backend()
    yield
    _join_and_reset_deferred()
    reset_backend()


@pytest.fixture(autouse=True)
def _reset_telemetry_pipeline() -> Iterator[None]:
    """Reset TelemetryPipeline singleton between tests for isolation."""
    yield
    try:
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        TelemetryPipeline.reset()
    except Exception:  # justified: fail-open — pipeline may not be importable in all test configs
        pass


@pytest.fixture(autouse=True)
def _reset_telemetry_run_cache() -> Iterator[None]:
    """Reset the cached run directory in telemetry between tests.

    The telemetry module caches find_active_run() results with a 5-second
    TTL. Without this reset, a stale cached path from a previous test's
    tmp_path leaks into subsequent tests.
    """
    try:
        import trw_mcp.tools.telemetry as tel_mod

        tel_mod._cached_run_dir = (0.0, None)
    except Exception:  # justified: fail-open — telemetry module may not be imported
        pass
    yield
    try:
        import trw_mcp.tools.telemetry as tel_mod

        tel_mod._cached_run_dir = (0.0, None)
    except Exception:  # justified: fail-open
        pass


@pytest.fixture(autouse=True)
def _isolate_trw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect all resolve_trw_dir() and resolve_project_root() calls to tmp dirs.

    Prevents test runs from writing ceremony-feedback.yaml, tool-telemetry.jsonl,
    and analytics.yaml to the real project's .trw/ directory (PRD-FIX-050-FR01/FR02).

    Patches both the source module (_paths) and all late-import consumers in tools/.
    The _step_ceremony_feedback function uses `import trw_mcp.tools.ceremony as _cer;
    _cer.resolve_trw_dir()` — this patch covers that code path via ceremony module.
    """
    test_root = tmp_path
    test_trw_dir = test_root / ".trw"

    def _fake_trw_dir() -> Path:
        return test_trw_dir

    def _fake_project_root() -> Path:
        return test_root

    # Patch source module
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", _fake_trw_dir)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", _fake_project_root)

    # Patch late-import consumers in tools/ (critical for _step_ceremony_feedback)
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", _fake_trw_dir)
    try:
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", _fake_project_root)
    except AttributeError:
        pass  # ceremony doesn't import resolve_project_root

    # Also patch tools/learning and tools/requirements to stay consistent
    try:
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", _fake_trw_dir)
    except AttributeError:
        pass  # Not yet imported
    try:
        monkeypatch.setattr("trw_mcp.tools.requirements.resolve_project_root", _fake_project_root)
    except AttributeError:
        pass  # Not yet imported

    # Patch tools/orchestration — it does `from _paths import resolve_project_root`
    # at module level. If orchestration is first imported while _paths is already
    # patched, the from-import captures the fake into orchestration.__dict__.
    # On teardown only _paths is restored, leaving orchestration with a stale fake.
    # Patching explicitly here ensures each test gets the correct tmp_path closure.
    try:
        monkeypatch.setattr("trw_mcp.tools.orchestration.resolve_project_root", _fake_project_root)
    except AttributeError:
        pass  # Not yet imported

    # Patch state/recall_tracking — resolve_trw_dir is a module-level import
    # so record_recall writes to the wrong directory without this patch.
    try:
        monkeypatch.setattr("trw_mcp.state.recall_tracking.resolve_trw_dir", _fake_trw_dir)
    except AttributeError:
        pass  # Not yet imported

    # Patch tools/telemetry — resolve_trw_dir and find_active_run are
    # module-level imports that suffer the same stale-closure problem.
    try:
        monkeypatch.setattr("trw_mcp.tools.telemetry.resolve_trw_dir", _fake_trw_dir)
    except AttributeError:
        pass  # Not yet imported
    try:
        monkeypatch.setattr(
            "trw_mcp.tools.telemetry.find_active_run",
            lambda session_id=None: None,
        )
    except AttributeError:
        pass  # Not yet imported

    # Patch resources/ modules — they import resolve_project_root at module level,
    # so the bound reference must be updated each test to point to the current tmp_path.
    try:
        monkeypatch.setattr("trw_mcp.resources.config.resolve_project_root", _fake_project_root)
    except AttributeError:
        pass  # Not yet imported
    try:
        monkeypatch.setattr("trw_mcp.resources.run_state.resolve_project_root", _fake_project_root)
    except AttributeError:
        pass  # Not yet imported

    yield


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory with .trw/ structure.

    Returns:
        Path to the temporary project root.
    """
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "reflections").mkdir()
    (trw_dir / "scripts").mkdir()
    (trw_dir / "patterns").mkdir()
    (trw_dir / "context").mkdir()
    return tmp_path


@pytest.fixture
def config(tmp_path: Path) -> TRWConfig:
    """Provide test configuration with temp directory overrides."""
    return TRWConfig(trw_dir=str(tmp_path / ".trw"))


@pytest.fixture
def reader() -> FileStateReader:
    """Provide a FileStateReader instance."""
    return FileStateReader()


@pytest.fixture
def writer() -> FileStateWriter:
    """Provide a FileStateWriter instance."""
    return FileStateWriter()


@pytest.fixture
def event_logger(writer: FileStateWriter) -> FileEventLogger:
    """Provide a FileEventLogger instance."""
    return FileEventLogger(writer)


@pytest.fixture
def sample_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a sample run directory with minimal state.

    Returns:
        Path to the run directory.
    """
    run_dir = tmp_path / "docs" / "test-task" / "runs" / "20260206T120000Z-abcd1234"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "scratch" / "_orchestrator").mkdir(parents=True)
    (run_dir / "shards").mkdir()

    # Write run.yaml
    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260206T120000Z-abcd1234",
            "task": "test-task",
            "framework": "v18.0_TRW",
            "status": "active",
            "phase": "research",
            "confidence": "medium",
        },
    )

    # Write events.jsonl
    writer.append_jsonl(
        meta / "events.jsonl",
        {
            "ts": "2026-02-06T12:00:00Z",
            "event": "run_init",
            "task": "test-task",
        },
    )

    return run_dir
