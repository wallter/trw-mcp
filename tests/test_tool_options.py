"""PRD-CORE-291-FR03: the ``options`` mapping on ``trw_recall`` / ``trw_build_check``.

``RecallOptions`` and ``BuildCheckOptions`` (``trw_mcp.tools._tool_options``)
moved the rarely-set flat parameters of both tools into one ``options``
mapping with byte-identical key names. These tests pin:

* a representative wiring check per tool — the option actually reaches and
  changes production behavior, not just a schema-level acceptance;
* an unknown option key fails loudly, naming the accepted set;
* a removed flat parameter is fastmcp's unknown-keyword error through the
  real MCP call path, never a silent no-op;
* the served signature carries exactly the new parameter set.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as FastMCPValidationError

from tests.conftest import extract_tool_fn, get_tools_sync, make_test_server

# --- (a) representative wiring: the option actually reaches production behavior ---


def test_recall_min_impact_option_filters_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``options={"min_impact": 0.9}`` actually filters trw_recall's results."""
    from tests._memory_store_fake import FakeMemoryStore
    from trw_mcp.state import _store_selection

    # FakeMemoryStore.recall() only searches the "default" namespace (a known fixture
    # quirk -- the `fake_memory_store` fixture pins writes to "project:test", which
    # recall() never sees), so pin selected_store to "default" directly here instead
    # of using that fixture, keeping this test's writes and reads aligned.
    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "default"))
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    tools = get_tools_sync(make_test_server("learning"))

    tools["trw_learn"].fn(summary="Options wiring low impact entry", detail="low", impact=0.2)
    tools["trw_learn"].fn(summary="Options wiring high impact entry", detail="high", impact=0.95)

    unfiltered = tools["trw_recall"].fn(query="options wiring")
    assert len(unfiltered["learnings"]) == 2

    filtered = tools["trw_recall"].fn(query="options wiring", options={"min_impact": 0.9})
    assert len(filtered["learnings"]) == 1
    # PRD-CORE-294 FR01: default rows are stubs {id, claim, anchor?}, not full rows.
    assert filtered["learnings"][0]["claim"] == "Options wiring high impact entry"


def test_build_check_min_coverage_option_flips_tests_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``options={"min_coverage": 90}`` with coverage_pct=50 flips tests_passed False."""
    (tmp_path / ".trw" / "context").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.tools.build._registration.resolve_trw_dir", lambda: tmp_path / ".trw")
    monkeypatch.setattr("trw_mcp.tools.build._registration.find_active_run", lambda **_kwargs: None)
    build_fn = extract_tool_fn(make_test_server("build"), "trw_build_check")

    unflipped = build_fn(tests_passed=True, test_count=10, coverage_pct=50.0)
    assert unflipped["tests_passed"] is True

    flipped = build_fn(tests_passed=True, test_count=10, coverage_pct=50.0, options={"min_coverage": 90})
    assert flipped["tests_passed"] is False
    assert flipped["coverage_threshold_failed"] is True


# --- (b) unknown option key names the accepted set ---


def test_recall_unknown_option_key_raises_toolerror_naming_accepted_keys(tmp_path: Path) -> None:
    from trw_mcp.tools._tool_options import RecallOptions

    tools = get_tools_sync(make_test_server("learning"))
    with pytest.raises(ToolError) as excinfo:
        tools["trw_recall"].fn(query="*", options={"bogus_key": True})
    message = str(excinfo.value)
    for key in sorted(RecallOptions.model_fields):
        assert key in message, f"accepted key {key!r} missing from error message: {message}"


def test_build_check_unknown_option_key_raises_toolerror_naming_accepted_keys(tmp_path: Path) -> None:
    from trw_mcp.tools._tool_options import BuildCheckOptions

    build_fn = extract_tool_fn(make_test_server("build"), "trw_build_check")
    with pytest.raises(ToolError) as excinfo:
        build_fn(tests_passed=True, options={"bogus_key": True})
    message = str(excinfo.value)
    for key in sorted(BuildCheckOptions.model_fields):
        assert key in message, f"accepted key {key!r} missing from error message: {message}"


# --- (c) a removed flat parameter is fastmcp's unknown-keyword error ---


@pytest.mark.parametrize("removed", ["min_impact", "compact", "ultra_compact", "topic", "token_budget", "as_of"])
async def test_recall_removed_flat_parameter_is_a_tool_error(removed: str) -> None:
    """NFR: the old flat recall names fail loudly through the real MCP call path."""
    server = make_test_server("learning")
    with pytest.raises((ToolError, FastMCPValidationError), match="Unexpected keyword argument"):
        await server.call_tool("trw_recall", {"query": "*", removed: True})


@pytest.mark.parametrize("removed", ["mypy_clean", "failures", "run_path", "min_coverage", "command_results"])
async def test_build_check_removed_flat_parameter_is_a_tool_error(removed: str) -> None:
    """NFR: the old flat build_check names fail loudly through the real MCP call path."""
    server = make_test_server("build")
    with pytest.raises((ToolError, FastMCPValidationError), match="Unexpected keyword argument"):
        await server.call_tool("trw_build_check", {"tests_passed": True, removed: True})


# --- (d) the served signature carries exactly the new parameter set ---


def test_trw_recall_signature_is_exactly_the_new_parameter_set() -> None:
    from trw_mcp.tools.learning import register_learning_tools

    server = make_test_server()
    register_learning_tools(server)
    fn = extract_tool_fn(server, "trw_recall")
    params = list(inspect.signature(fn).parameters)
    assert params == ["ctx", "query", "tags", "status", "max_results", "ids", "options"]


def test_trw_build_check_signature_is_exactly_the_new_parameter_set() -> None:
    from trw_mcp.tools.build import register_build_tools

    server = make_test_server()
    register_build_tools(server)
    fn = extract_tool_fn(server, "trw_build_check")
    params = list(inspect.signature(fn).parameters)
    assert params == [
        "ctx",
        "tests_passed",
        "test_count",
        "failure_count",
        "coverage_pct",
        "static_checks_clean",
        "scope",
        "options",
    ]
