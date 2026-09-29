"""PRD-CORE-320 FR08 — trw_deliver verifies the scoped PRDs' declared call chains.

Exercises the real deliver step (:func:`step_capability_integration`) against a
fixture PRD (under ``<project_root>/docs/requirements-aare-f/prds/``) and a
fixture package (under ``<project_root>/trw-mcp/src/``), which the test makes the
step's source root in place of the running ``trw_mcp`` package's. Integration
tier: filesystem I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import trw_mcp.state.validation.call_chain as call_chain_module
from trw_mcp.models.typed_dicts import CapabilityIntegrationRow, DeliverResultDict
from trw_mcp.state.validation.call_chain import ToolSite
from trw_mcp.tools._deliver_capability_integration import step_capability_integration

pytestmark = pytest.mark.integration

_PRD_ID = "PRD-TEST-320"


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _prd_with_call_chain(cell: str) -> str:
    return (
        "# PRD-TEST-320: fixture\n\n"
        "## Traceability\n\n"
        "| Requirement | Call chain | Test |\n"
        "|---|---|---|\n"
        f"| FR02 | {cell} | t.py |\n"
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root the resolver, ``_resolve_prd_scope`` and the step all agree on."""
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    # The step checks chains against the running trw_mcp's source root; here, the fixture package's.
    monkeypatch.setattr(
        "trw_mcp.tools._deliver_capability_integration._source_root", lambda: tmp_path / "trw-mcp" / "src"
    )
    monkeypatch.setattr(
        call_chain_module,
        "registered_tool_sites",
        lambda: {"do_the_thing": ToolSite("fixturepkg.entrypoints", 5)},
    )
    _write(tmp_path, "trw-mcp/src/fixturepkg/__init__.py", "")
    return tmp_path


def _entrypoints(calls_helper: bool) -> str:
    body = "_helper()\n" if calls_helper else "return None\n"
    return (
        "from fixturepkg.middle import helper as _helper\n\n\n"
        "def register(server):\n    @server.tool()\n    def do_the_thing() -> None:\n        " + body
    )


def _write_fixture_pkg(project: Path, *, wired: bool) -> None:
    _write(project, "trw-mcp/src/fixturepkg/entrypoints.py", _entrypoints(wired))
    _write(
        project,
        "trw-mcp/src/fixturepkg/middle.py",
        "import fixturepkg.leaf as leaf_module\n\n\ndef helper() -> None:\n    leaf_module.capability()\n",
    )
    _write(project, "trw-mcp/src/fixturepkg/leaf.py", "def capability() -> None:\n    return None\n")


def _run_dir(project: Path, prd_scope: list[str]) -> Path:
    run_dir = project / ".trw" / "runs" / "R1"
    (run_dir / "meta").mkdir(parents=True)
    scope = "[" + ", ".join(prd_scope) + "]"
    (run_dir / "meta" / "run.yaml").write_text(f"prd_scope: {scope}\n", encoding="utf-8")
    return run_dir


_CHAIN_CELL = "`fixturepkg.entrypoints.do_the_thing` -> `fixturepkg.middle.helper` -> `fixturepkg.leaf.capability`"


def test_a_broken_hop_in_a_scoped_prd_warns_and_names_it_then_restoring_clears_it(
    project: Path,
) -> None:
    _write(project, "docs/requirements-aare-f/prds/PRD-TEST-320.md", _prd_with_call_chain(_CHAIN_CELL))
    _write_fixture_pkg(project, wired=False)
    run_dir = _run_dir(project, [_PRD_ID])

    results: DeliverResultDict = {}
    step_capability_integration(run_dir, results)

    assert (
        "PRD-CORE-320" in results["integration_isolated_warning"] or "FR02" in results["integration_isolated_warning"]
    )
    assert "FR02" in results["integration_isolated_warning"]
    rows = cast("list[CapabilityIntegrationRow]", results["capability_integration"])
    assert len(rows) == 1
    assert rows[0]["integration"] == "isolated"
    assert "fixturepkg.entrypoints.do_the_thing" in rows[0]["first_unverified_hop"]

    # Restoring the call site clears both.
    _write_fixture_pkg(project, wired=True)
    results2: DeliverResultDict = {}
    step_capability_integration(run_dir, results2)
    assert "integration_isolated_warning" not in results2
    rows2 = cast("list[CapabilityIntegrationRow]", results2["capability_integration"])
    assert rows2[0]["integration"] == "wired"
    assert len(rows2[0]["call_sites"]) == 2


@pytest.mark.parametrize(
    "cell",
    ["`fixturepkg.entrypoints.do_the_thing` -> `fixturepkg.leaf.capability", "`` -> `fixturepkg.leaf.capability`"],
    ids=["unbalanced-backticks", "empty-hop"],
)
def test_a_malformed_call_chain_cell_gives_an_isolated_row_not_an_absent_one(project: Path, cell: str) -> None:
    _write(project, "docs/requirements-aare-f/prds/PRD-TEST-320.md", _prd_with_call_chain(cell))
    _write_fixture_pkg(project, wired=True)
    run_dir = _run_dir(project, [_PRD_ID])

    results: DeliverResultDict = {}
    step_capability_integration(run_dir, results)

    rows = cast("list[CapabilityIntegrationRow]", results["capability_integration"])
    assert len(rows) == 1
    assert rows[0]["integration"] == "isolated"
    assert rows[0]["reason"] == "malformed chain"
    assert "FR02" in results["integration_isolated_warning"]


def test_a_prd_with_no_call_chain_column_produces_no_rows_and_no_warning(project: Path) -> None:
    _write(
        project,
        "docs/requirements-aare-f/prds/PRD-TEST-320.md",
        "# PRD-TEST-320: fixture\n\n## Traceability\n\n| Requirement | Test |\n|---|---|\n| FR02 | t.py |\n",
    )
    _write_fixture_pkg(project, wired=True)
    run_dir = _run_dir(project, [_PRD_ID])

    results: DeliverResultDict = {}
    step_capability_integration(run_dir, results)

    assert "capability_integration" not in results
    assert "integration_isolated_warning" not in results


def test_a_run_with_no_prd_scope_produces_no_rows(project: Path) -> None:
    run_dir = _run_dir(project, [])

    results: DeliverResultDict = {}
    step_capability_integration(run_dir, results)

    assert "capability_integration" not in results
    assert "integration_isolated_warning" not in results


def test_an_unresolvable_prd_scope_entry_gives_an_isolated_prd_not_found_row(project: Path) -> None:
    run_dir = _run_dir(project, ["PRD-DOES-NOT-EXIST"])

    results: DeliverResultDict = {}
    step_capability_integration(run_dir, results)

    rows = cast("list[CapabilityIntegrationRow]", results["capability_integration"])
    assert len(rows) == 1
    assert rows[0]["integration"] == "isolated"
    assert rows[0]["reason"] == "prd not found"
    assert "integration_isolated_warning" in results


def test_a_hook_entry_chain_with_the_marker_in_events_jsonl_is_wired(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-320 FR04: the run's own events.jsonl carrying hook_real_path makes the chain wired."""
    import trw_mcp.state.validation.hook_entry as hook_entry_module

    hooks_dir = project / "trw-mcp" / "src" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "fixture-hook.sh").write_text("#!/bin/sh\npython3 -m fixturepkg.middle\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(hook_entry_module, "hooks_dir", lambda: hooks_dir)

    cell = "`hook:fixture-hook` -> `fixturepkg.middle.helper` -> `fixturepkg.leaf.capability`"
    _write(project, "docs/requirements-aare-f/prds/PRD-TEST-320.md", _prd_with_call_chain(cell))
    _write_fixture_pkg(project, wired=True)
    run_dir = _run_dir(project, [_PRD_ID])
    (run_dir / "meta" / "events.jsonl").write_text(
        '{"ts":"2026-01-01T00:00:00Z","event":"hook_real_path","hook_real_path":"fixture-hook"}\n',
        encoding="utf-8",
    )

    results: DeliverResultDict = {}
    step_capability_integration(run_dir, results)

    assert "integration_isolated_warning" not in results
    rows = cast("list[CapabilityIntegrationRow]", results["capability_integration"])
    assert rows[0]["integration"] == "wired"
    assert rows[0]["call_sites"][0].startswith("hooks/fixture-hook.sh:")


def test_a_hook_entry_chain_without_the_marker_is_isolated(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same script, same invocation -- no marker anywhere in this run's evidence."""
    import trw_mcp.state.validation.hook_entry as hook_entry_module

    hooks_dir = project / "trw-mcp" / "src" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "fixture-hook.sh").write_text("#!/bin/sh\npython3 -m fixturepkg.middle\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(hook_entry_module, "hooks_dir", lambda: hooks_dir)

    cell = "`hook:fixture-hook` -> `fixturepkg.middle.helper` -> `fixturepkg.leaf.capability`"
    _write(project, "docs/requirements-aare-f/prds/PRD-TEST-320.md", _prd_with_call_chain(cell))
    _write_fixture_pkg(project, wired=True)
    run_dir = _run_dir(project, [_PRD_ID])

    results: DeliverResultDict = {}
    step_capability_integration(run_dir, results)

    assert "FR02" in results["integration_isolated_warning"]
    rows = cast("list[CapabilityIntegrationRow]", results["capability_integration"])
    assert rows[0]["integration"] == "isolated"
    assert rows[0]["reason"] == "hook real path not observed"


def test_the_source_root_is_the_running_trw_mcp_package() -> None:
    """Not ``<project>/trw-mcp/src``, which exists only in the monorepo (worker-3 review)."""
    import trw_mcp
    from trw_mcp.tools._deliver_capability_integration import _source_root

    assert (_source_root() / "trw_mcp" / "__init__.py").resolve() == Path(trw_mcp.__file__).resolve()


def test_a_session_marker_older_than_the_run_is_not_this_runs_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session-events.jsonl spans every session; only markers written since this run started count (worker-3 review)."""
    from trw_mcp.tools._deliver_capability_integration import _hook_evidence

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    run_dir = trw_dir / "runs" / "R1"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text("created_at: '2026-09-26T10:00:00Z'\n", encoding="utf-8")
    lines = [
        '{"ts":"2026-09-25T09:00:00Z","event":"hook_real_path","hook_real_path":"stale-hook"}',
        '{"event":"hook_real_path","hook_real_path":"undated-hook"}',
        '{"ts":"2026-09-26T10:30:00Z","event":"hook_real_path","hook_real_path":"fresh-hook"}',
    ]
    (trw_dir / "context" / "session-events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert _hook_evidence(run_dir) == frozenset({"fresh-hook"})
