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
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from fastmcp import FastMCP

from tests._trw_home import (
    isolated_trw_home,  # noqa: F401  (shared HOME/XDG/TRW_USER_DIR floor; see that module's docstring)
)

pytest_plugins = ("tests._ceremony_helpers_support",)


# --------------------------------------------------------------------------
# xdist fan-out cap (2026-09-05 OOM incident)
# --------------------------------------------------------------------------
# A 2026-09-05 kernel OOM (191 pytest workers, ~109GB RSS) traced to delegated
# agents running `pytest -n auto` directly in several packages at once,
# bypassing the Makefile's `PYTEST_WORKERS ?= 4` default (which only guards
# `make test-parallel`/`make test-release`, not a raw `pytest` invocation).
# This guard is duplicated verbatim in every package conftest of the
# monorepo it is developed in — no shared test-support module exists across
# these independently-distributed packages (trw-mcp and trw-memory ship to
# PyPI; a new cross-package test dependency is not worth it for 15 lines).
_MAX_XDIST_WORKERS = 4
_ALLOW_WIDE_XDIST_ENV = "TRW_PYTEST_ALLOW_WIDE_XDIST"


def _xdist_fanout_violation(numprocesses: object, allow_wide: bool) -> str | None:
    """Return a violation reason if ``numprocesses`` exceeds the workstation
    cap, else ``None``.

    ``numprocesses`` is ``config.option.numprocesses`` as pytest-xdist sets
    it: ``None`` when ``-n`` was not passed, the literal string ``"auto"`` or
    ``"logical"`` when the caller asked xdist to size itself off the CPU core
    count, or an ``int``/int-like value from an explicit ``-n N``.
    """
    if allow_wide or numprocesses is None:
        return None
    if isinstance(numprocesses, str):
        return f"xdist fan-out -n {numprocesses!r} is uncapped"
    if not isinstance(numprocesses, int):
        return None
    worker_count = numprocesses
    if worker_count > _MAX_XDIST_WORKERS:
        return f"xdist fan-out -n {worker_count} exceeds the cap of {_MAX_XDIST_WORKERS}"
    return None


def pytest_configure(config: pytest.Config) -> None:
    """Refuse a wide xdist fan-out before it OOMs the workstation again."""
    allow_wide = os.environ.get(_ALLOW_WIDE_XDIST_ENV) == "1"
    violation = _xdist_fanout_violation(getattr(config.option, "numprocesses", None), allow_wide)
    if violation is not None:
        pytest.exit(
            f"{violation}. xdist fan-out capped at 4 workers on this "
            "workstation (2026-09-05 OOM); use -n 4 or set "
            "TRW_PYTEST_ALLOW_WIDE_XDIST=1",
            returncode=3,
        )


# Prefer monorepo sources over stale site-packages when tests run from the checkout.
_TESTS_DIR = Path(__file__).resolve().parent
_TRW_MCP_SRC = _TESTS_DIR.parent / "src"
_MONOREPO_ROOT = _TESTS_DIR.parent.parent
_TRW_MEMORY_SRC = _MONOREPO_ROOT / "trw-memory" / "src"
for _path in (str(_TRW_MEMORY_SRC), str(_TRW_MCP_SRC)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests import _path_isolation
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

# Install the `.trw` path stand-ins before any test module is imported, so even
# collection-time resolution lands in a scratch dir rather than the real repo.
# The per-test `_isolate_trw_dir` fixture re-runs the sweep (cheap, idempotent)
# to pick up modules imported since, and aims it at that test's tmp_path.
_path_isolation.install()

# Capture structlog's pristine global config at conftest import time. The root
# conftest is imported by pytest BEFORE any test module — and before any module
# does ``from trw_mcp.server import ...``, which runs ``configure_logging()`` at
# import and installs a CRITICAL-level filtering ``wrapper_class`` process-wide.
# Restoring to THIS clean baseline (not whatever each test inherits) is what
# makes ``capture_logs()`` reliable regardless of collection/import order.
_PRISTINE_STRUCTLOG_CONFIG = structlog.get_config()


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
    "before_edit_hint": ("trw_mcp.tools.before_edit_hint", "register_before_edit_hint_tools"),
    "before_edit_hint_batch": ("trw_mcp.tools.before_edit_hint_batch", "register_before_edit_hint_batch_tools"),
    "build": ("trw_mcp.tools.build", "register_build_tools"),
    "ceremony": ("trw_mcp.tools.ceremony", "register_ceremony_tools"),
    "ceremony_feedback": ("trw_mcp.tools.ceremony_feedback", "register_ceremony_feedback_tools"),
    "checkpoint": ("trw_mcp.tools.checkpoint", "register_checkpoint_tools"),
    "code_search": ("trw_mcp.tools.code_search", "register_code_search_tools"),
    "codebase_risk_report": ("trw_mcp.tools.codebase_risk_report", "register_codebase_risk_report_tools"),
    "knowledge": ("trw_mcp.tools.knowledge", "register_knowledge_tools"),
    "learning": ("trw_mcp.tools.learning", "register_learning_tools"),
    "meta_tune": ("trw_mcp.tools.meta_tune_ops", "register_meta_tune_tools"),
    "orchestration": ("trw_mcp.tools.orchestration", "register_orchestration_tools"),
    "phase_overrides": ("trw_mcp.tools.phase_overrides", "register_phase_override_tools"),
    "pipeline_health": ("trw_mcp.tools._pipeline_health_tool", "register_pipeline_health_tools"),
    "profile_explain": ("trw_mcp.tools.trw_profile_explain", "register_trw_profile_explain_tools"),
    "requirements": ("trw_mcp.tools.requirements", "register_requirements_tools"),
    "review": ("trw_mcp.tools.review", "register_review_tools"),
    "skill_discovery": ("trw_mcp.tools.skill_discovery", "register_skill_discovery_tools"),
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
        # PRD-HPO-PROF-001 profile system — pure logic, no filesystem I/O.
        "test_profile_model.py",
        "test_model.py",
        "test_resolver.py",
        "test_invariants.py",
        "test_inference.py",
        "test_explain.py",
        "test_snapshot.py",
        "test_allowlist_policy_surface.py",
        "test_property_layer_composition.py",
        # PRD-INTENT-002 phase-exposure — pure logic / mocks only.
        "test_phase_overrides.py",
        "test_models.py",
        "test_scoring.py",
        "test_scoring_branches.py",
        "test_scoring_edge_cases.py",
        "test_scoring_properties.py",
        "test_bayesian_calibration.py",
        "test_clients_llm.py",
        "test_middleware_ceremony.py",
        "test_middleware_context_budget.py",
        "test_middleware_compression.py",
        "test_middleware_response_optimizer.py",
        "test_prompts_messaging.py",
        "test_telemetry_embeddings.py",
        "test_validation_v2.py",
        "test_prd_utils_edge.py",
        "test_fix055_traceability_lang.py",
        "test_core080_template_variants.py",
        "test_response_optimizer.py",
        "test_scoring_q_preseed.py",
        # Token-bloat W5: prd_validate payload compaction — pure functions, no I/O
        "test_prd_validate_payload_compaction.py",
        # PRD-INFRA-145: OTel GenAI span shape — recording fake tracer, no I/O
        "test_otel_genai.py",
        # PRD-CORE-184: task-type detection + nudge weights — pure logic, no I/O
        "test_task_type_detection.py",
        "test_task_type_nudge_weights.py",
        # Pure model/config validation — no filesystem I/O
        "test_client_profile.py",
        "test_sprint44_models.py",
        "test_api_import.py",
        "test_fix044_module_config.py",
        "test_fix056_status_integrity.py",
        # PRD-IMPROVE-MCP-01 FR1/FR2: pure tag coercion + regex policy, no I/O
        "test_learn_ergonomics_improve_mcp_01.py",
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
        # PRD-FIX-076: tool surface reduction — registry/manifest absence, no I/O
        "test_fix076_tool_surface_reduction.py",
        # PRD-CORE-144: empirical probe harness — pure model/budget/cache/
        # verdict/telemetry logic, no filesystem I/O (subprocess-spawning
        # invocation/bounds/observability tests stay default/integration).
        "test_budget.py",
        "test_verdict.py",
    }
)

_SLOW_FILES: frozenset[str] = frozenset(
    {
        "test_consolidation.py",
        "test_bootstrap_branches.py",
        "test_bootstrap_claude_md_sync_split.py",
        "test_bootstrap_codex_split.py",
        "test_bootstrap_cursor_split.py",
        "test_bootstrap_ide_detection.py",
        "test_bootstrap_init_content.py",
        "test_bootstrap_merge_metadata.py",
        "test_bootstrap_multi_ide_detection.py",
        "test_bootstrap_multi_ide_init.py",
        "test_bootstrap_multi_ide_preservation.py",
        "test_bootstrap_opencode_split.py",
        "test_bootstrap_update_cleanup.py",
        "test_bootstrap_update_core.py",
        "test_bootstrap_update_migration.py",
        "test_bootstrap_version_utils.py",
        # PRD-CORE-146-NFR01: 1000-iteration latency benchmark (~1-3s)
        "test_nudge_performance.py",
        # PRD-FIX-130-FR04: 2000-row fixture stores + a bounded background join
        "test_session_start_step_latency.py",
        # PRD-CORE-262-FR01/NFR01: spawns up to 12 real trw-mcp subprocesses per
        # arm and grows a 64 MiB WAL. ``slow`` is ADDITIVE to the default
        # integration marker, so this file collects ZERO items under -m unit and
        # ``make test-fast`` never pays for it.
        "test_stdio_n_server_handshake.py",
        # PRD-CORE-262-FR05: runs full init-project flows into tmp projects.
        "test_init_scaffold_containment.py",
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
        if filename in _SLOW_FILES or filename.startswith(("test_consolidation", "test_bootstrap_branches")):
            item.add_marker(pytest.mark.slow)

        if filename in _UNIT_FILES:
            item.add_marker(pytest.mark.unit)
        elif filename in _E2E_FILES:
            item.add_marker(pytest.mark.e2e)
        else:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session", autouse=True)
def _isolate_trw_user_dir_floor(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Session-wide FLOOR for ``TRW_USER_DIR`` — never let it be unset mid-run.

    The per-test ``_isolate_trw_user_dir`` below uses ``monkeypatch``, which
    restores the env var to its PRE-TEST value at teardown. Without this floor
    that pre-test value is *unset*, which opens a window between one test's
    teardown and the next test's setup where ``TRW_USER_DIR`` is absent. A
    background thread left over from the previous test (session_start /
    deferred-deliver / embedder warmup) that calls ``get_user_backend()`` in
    that window falls through ``resolve_user_memory_dir``'s precedence chain to
    ``Path.home() / ".trw" / "memory"`` — the OPERATOR'S REAL user-tier store.
    Three tests were observed binding the real ``~/.trw/memory/memory.db`` this
    way (test_core099_provenance_wiring, test_tools_ceremony_session_start).

    Writing ``os.environ`` directly (not ``monkeypatch``) is deliberate: the
    floor must outlive every function-scoped monkeypatch undo. The per-test
    fixture narrows the var on top of this floor, and its restore returns the
    value to the floor rather than to unset.
    """
    import os

    session_user_dir = tmp_path_factory.mktemp("trw_user_dir_session")
    old = os.environ.get("TRW_USER_DIR")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    os.environ["TRW_USER_DIR"] = str(session_user_dir)
    os.environ.pop("XDG_DATA_HOME", None)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TRW_USER_DIR", None)
        else:
            os.environ["TRW_USER_DIR"] = old
        if old_xdg is not None:
            os.environ["XDG_DATA_HOME"] = old_xdg


@pytest.fixture(autouse=True)
def _isolate_trw_user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect TRW_USER_DIR to an isolated directory for each test.

    Prevents tests from reading or writing to the operator's real
    ``~/.trw/`` user-tier memory store. Without this guard a cold-start
    recall test that writes to the user tier would persist entries into
    the developer's actual user-scope memory database.

    Function scope prevents user-tier learnings written by one test from
    changing later wildcard recall and cold-start assertions in the same xdist
    worker. Per-file overrides remain valid because they use the same
    function-scoped monkeypatch restoration boundary. The inter-test window is
    covered by the session-scoped ``_isolate_trw_user_dir_floor`` above.

    Pairs with ``_reset_memory_backend`` (function-scoped autouse) which
    calls ``reset_user_backend()`` + ``reset_user_scope_cache()`` between
    tests — this fixture provides the directory boundary, that one discards the
    backend singleton already bound to the previous directory.
    """
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / ".trw-user"))
    # Also clear XDG_DATA_HOME so platform-default path resolution does not
    # slip through on Linux when TRW_USER_DIR is absent from getenv().
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_home_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect ``$HOME`` (and therefore ``Path.home()``) to an isolated directory.

    PRD-FIX-133: Antigravity CLI's MCP config lives at a GLOBAL,
    cross-project path (``~/.gemini/config/mcp_config.json``) rather than
    inside the project tree — the same shape ``_isolate_trw_user_dir`` above
    already guards for ``~/.trw/``. Any bootstrap test that exercises the full
    ``init-project``/``update-project`` flow with ``antigravity-cli`` selected
    calls a writer that resolves ``Path.home()``; without this floor that
    writer would target the developer's REAL home directory during a test run.

    Function-scoped so no test's write to the fake global config leaks into a
    later test's read of it.
    """
    monkeypatch.setenv("HOME", str(tmp_path / ".home"))
    yield


@pytest.fixture(autouse=True)
def _restore_sys_path() -> Iterator[None]:
    """Snapshot and restore ``sys.path`` around every test.

    Several tests append to ``sys.path`` mid-test to import repo-root scripts or
    sibling packages, and a raw ``sys.path.insert`` is never undone. That leaked
    entry is not merely untidy: a leaked MONOREPO-ROOT entry puts the repo-root
    ``tests`` package ahead of ``trw-mcp/tests`` on the path, and every
    ``multiprocessing`` SPAWN child inherits the parent's ``sys.path`` verbatim —
    so the child re-imports ``tests`` from the wrong package and dies with
    ``ModuleNotFoundError: No module named 'tests.<submodule>'``. Restoring the
    path per test kills that whole bug class rather than band-aiding each spawn
    site with ``monkeypatch.syspath_prepend``.

    Collection-time inserts (module-level, e.g. ``test_agent_loc.py``) run before
    any test, so they are already inside the snapshot and survive restoration.
    The list is restored IN PLACE (slice assignment) so any code holding a
    reference to ``sys.path`` still observes the restored value.
    """
    snapshot = list(sys.path)
    yield
    sys.path[:] = snapshot


@pytest.fixture(autouse=True)
def _isolate_client_session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the host client's session-identity variables (PRD-FIX-118 FR01).

    ``resolve_pin_key`` layer 2b reads the launching client's own session
    variable (e.g. ``CLAUDE_CODE_SESSION_ID``) so the MCP server and a shell hook
    key ``.trw/runtime/pins.json`` on the same string. That variable is present
    in the environment of any test run started FROM such a client, and absent in
    CI — which would make every pin-precedence assertion machine-dependent, and
    would silently collapse a two-client isolation fixture onto one real key.

    Cleared for every test so identity is something a test opts INTO
    (``monkeypatch.setenv`` after this fixture, or an explicit subprocess env),
    never something the developer's terminal supplies. ``monkeypatch`` restores
    the real values at teardown.
    """
    from trw_mcp.client_profiles.session_identity import known_session_id_env_vars

    for name in known_session_id_env_vars():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_config_singleton() -> Iterator[None]:
    """Reset TRWConfig singleton for test isolation."""
    _reset_config()
    yield
    _reset_config()


@pytest.fixture(autouse=True)
def _default_distill_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the proprietary trw-distill package as ABSENT by default (hermetic).

    ``_sidecar_substrate.distill_installed`` (2026-07-19) opens the distill-
    sidecar tier gate whenever ``trw_distill`` is importable — proof of a paid
    entitlement. Whether it is installed in the test venv is an ENVIRONMENT
    accident (the monorepo dev venv has an editable ``trw-distill``; a clean CI
    venv does not), which would make every tier-gate assertion non-deterministic
    across environments. Pin it to ``False`` so the suite exercises the
    entitlement-sentinel path deterministically. Tests that verify the
    package-presence unlock override this with
    ``monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)``.
    """
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)


@pytest.fixture(autouse=True)
def _reset_run_pin() -> Iterator[None]:
    """Reset active run pin + pin-store cache for test isolation.

    PRD-CORE-141: the 1-second TTL cache in ``_pin_store`` can carry state across
    tests. Without invalidating it, a prior test's empty-dict read caches into
    the next test's malformed-file scenario and the warning never fires.
    """
    from trw_mcp.state._paths import _pinned_runs
    from trw_mcp.state._pin_store import invalidate_pin_store_cache

    _pinned_runs.clear()
    invalidate_pin_store_cache()
    yield
    _pinned_runs.clear()
    invalidate_pin_store_cache()


@pytest.fixture(autouse=True)
def _reset_auto_close_throttle_fixture() -> Iterator[None]:
    """Reset the per-process auto_close_stale_runs throttle between tests.

    Production code throttles auto_close_stale_runs to once per hour to keep
    session_start fast; tests need a fresh throttle window per case so they
    can call the function multiple times without artificially being skipped.
    """
    from trw_mcp.state.analytics._stale_runs import _reset_auto_close_throttle

    _reset_auto_close_throttle()
    yield
    _reset_auto_close_throttle()


@pytest.fixture(autouse=True)
def _reset_low_coverage_advisory_guard() -> Iterator[None]:
    """Reset the one-time low-vector-coverage advisory guard between tests.

    Option A+ (2026-06-10): ``run_embeddings_maintenance`` surfaces the
    low-coverage backfill nudge once per PROCESS so it doesn't cry wolf every
    session while the background self-heal runs. Tests that exercise the
    first-surfacing path need a fresh guard per case.
    """
    from trw_mcp.tools._ceremony_embeddings_maintenance import (
        reset_low_coverage_advisory_guard,
    )

    reset_low_coverage_advisory_guard()
    yield
    reset_low_coverage_advisory_guard()


@pytest.fixture(autouse=True)
def _reset_deferred_delivery_state() -> Iterator[None]:
    """Reset deferred-delivery throttle + cancel event between tests.

    The 2026-05-17 watchdog changes added two pieces of process-local
    state in ``trw_mcp.tools._deferred_state``:

    - ``_last_auto_prune_at`` — process-local throttle marker so the
      auto_prune step doesn't pay its O(N^2) Jaccard cost more than
      once per ``learning_auto_prune_min_interval_hours``.
    - ``_cancel_event`` — cooperative cancellation signal flipped by
      the per-step / per-batch watchdog on budget overrun.

    Without this reset, the first test that calls a deliver path sets
    the throttle, and every subsequent test sees ``status="throttled"``
    instead of exercising the actual step. Similarly, a watchdog test
    that leaves the cancel event set causes downstream tests to start
    with every step short-circuited.
    """
    from trw_mcp.tools import _deferred_state as _ds

    _ds._last_auto_prune_at = None
    _ds._cancel_event.clear()
    yield
    _ds._last_auto_prune_at = None
    _ds._cancel_event.clear()


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


def _join_and_reset_q_learning() -> None:
    """PRD-FIX-088 FR01: reset the Q-learning bg worker between tests.

    Joins any running ``_q_thread``, drains the coalescing queue, and
    clears the ``_q_thread`` reference so the next test starts fresh.
    Prevents use-after-close segfaults on the SQLite backend the same
    way ``_join_and_reset_deferred`` does for the deliver-deferred thread.
    """
    try:
        import queue as _queue

        import trw_mcp.tools._q_learning_state as _qls

        with _qls._q_lock:
            t = _qls._q_thread
        if t is not None and t.is_alive():
            t.join(timeout=15)
        with _qls._q_lock:
            _qls._q_thread = None
            # Drain any leftover events.
            try:
                while True:
                    _qls._q_queue.get_nowait()
            except _queue.Empty:
                pass
        # PRD-FIX-088 P1.5 Fix 9: zero the worker-health dataclass so the
        # next test starts with a clean error_count / last_error.
        _qls.reset_health()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_memory_backend() -> Iterator[None]:
    """Reset the project AND user memory-adapter singletons for test isolation.

    Joins any running deferred-deliver thread first to prevent
    use-after-close segfaults on the SQLite backend.

    ``reset_user_backend()`` is as load-bearing as ``reset_backend()``:
    ``_user_tier._user_backend`` is a SEPARATE module-global singleton, and
    ``_federate_user_tier`` only skips user-tier federation when
    ``peek_user_backend()`` returns ``None`` *and* no user ``memory.db`` exists
    on disk. Leaving the singleton bound made ``peek_user_backend()`` return a
    backend rooted at an EARLIER test's ``TRW_USER_DIR`` (this fixture's sibling
    ``_isolate_trw_user_dir`` re-points the env var, but not the already-built
    object), so that stale store's entries were federated into every later
    recall — inflating every count by up to ``recall_user_tier_cap`` (5).
    ``reset_user_backend()`` also re-arms the memoized user-scope probe, so the
    separate ``reset_user_scope_cache()`` call below is only needed for tests
    that never construct a user backend at all.
    """
    from trw_mcp.state._tier_routing import reset_user_scope_cache
    from trw_mcp.state._user_tier import reset_user_backend
    from trw_mcp.state.memory_adapter import reset_backend

    _join_and_reset_deferred()
    _join_and_reset_q_learning()
    reset_backend()
    reset_user_backend()
    # core185-8: the user-scope presence probe is memoized; clear it between
    # tests so a prior test that set TRW_USER_TIER_ENABLED cannot leak a stale
    # "user scope present" verdict into a later, unconfigured test.
    reset_user_scope_cache()
    yield
    _join_and_reset_deferred()
    _join_and_reset_q_learning()
    reset_backend()
    reset_user_backend()
    reset_user_scope_cache()


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
def _restore_structlog_config() -> Iterator[None]:
    """Save structlog's global config before each test, restore it after.

    Root-cause isolation fix: ``trw_mcp._logging.configure_logging()`` installs a
    filtering ``wrapper_class`` (``make_filtering_bound_logger``) into structlog's
    process-global config. It runs at import time of ``trw_mcp.server`` and from
    the CLI/server boot path, so any test that imports the server or dispatches a
    production tool leaves the filtering wrapper bound for the rest of the
    process. ``structlog.testing.capture_logs()`` installs its LogCapture
    processor but the already-bound filtering wrapper drops events below CRITICAL
    *before* they reach processors — yielding empty ``logs`` lists and false
    failures in alphabetically-later tests that assert on captured events.

    The poison also happens at *collection* time (several test modules do
    ``from trw_mcp.server import ...`` at module level), so a per-test
    save-of-the-inherited-config would just save and re-apply the already-poisoned
    state forever. Instead we restore to ``_PRISTINE_STRUCTLOG_CONFIG`` — the
    config captured at conftest import time, before any server import.

    Restoring on BOTH setup and teardown keeps ``capture_logs()`` reliable
    regardless of collection/import order: setup guarantees the test body starts
    from the pristine config even if collection already poisoned it, and teardown
    reverts any mutation the test itself made (e.g. calling ``configure_logging``).
    """
    structlog.configure(**_PRISTINE_STRUCTLOG_CONFIG)
    try:
        yield
    finally:
        structlog.configure(**_PRISTINE_STRUCTLOG_CONFIG)


@pytest.fixture(autouse=True)
def _isolate_trw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Redirect all resolve_trw_dir() and resolve_project_root() calls to tmp dirs.

    Prevents test runs from writing ceremony-feedback.yaml, tool-telemetry.jsonl,
    pipeline-events.jsonl, and analytics.yaml to the real project's .trw/
    directory (PRD-FIX-050-FR01/FR02).

    This used to hand-enumerate the consumer modules to patch. That list covered
    9 of the 24 modules that bind a resolver at import time, and
    ``trw_mcp.telemetry.pipeline`` was one of the 15 it missed — which is how the
    suite wrote thousands of synthetic events into the real
    ``.trw/logs/pipeline-events.jsonl`` that ``trw-eval`` reads for RCA scoring.
    A list that must be extended by hand for every new module is not a safety
    net, so isolation now goes through ``tests/_path_isolation``: a permanent
    stand-in resolver plus a ``sys.modules`` sweep that needs no maintenance and
    survives the monkeypatch teardown that leaked-thread writes used to exploit.
    See that module's docstring for the full rationale, and
    ``tests/test_trw_dir_isolation_guard.py`` for the runtime guard.
    """
    _path_isolation.set_current_root(tmp_path)
    _path_isolation.install()

    # Not a path resolver, so it is not covered by the sweep: tools/telemetry
    # binds find_active_run at import and a real scan would walk the tmp tree
    # (and cache a stale run dir) on every test.
    try:
        monkeypatch.setattr(
            "trw_mcp.tools.telemetry.find_active_run",
            lambda session_id=None: None,
        )
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


# PRD-FIX-088 P1.5 Fix 7: shared invoke helper for ``trw_build_check`` tests.
# Replaces 14-line duplicated ``_invoke_build_check`` helpers across
# ``test_q_learning_defer_always.py``, ``test_build_check_step_telemetry.py``,
# ``test_build_check_latency.py``, and ``test_build_check_persistence.py``.
@pytest.fixture
def build_check_invoke(tmp_project: Path) -> Any:
    """Return a callable that invokes ``trw_build_check`` against ``tmp_project``.

    Usage::

        def test_x(build_check_invoke):
            result = build_check_invoke(tests_passed=True, scope="quick")

    Defaults: ``tests_passed=True``, ``test_count=1``, ``scope="full"``.
    Any kwarg supplied overrides the default.
    """

    def _invoke(**kwargs: Any) -> dict[str, Any]:
        import trw_mcp.tools.build._registration as reg_mod

        server = make_test_server("build")
        fn = extract_tool_fn(server, "trw_build_check")
        original_resolve = reg_mod.resolve_trw_dir
        reg_mod.resolve_trw_dir = lambda: tmp_project / ".trw"
        try:
            defaults: dict[str, Any] = {
                "tests_passed": True,
                "test_count": 1,
                "scope": "full",
            }
            defaults.update(kwargs)
            return fn(**defaults)  # type: ignore[no-any-return]
        finally:
            reg_mod.resolve_trw_dir = original_resolve

    return _invoke
