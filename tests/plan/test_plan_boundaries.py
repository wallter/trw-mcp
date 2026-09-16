"""PRD-CORE-275-FR07/FR08/FR09 and NFR02/NFR03/NFR05: the boundaries.

These are the tests that keep the feature from becoming something else. Each one
pins a property the PRD claims, and each guard has a control that shows the
property is enforced rather than merely true today by accident.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from trw_mcp.plan import PlanError, PlanRefusal, build_proposal, encode, parse_proposal

PID = "a" * 32
PLAN_SRC = Path(__file__).resolve().parents[2] / "src" / "trw_mcp" / "plan"
CLI_SRC = Path(__file__).resolve().parents[2] / "src" / "trw_mcp" / "tools" / "_plan_cli.py"

#: Modules that would let this package reach the network, spawn work, or send.
#: Transport belongs to the agent's own MCP session (FR08).
FORBIDDEN_IMPORTS = ("socket", "subprocess", "http", "urllib", "asyncio", "trw_mcp.comms", "fastmcp")

#: Anything that could turn a plan into authority (FR07).
AUTHORITY_MODULES = ("trw_mcp.state.ceremony", "trw_mcp.tools.ceremony", "trw_mcp.state.phases")


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _package_imports() -> set[str]:
    found: set[str] = set()
    for path in sorted(PLAN_SRC.glob("*.py")):
        found |= _imports_of(path)
    return found


def test_the_plan_package_cannot_reach_transport() -> None:
    """FR08, by import closure over the leaf modules.

    Scoped deliberately: this is about the plan modules, NOT the whole trw-mcp
    entry point, whose shared CLI bootstrap already reads files and may import
    transport. Import closure is EVIDENCE for the property, not a security proof
    on its own.
    """
    imported = _package_imports()

    for forbidden in FORBIDDEN_IMPORTS:
        assert not any(name == forbidden or name.startswith(f"{forbidden}.") for name in imported), (
            f"plan package imports {forbidden}"
        )


def test_the_plan_package_cannot_reach_an_authority_mutator() -> None:
    """FR07: the real argument, stronger than the field allowlist."""
    imported = _package_imports() | _imports_of(CLI_SRC)

    for forbidden in AUTHORITY_MODULES:
        assert not any(name.startswith(forbidden) for name in imported), f"reaches {forbidden}"


def test_the_cli_adapter_imports_no_transport_either() -> None:
    imported = _imports_of(CLI_SRC)
    for forbidden in ("socket", "http", "urllib", "trw_mcp.comms"):
        assert not any(name == forbidden or name.startswith(f"{forbidden}.") for name in imported)


@pytest.mark.parametrize(
    "field",
    ["approved", "approval", "permission", "grant", "completion", "verdict", "signoff", "override", "safety"],
)
def test_an_authority_field_is_refused(field: str) -> None:
    raw = json.loads(encode(build_proposal(plan_id=PID, revision=1, paths=["src/a.py"], test_paths=[], summary="s")))
    raw[field] = True

    with pytest.raises(PlanError) as excinfo:
        parse_proposal(json.dumps(raw))

    assert excinfo.value.refusal is PlanRefusal.AUTHORITY_FIELD


def test_authority_refusal_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """NEGATIVE CONTROL: empty the allowlist and the refusal must change.

    Without this, the parametrized test above is equally satisfied by a parser
    that rejects every unknown key for an unrelated reason.
    """
    from trw_mcp.plan import _schema

    raw = json.loads(encode(build_proposal(plan_id=PID, revision=1, paths=["src/a.py"], test_paths=[], summary="s")))
    raw["approved"] = True
    with pytest.raises(PlanError) as guarded:
        parse_proposal(json.dumps(raw))
    assert guarded.value.refusal is PlanRefusal.AUTHORITY_FIELD

    monkeypatch.setattr(_schema, "AUTHORITY_KEYS", frozenset())

    with pytest.raises(PlanError) as unguarded:
        parse_proposal(json.dumps(raw))
    assert unguarded.value.refusal is PlanRefusal.UNKNOWN_KEY


def test_a_hostile_body_is_carried_as_data_not_executed() -> None:
    """FR07: the body is peer text. It is bounded and stored, never interpreted."""
    hostile = "$(rm -rf /) `whoami` <script>alert(1)</script> ../../etc/passwd"
    body = build_proposal(plan_id=PID, revision=1, paths=["src/a.py"], test_paths=[], summary=hostile)

    assert parse_proposal(encode(body))["summary"] == hostile


def test_no_new_mcp_tool_action_or_config_field() -> None:
    """NFR03: this slice adds CLI verbs, and nothing to the MCP surface."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.surface_packs import PACK_TOOLS
    from trw_mcp.server._tools import raw_registered_tool_names

    registered = set(raw_registered_tool_names())
    packed = {tool for tools in PACK_TOOLS.values() for tool in tools}

    assert registered == packed, "registrar and packs disagree"
    assert not any(name.startswith("trw_plan") for name in registered)
    assert not any(field.startswith("plan_") for field in TRWConfig.model_fields)


def test_the_plan_verbs_are_registered_on_the_real_parser() -> None:
    """NFR04: wired, not merely present."""
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    actions = [a for a in parser._actions if getattr(a, "choices", None) and "plan" in (a.choices or {})]

    assert actions, "`trw-mcp plan` is not registered on the CLI parser"
    plan_parser = actions[0].choices["plan"]
    verbs = [a for a in plan_parser._actions if getattr(a, "choices", None)]
    assert set(verbs[0].choices) == {"precheck", "propose", "review", "verify"}


def test_the_dispatcher_routes_plan() -> None:
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    assert "plan" in SUBCOMMAND_HANDLERS


def test_a_body_is_bounded_against_the_configured_value_not_a_literal() -> None:
    """NFR02: the bound is read from config, so a config change cannot make a
    body silently undeliverable while a hardcoded test stays green."""
    from trw_mcp.models.config import TRWConfig

    limit = TRWConfig().comms_body_max_bytes
    body = encode(build_proposal(plan_id=PID, revision=1, paths=["src/a.py"], test_paths=[], summary="s"))

    assert len(body.encode("utf-8")) < limit


def test_an_oversized_summary_refuses_before_emission() -> None:
    raw = json.loads(encode(build_proposal(plan_id=PID, revision=1, paths=["src/a.py"], test_paths=[], summary="s")))
    raw["summary"] = "x" * 4096

    with pytest.raises(PlanError) as excinfo:
        parse_proposal(json.dumps(raw))

    assert excinfo.value.refusal is PlanRefusal.TOO_LARGE
