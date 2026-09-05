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

from tests._formation_test_support import FormationFixture, formation_env, make_run_dir  # noqa: F401
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
    """Byte-identical keys are the whole migration story — pin them literally.

    ``formation`` / ``join_formation`` (PRD-CORE-265-FR03/FR04) are listed
    separately because they replaced no flat parameter: they are new keys added
    to the bag precisely so the formation surface costs no new MCP tool.
    """
    assert set(ADVANCED_KEYS) == {
        "artifacts",
        "complexity_signals",
        "config_overrides",
        "formation",
        "join_formation",
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


# ---------------------------------------------------------------------------
# PRD-CORE-265-FR03 / FR04: formation creation and join
# ---------------------------------------------------------------------------


def test_formation_init_tool_and_cli_write_the_same_manifest(
    formation_env: FormationFixture,
    tmp_path: Path,
) -> None:
    """FR03. Two entry points, one facade — so neither can drift from the other.

    ATTRIBUTION. Guards ``tools/_orchestration_formation.apply_formation_init``
    and ``tools/_formation_cli._run_init``: both call
    ``trw_mcp.formation.create``. Point either at its own writer and the two
    manifests stop matching. The tool-set assertion guards the "no new MCP tool"
    commitment in the same breath.
    """
    import argparse

    import yaml

    from trw_mcp.tools._formation_cli import run_formation
    from trw_mcp.tools._orchestration_formation import apply_formation_init

    result: dict[str, str] = {}
    apply_formation_init(formation_env.payload(), None, formation_env.orchestrator_run, None, result)
    assert result["formation_id"] == "release-train"
    assert result["formation_revision"] == "1"
    via_tool = yaml.safe_load(formation_env.manifest_path().read_text(encoding="utf-8"))
    assert [m["status"] for m in via_tool["members"]] == ["pending", "pending"]

    second_run = make_run_dir(formation_env.trw_dir / "runs", "orchestrator-2")
    payload_file = tmp_path / "payload.yaml"
    payload_file.write_text(yaml.safe_dump(formation_env.payload(formation_id="release-train-2")), encoding="utf-8")
    args = argparse.Namespace(
        formation_command="init", from_file=str(payload_file), run_path=str(second_run), as_json=False
    )
    with pytest.raises(SystemExit) as exited:
        run_formation(args)
    assert exited.value.code == 0
    via_cli = yaml.safe_load((second_run / "formation.yaml").read_text(encoding="utf-8"))

    volatile = ("created_utc", "updated_utc", "orchestrator_run_path", "formation_id")
    assert {k: v for k, v in via_tool.items() if k not in volatile} == {
        k: v for k, v in via_cli.items() if k not in volatile
    }, "the tool path and the CLI path must produce identical manifests apart from timestamps and roots"

    with pytest.raises(StateError, match="already exists"):
        apply_formation_init(formation_env.payload(), None, formation_env.orchestrator_run, None, {})


def test_formation_init_refuses_both_keys_and_an_already_allocated_prd_id(
    formation_env: FormationFixture,
) -> None:
    """FR01 (as amended) + FR03. Two refusals a silent default would have hidden."""
    from trw_mcp.tools._orchestration_formation import apply_formation_init

    with pytest.raises(StateError, match="not both"):
        apply_formation_init(
            formation_env.payload(),
            {"formation_id": "x", "member_id": "y"},
            tmp_run := formation_env.orchestrator_run,
            None,
            {},
        )
    assert not (tmp_run / "formation.yaml").exists()

    from trw_mcp.formation import FormationError, create

    prds = formation_env.project_root / "prds"
    prds.mkdir()
    (prds / "PRD-CORE-900-something.md").write_text("x", encoding="utf-8")
    with pytest.raises(FormationError, match="PRD-CORE-900"):
        create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=prds)


def test_join_formation_is_atomic_and_refuses_unknown_member(formation_env: FormationFixture) -> None:
    """FR04. N concurrent joins => N joined members and a revision delta of N.

    ATTRIBUTION. The revision assertion guards the locked read-modify-write in
    ``formation/_store.rewrite_manifest`` plus the ``revision + 1`` in
    ``formation/_join.join``: drop the lock and two threads read the same
    revision, so the delta falls below N. The stamping assertion guards
    ``_stamp_run_record``; the rebind assertion guards the refusal that stops a
    misresolved run from orphaning the first run's evidence.
    """
    from concurrent.futures import ThreadPoolExecutor

    from trw_mcp.formation import FormationError, create, join, load

    create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda item: join("release-train", item[0], item[1], pin_key=f"pin-{item[0]}"),
                sorted(formation_env.member_runs.items()),
            )
        )

    context = load(formation_env.orchestrator_run)
    assert context is not None
    assert context.manifest.revision == 3, "revision must advance by exactly one per join"
    assert [m.status for m in context.manifest.members] == ["joined", "joined"]
    assert {m.member_id: m.pin_key for m in context.manifest.members} == {
        "impl-1": "pin-impl-1",
        "impl-2": "pin-impl-2",
    }

    member_yaml = (formation_env.member_runs["impl-1"] / "meta" / "run.yaml").read_text(encoding="utf-8")
    assert "formation_id: release-train" in member_yaml and "member_id: impl-1" in member_yaml

    # Idempotent re-join: same run path, no revision bump.
    join("release-train", "impl-1", formation_env.member_runs["impl-1"], pin_key="pin-impl-1")
    again = load(formation_env.orchestrator_run)
    assert again is not None and again.manifest.revision == 3

    with pytest.raises(FormationError) as unknown:
        join("release-train", "impl-9", formation_env.member_runs["impl-1"])
    assert "impl-1" in str(unknown.value) and "impl-2" in str(unknown.value), "the refusal must list declared ids"

    elsewhere = make_run_dir(formation_env.trw_dir / "runs", "impl-1-restarted")
    with pytest.raises(FormationError, match="refusing to rebind"):
        join("release-train", "impl-1", elsewhere)
