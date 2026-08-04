"""Fresh-process schema contract for the v26.1 evidence and recovery tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _production_schemas(tmp_path: Path) -> dict[str, set[str]]:
    """Import the eager production registry in a clean interpreter and list tools."""
    # tool_resolution_mode=all makes SurfaceAuthorityMiddleware a strict no-op so
    # the full registered schema surface is advertised (PRD-CORE-218 FR04);
    # _run_registry_probe_raw writes that config.
    code = """
import asyncio
import json
from trw_mcp.server._tools import mcp

tools = asyncio.run(mcp.list_tools())
print(json.dumps({tool.name: sorted(tool.parameters.get("properties", {})) for tool in tools}))
"""
    return _run_registry_probe(tmp_path, code)


def _production_param_descriptions(tmp_path: Path, tool: str, param: str) -> str:
    """Return the served JSON-Schema ``description`` for one tool parameter.

    A collapsed object parameter advertises its accepted keys in its own
    schema description rather than as top-level properties, and that
    description IS served to the caller. Asserting on it keeps the
    "advertise every enforceable input" contract checkable after a
    signature collapse, instead of pinning the contract to a flat shape.
    """
    code = f"""
import asyncio
import json
from trw_mcp.server._tools import mcp

tools = asyncio.run(mcp.list_tools())
for tool in tools:
    if tool.name == {tool!r}:
        prop = tool.parameters.get("properties", {{}}).get({param!r}, {{}})
        print(json.dumps(prop.get("description", "")))
        break
else:
    print(json.dumps(""))
"""
    payload = _run_registry_probe_raw(tmp_path, code)
    return str(json.loads(payload))


def _run_registry_probe(tmp_path: Path, code: str) -> dict[str, set[str]]:
    payload = json.loads(_run_registry_probe_raw(tmp_path, code))
    return {name: set(properties) for name, properties in payload.items()}


def _run_registry_probe_raw(tmp_path: Path, code: str) -> str:
    source_root = Path(__file__).parents[1] / "src"
    # Idempotent so either probe works standalone or after the other.
    (tmp_path / ".trw").mkdir(exist_ok=True)
    (tmp_path / ".trw/config.yaml").write_text("tool_resolution_mode: all\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source_root)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.splitlines()[-1]


def test_fresh_production_process_exposes_v261_evidence_and_recovery_schema(tmp_path: Path) -> None:
    """A restarted production registry must advertise every enforceable input."""
    schemas = _production_schemas(tmp_path)
    assert "command_results" in schemas["trw_build_check"]
    assert {"review_completed", "reviewer_identity"} <= schemas["trw_review"]
    # reviewer_source / reviewer_receipt_id were collapsed out of the flat
    # signature into the reviewer_identity object. They are still enforceable
    # inputs, so they must still be ADVERTISED -- now in that parameter's own
    # served schema description. Asserting reachability rather than top-level
    # position keeps this contract honest across the collapse; asserting only
    # that "reviewer_identity" exists would pass for an opaque object with no
    # documented keys, which is the failure this line exists to catch.
    reviewer_identity_desc = _production_param_descriptions(tmp_path, "trw_review", "reviewer_identity")
    assert "reviewer_source" in reviewer_identity_desc
    assert "reviewer_receipt_id" in reviewer_identity_desc
    assert {"delivery_id", "capability_token"} <= schemas["trw_deliver"]
    assert {"delivery_id"} <= schemas["trw_delivery_status"]
    assert {
        "delivery_id",
        "action",
        "capability_token",
        "expected_revision",
        "evidence_ref",
    } <= schemas["trw_delivery_recover"]
