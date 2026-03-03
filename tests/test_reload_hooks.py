"""Tests for __reload_hook__() in all tool modules.

INFRA-014 FR01: Verify that every tool module with module-level singletons
exposes a __reload_hook__() function that resets all cached state to fresh
instances, enabling mcp-hmr hot-reload without restarting the MCP server.
"""

from __future__ import annotations

import importlib
import types
from typing import Any

import pytest

# All tool modules that must have __reload_hook__
TOOL_MODULES = [
    "trw_mcp.tools.ceremony",
    "trw_mcp.tools.learning",
    "trw_mcp.tools.orchestration",
    "trw_mcp.tools.build",
    "trw_mcp.tools.requirements",
    "trw_mcp.tools.review",
    "trw_mcp.tools.telemetry",
    "trw_mcp.tools.checkpoint",
    "trw_mcp.tools.usage",
    "trw_mcp.tools.report",
]

# Expected singletons per module (must be reset by __reload_hook__)
EXPECTED_SINGLETONS: dict[str, list[str]] = {
    "trw_mcp.tools.ceremony": ["_config", "_reader", "_writer", "_events"],
    "trw_mcp.tools.learning": ["_config", "_reader", "_writer", "_llm"],
    "trw_mcp.tools.orchestration": ["_config", "_reader", "_writer", "_events"],
    "trw_mcp.tools.build": ["_config", "_writer"],
    "trw_mcp.tools.requirements": ["_config", "_writer"],
    "trw_mcp.tools.review": ["_writer", "_events"],
    "trw_mcp.tools.telemetry": ["_config", "_writer", "_events"],
    "trw_mcp.tools.checkpoint": ["_reader", "_writer", "_events"],
    "trw_mcp.tools.usage": ["_config", "_reader"],
    "trw_mcp.tools.report": ["_reader"],
}


@pytest.mark.unit
@pytest.mark.parametrize("module_name", TOOL_MODULES)
def test_reload_hook_exists(module_name: str) -> None:
    """Every tool module must expose __reload_hook__."""
    mod = importlib.import_module(module_name)
    assert hasattr(mod, "__reload_hook__"), (
        f"{module_name} is missing __reload_hook__() — required for mcp-hmr hot-reload"
    )
    hook = getattr(mod, "__reload_hook__")
    assert callable(hook), f"{module_name}.__reload_hook__ must be callable"


@pytest.mark.unit
@pytest.mark.parametrize("module_name", TOOL_MODULES)
def test_reload_hook_callable(module_name: str) -> None:
    """__reload_hook__() must be callable without arguments."""
    mod = importlib.import_module(module_name)
    hook = getattr(mod, "__reload_hook__")
    # Calling it should not raise
    hook()


@pytest.mark.unit
@pytest.mark.parametrize("module_name", TOOL_MODULES)
def test_reload_hook_resets_singletons(module_name: str) -> None:
    """After calling __reload_hook__(), all singletons must be fresh instances."""
    mod = importlib.import_module(module_name)
    expected = EXPECTED_SINGLETONS[module_name]

    # Capture original singleton ids
    originals: dict[str, int] = {}
    for name in expected:
        obj = getattr(mod, name, None)
        assert obj is not None, f"{module_name}.{name} should not be None before reload"
        originals[name] = id(obj)

    # Call reload hook
    mod.__reload_hook__()

    # Verify singletons are fresh instances (different object ids)
    for name in expected:
        new_obj = getattr(mod, name, None)
        assert new_obj is not None, (
            f"{module_name}.{name} should not be None after reload — "
            "reload hook must reinitialize, not set to None"
        )
        assert id(new_obj) != originals[name], (
            f"{module_name}.{name} was not reset by __reload_hook__() — "
            f"same object id {originals[name]}"
        )


@pytest.mark.unit
def test_ceremony_reload_resets_all_four() -> None:
    """Ceremony module must reset _config, _reader, _writer, _events."""
    from trw_mcp.tools import ceremony

    old_config = ceremony._config
    old_reader = ceremony._reader
    old_writer = ceremony._writer
    old_events = ceremony._events

    ceremony.__reload_hook__()

    assert ceremony._config is not old_config
    assert ceremony._reader is not old_reader
    assert ceremony._writer is not old_writer
    assert ceremony._events is not old_events


@pytest.mark.unit
def test_learning_reload_resets_llm() -> None:
    """Learning module must reset _llm client along with config/reader/writer."""
    from trw_mcp.tools import learning

    old_llm = learning._llm
    old_config = learning._config

    learning.__reload_hook__()

    assert learning._llm is not old_llm
    assert learning._config is not old_config


@pytest.mark.unit
def test_telemetry_reload_resets_cached_run_dir() -> None:
    """Telemetry module must reset _cached_run_dir to invalidate TTL cache."""
    from trw_mcp.tools import telemetry

    # Poison the cache
    telemetry._cached_run_dir = (999999.0, None)

    telemetry.__reload_hook__()

    assert telemetry._cached_run_dir == (0.0, None), (
        "Telemetry __reload_hook__() must reset _cached_run_dir TTL cache"
    )


@pytest.mark.unit
def test_checkpoint_reload_resets_counter() -> None:
    """Checkpoint module must reset _checkpoint_state.counter to 0."""
    from trw_mcp.tools import checkpoint

    checkpoint._checkpoint_state.counter = 42

    checkpoint.__reload_hook__()

    assert checkpoint._checkpoint_state.counter == 0, (
        "Checkpoint __reload_hook__() must reset tool call counter"
    )


@pytest.mark.unit
def test_requirements_reload_clears_template_cache() -> None:
    """Requirements module must clear cached PRD template on reload."""
    from trw_mcp.tools import requirements

    # Poison the cache
    requirements._CACHED_TEMPLATE_BODY = "stale"
    requirements._CACHED_TEMPLATE_VERSION = "0.0.0"

    requirements.__reload_hook__()

    assert requirements._CACHED_TEMPLATE_BODY is None, (
        "Requirements __reload_hook__() must clear _CACHED_TEMPLATE_BODY"
    )
    assert requirements._CACHED_TEMPLATE_VERSION is None, (
        "Requirements __reload_hook__() must clear _CACHED_TEMPLATE_VERSION"
    )


@pytest.mark.unit
def test_reload_hook_returns_none() -> None:
    """__reload_hook__() should return None (void function)."""
    from trw_mcp.tools import ceremony

    result = ceremony.__reload_hook__()
    assert result is None
