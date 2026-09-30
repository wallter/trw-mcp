"""The B80-60 contract spec matches the live tool registry (PRD-CORE-300-FR17).

``docs/sprint-mcp8/B80-60-CONTRACT-SPECS.md`` states, per operation, the inputs,
outputs, side effects and reviewer exposure of ``trw_init``,
``trw_session_start``, ``trw_prd_validate`` and ``trw_review``, and whether each
candidate merge (FR18, FR19) overlaps. Nothing here is hand-copied: parameter
names come from the production server's registered tools, output keys from the
registered functions' ``TypedDict`` return annotations or from a real call in a
temporary project, and the phase each call leaves the run in is observed. A
drift in any of them fails by tool name, so the spec cannot silently go stale
while FR18/FR19 are decided from it.
"""

from __future__ import annotations

import asyncio
import re
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from typing_extensions import is_typeddict

from tests._layout import MONOREPO_ROOT, requires_monorepo
from tests.conftest import get_tools_sync, make_test_server

pytestmark = requires_monorepo

# Guarded: this module is collected in the public mirror too, where MONOREPO_ROOT is None.
SPEC_PATH = (MONOREPO_ROOT or Path()) / "docs" / "sprint-mcp8" / "B80-60-CONTRACT-SPECS.md"
SPEC_TOOLS = ("trw_init", "trw_session_start", "trw_prd_validate", "trw_review")
_BLOCK_RE = re.compile(r"<!-- contract-spec:start -->\s*```yaml\n(.*?)```\s*<!-- contract-spec:end -->", re.DOTALL)


def _spec() -> dict[str, Any]:
    match = _BLOCK_RE.search(SPEC_PATH.read_text(encoding="utf-8"))
    assert match is not None, f"{SPEC_PATH} has no contract-spec block"
    loaded = yaml.safe_load(match.group(1))
    assert isinstance(loaded, dict)
    return loaded


def _drift(declared: set[str], live: set[str]) -> str:
    """Empty when the spec matches the live set; otherwise both one-sided differences."""
    if declared == live:
        return ""
    return f"spec-only {sorted(declared - live)}, live-only {sorted(live - declared)}"


def _live_tools() -> dict[str, Any]:
    """The tools the production server registers, by name."""
    from tests._served_app import served_app

    mcp = served_app()
    return {tool.name: tool for tool in asyncio.run(mcp._list_tools())}


def _typed_output_keys(tool: Any) -> set[str]:
    """Keys of the TypedDict the registered function declares it returns."""
    returned = typing.get_type_hints(tool.fn)["return"]
    assert is_typeddict(returned), f"{tool.name} no longer returns a TypedDict: {returned!r}"
    return set(returned.__required_keys__) | set(returned.__optional_keys__)


def _phase(run_path: Path) -> str:
    data = yaml.safe_load((run_path / "meta" / "run.yaml").read_text(encoding="utf-8"))
    return str(data["phase"])


def _run_dirs(project: Path) -> list[Path]:
    return sorted(project.glob(".trw/runs/*/*/meta/run.yaml"))


@pytest.fixture
def lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """init -> prd_validate -> review in one temporary project, recording what each call left."""
    from tests._ceremony_helpers_support import disable_platform_contact

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    disable_platform_contact(monkeypatch)
    tools = get_tools_sync(make_test_server("orchestration", "requirements", "review"))

    init = tools["trw_init"].fn(task_name="contract-spec", objective="B80-60 contract spec")
    run_path = Path(init["run_path"])
    phase_after_init = _phase(run_path)

    prd = tmp_path / "PRD-CORE-999.md"
    prd.write_text(
        "---\nprd:\n  id: PRD-CORE-999\n  title: Contract probe\n  status: draft\n  category: CORE\n---\n# Probe\n",
        encoding="utf-8",
    )
    validate = tools["trw_prd_validate"].fn(prd_path=str(prd))
    phase_after_validate = _phase(run_path)

    review = tools["trw_review"].fn(review_completed=True, options={"run_path": str(run_path)})
    return {
        "project": tmp_path,
        "run_path": run_path,
        "outputs": {"trw_init": init, "trw_prd_validate": validate, "trw_review": review},
        "phase_after": {
            "trw_init": phase_after_init,
            "trw_prd_validate": phase_after_validate,
            "trw_review": _phase(run_path),
        },
    }


@pytest.mark.parametrize("name", SPEC_TOOLS)
def test_spec_inputs_are_the_registered_parameters(name: str) -> None:
    registered = set(_live_tools()[name].parameters["properties"])
    assert not (drift := _drift(set(_spec()["tools"][name]["inputs"]), registered)), f"{name}: {drift}"


def test_drift_names_both_sides_of_a_stale_spec() -> None:
    """A spec that drops a live parameter and keeps a removed one is reported, not passed."""
    registered = set(_live_tools()["trw_init"].parameters["properties"])
    stale = (set(_spec()["tools"]["trw_init"]["inputs"]) - {"advanced"}) | {"config_overrides"}
    assert _drift(stale, registered) == "spec-only ['config_overrides'], live-only ['advanced']"
    assert _drift(registered, registered) == ""


@pytest.mark.parametrize(
    ("name", "argument", "live_keys"),
    [
        (
            "trw_init",
            "advanced",
            lambda: __import__("trw_mcp.tools._orchestration_init_advanced", fromlist=["ADVANCED_KEYS"]).ADVANCED_KEYS,
        ),
        (
            "trw_review",
            "options",
            lambda: __import__("trw_mcp.tools._tool_options", fromlist=["ReviewOptions"]).ReviewOptions.model_fields,
        ),
    ],
)
def test_spec_structured_argument_keys_are_the_accepted_keys(name: str, argument: str, live_keys: Any) -> None:
    declared = set(_spec()["tools"][name]["structured_input_keys"][argument])
    assert not (drift := _drift(declared, set(live_keys()))), f"{name}({argument}=...): {drift}"


@pytest.mark.parametrize("name", ["trw_session_start", "trw_prd_validate"])
def test_spec_typed_outputs_are_the_registered_return_type(name: str) -> None:
    tool = _live_tools()[name]
    entry = _spec()["tools"][name]
    assert entry["output_source"] == typing.get_type_hints(tool.fn)["return"].__name__
    assert not (drift := _drift(set(entry["output_keys"]), _typed_output_keys(tool))), f"{name}: {drift}"


@pytest.mark.parametrize("name", ["trw_init", "trw_review"])
def test_spec_observed_outputs_are_what_a_real_call_returns(name: str, lifecycle: dict[str, Any]) -> None:
    spec = _spec()
    observed = set(lifecycle["outputs"][name]) - set(spec["decoration_keys"])
    assert not (drift := _drift(set(spec["tools"][name]["output_keys"]), observed)), f"{name}: {drift}"


@pytest.mark.parametrize("name", ["trw_init", "trw_prd_validate", "trw_review"])
def test_spec_phase_effect_is_the_observed_phase(name: str, lifecycle: dict[str, Any]) -> None:
    assert lifecycle["phase_after"][name] == _spec()["tools"][name]["phase_after"]


@pytest.mark.parametrize("name", ["trw_prd_validate", "trw_review"])
def test_spec_verdict_vocabulary_contains_the_observed_verdict(name: str, lifecycle: dict[str, Any]) -> None:
    verdict = lifecycle["outputs"][name]["verdict"]
    assert verdict in _spec()["tools"][name]["verdict_values"]


def test_the_two_verdict_vocabularies_are_disjoint() -> None:
    tools = _spec()["tools"]
    assert not set(tools["trw_prd_validate"]["verdict_values"]) & set(tools["trw_review"]["verdict_values"])


def test_review_writes_its_artifacts_and_validate_does_not(lifecycle: dict[str, Any]) -> None:
    """trw_review's own writes, against trw_prd_validate's cache-only write."""
    meta = lifecycle["run_path"] / "meta"
    assert (meta / "review.yaml").is_file()
    assert list((meta / "receipts" / "review").glob("*.json"))
    assert list((lifecycle["project"] / ".trw" / "cache" / "prd-validation").rglob("*.json"))


def test_session_start_steps_are_the_live_step_table() -> None:
    from trw_mcp.tools._ceremony_step_table import SESSION_START_STEPS

    assert _spec()["tools"]["trw_session_start"]["steps"] == [step.key for step in SESSION_START_STEPS]


def test_session_start_creates_no_run_and_init_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._ceremony_helpers import make_ceremony_server

    spec = _spec()["tools"]
    tools = make_ceremony_server(monkeypatch, tmp_path)
    tools["trw_session_start"].fn()
    assert bool(_run_dirs(tmp_path)) is spec["trw_session_start"]["creates_run"]

    get_tools_sync(make_test_server("orchestration"))["trw_init"].fn(task_name="creates-a-run")
    assert bool(_run_dirs(tmp_path)) is spec["trw_init"]["creates_run"]


@pytest.mark.parametrize("name", SPEC_TOOLS)
def test_spec_reviewer_exposure_is_reviewer_tools(name: str) -> None:
    from trw_mcp.models.surface_packs import REVIEWER_TOOLS

    assert _spec()["tools"][name]["reviewer_surface"] is (name in REVIEWER_TOOLS)


def _contract_keys(entry: Mapping[str, Any], decorations: set[str]) -> set[str]:
    return set(entry["output_keys"]) - decorations


@pytest.mark.parametrize("fr", ["FR18", "FR19"])
def test_merge_overlap_is_computed_from_the_live_contracts(fr: str) -> None:
    """The shared inputs and shared output keys each merge records are the live intersection."""
    spec = _spec()
    merge = next(m for m in spec["merges"] if m["fr"] == fr)
    left, right = merge["pair"]
    live = _live_tools()
    shared_inputs = set(live[left].parameters["properties"]) & set(live[right].parameters["properties"])
    decorations = set(spec["decoration_keys"])
    shared_outputs = _contract_keys(spec["tools"][left], decorations) & _contract_keys(
        spec["tools"][right], decorations
    )
    assert set(merge["shared_inputs"]) == shared_inputs
    assert set(merge["shared_output_keys"]) == shared_outputs
    # No shared input means one tool cannot serve the other's callers without a mode switch.
    assert merge["overlap"] is bool(shared_inputs)
