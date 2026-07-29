"""``trw_init``'s collapsed ``advanced`` argument — contract and refusals.

Seven rare ``trw_init`` parameters were collapsed into one structured argument
to cut the definition cost every client pays in its system prompt. The collapse
is only safe if the bag behaves like the flat parameters it replaced, so these
bind the three properties that make it so:

* the keys are byte-identical to the old parameter names and reach the SAME
  production effect (a run.yaml/config.yaml assertion, never a returned echo);
* an unrecognised or mistyped key is REFUSED with the accepted-key list — a bag
  that silently discards a typo would turn a caller mistake into invisible data
  loss on a call that still answers ``initialized``;
* a JSON-string payload is accepted, because fastmcp performs no JSON-string
  pre-parsing and some clients serialize structured arguments that way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._tools_orchestration_support import orch_tools  # noqa: F401
from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._orchestration_init_advanced import ADVANCED_KEYS, parse_init_advanced


def _run_yaml(result: dict[str, str]) -> dict[str, Any]:
    return FileStateReader().read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")


# ---------------------------------------------------------------------------
# Parser contract
# ---------------------------------------------------------------------------


def test_accepted_keys_match_the_flat_parameter_names_they_replaced() -> None:
    """Byte-identical keys are the whole migration story — pin them literally."""
    assert set(ADVANCED_KEYS) == {
        "artifacts",
        "complexity_signals",
        "config_overrides",
        "planning_mode",
        "protected",
        "task_root",
        "wave_manifest",
    }


@pytest.mark.parametrize("empty", [None, {}, "", "   "])
def test_absent_bag_reproduces_the_old_flat_defaults(empty: object) -> None:
    """Every "not supplied" wire shape yields the pre-collapse defaults."""
    adv = parse_init_advanced(empty)  # type: ignore[arg-type]

    assert adv.config_overrides is None
    assert adv.task_root is None
    assert adv.wave_manifest is None
    assert adv.complexity_signals is None
    assert adv.artifacts == []
    assert adv.protected is False
    assert adv.planning_mode is None


def test_json_string_payload_is_parsed_not_rejected() -> None:
    """fastmcp does no JSON-string pre-parsing; the tool must tolerate the shape."""
    adv = parse_init_advanced(json.dumps({"task_root": "docs", "protected": True}))

    assert adv.task_root == "docs"
    assert adv.protected is True


def test_unknown_key_is_refused_and_the_error_lists_the_accepted_keys() -> None:
    """A typo must fail loudly — a silent discard is the defect this guards."""
    with pytest.raises(StateError) as excinfo:
        parse_init_advanced({"task_roots": "docs"})

    message = str(excinfo.value)
    assert "task_roots" in message
    for key in ADVANCED_KEYS:
        assert key in message, f"refusal must name every accepted key, missing {key}"


def test_unknown_keys_are_all_reported_not_just_the_first() -> None:
    with pytest.raises(StateError) as excinfo:
        parse_init_advanced({"nope": 1, "alsonope": 2})

    assert "alsonope" in str(excinfo.value)
    assert "nope" in str(excinfo.value)


@pytest.mark.parametrize(
    "bag",
    [
        {"protected": "yes"},
        {"config_overrides": ["not", "an", "object"]},
        {"complexity_signals": "files_affected=1"},
        {"artifacts": "docs/one.md"},
        {"wave_manifest": [1, 2]},
    ],
    ids=["protected", "config_overrides", "complexity_signals", "artifacts", "wave_manifest"],
)
def test_wrong_value_type_is_refused_rather_than_coerced(bag: dict[str, object]) -> None:
    """Coercion would silently reinterpret intent (``protected: "no"`` -> True)."""
    with pytest.raises(StateError):
        parse_init_advanced(bag)


def test_non_object_json_string_is_refused() -> None:
    with pytest.raises(StateError):
        parse_init_advanced("[1, 2, 3]")


def test_malformed_json_string_is_refused() -> None:
    with pytest.raises(StateError):
        parse_init_advanced("{not json")


# ---------------------------------------------------------------------------
# Live tool: each key reaches the same production effect as its flat ancestor
# ---------------------------------------------------------------------------


def test_advanced_task_root_changes_the_resolved_task_dir(orch_tools: dict[str, Any]) -> None:
    """``task_root`` still overrides ``config.task_root`` in the run variables."""
    result = orch_tools["trw_init"].fn(task_name="advtaskroot", advanced={"task_root": "specs"})

    assert _run_yaml(result)["variables"]["TASK_ROOT"] == "specs"


def test_advanced_protected_is_persisted_to_run_yaml(orch_tools: dict[str, Any]) -> None:
    """``protected`` exempts a run from GC — it must survive the bag, not the echo."""
    protected = orch_tools["trw_init"].fn(task_name="advprotected", advanced={"protected": True})
    plain = orch_tools["trw_init"].fn(task_name="advunprotected")

    assert _run_yaml(protected)["protected"] is True
    assert _run_yaml(plain)["protected"] is False


def test_advanced_artifacts_are_persisted_to_run_yaml(orch_tools: dict[str, Any]) -> None:
    result = orch_tools["trw_init"].fn(
        task_name="advartifacts",
        advanced={"artifacts": ["docs/a.md", "docs/b.md"]},
    )

    assert _run_yaml(result)["artifacts"] == ["docs/a.md", "docs/b.md"]


def test_advanced_json_string_reaches_the_live_tool(orch_tools: dict[str, Any]) -> None:
    """End-to-end for the Claude Code #3084 string shape, not just the parser."""
    result = orch_tools["trw_init"].fn(
        task_name="advjsonstring",
        advanced=json.dumps({"task_root": "specs", "protected": True}),
    )

    run = _run_yaml(result)
    assert run["variables"]["TASK_ROOT"] == "specs"
    assert run["protected"] is True


def test_unknown_advanced_key_creates_no_run(orch_tools: dict[str, Any], tmp_path: Path) -> None:
    """The refusal precedes scaffolding: a rejected call leaves no partial run."""
    with pytest.raises(StateError):
        orch_tools["trw_init"].fn(task_name="advrejected", advanced={"protectd": True})

    assert not list(tmp_path.rglob("advrejected"))
