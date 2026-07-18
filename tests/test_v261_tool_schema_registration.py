"""Fresh-process schema contract for the v26.1 evidence and recovery tools."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _production_schemas(tmp_path: Path) -> dict[str, set[str]]:
    """Import the eager production registry in a clean interpreter and list tools."""
    source_root = Path(__file__).parents[1] / "src"
    (tmp_path / ".trw").mkdir()
    # tool_resolution_mode=all makes SurfaceAuthorityMiddleware a strict no-op so
    # the full registered schema surface is advertised (PRD-CORE-218 FR04).
    (tmp_path / ".trw/config.yaml").write_text("tool_resolution_mode: all\n", encoding="utf-8")
    code = """
import asyncio
import json
from trw_mcp.server._tools import mcp

tools = asyncio.run(mcp.list_tools())
print(json.dumps({tool.name: sorted(tool.parameters.get("properties", {})) for tool in tools}))
"""
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
    payload = json.loads(completed.stdout.splitlines()[-1])
    return {name: set(properties) for name, properties in payload.items()}


def test_fresh_production_process_exposes_v261_evidence_and_recovery_schema(tmp_path: Path) -> None:
    """A restarted production registry must advertise every enforceable input."""
    schemas = _production_schemas(tmp_path)
    assert "command_results" in schemas["trw_build_check"]
    assert {"review_completed", "reviewer_source", "reviewer_receipt_id"} <= schemas["trw_review"]
    assert {"delivery_id", "capability_token"} <= schemas["trw_deliver"]
    assert {"delivery_id"} <= schemas["trw_delivery_status"]
    assert {
        "delivery_id",
        "action",
        "capability_token",
        "expected_revision",
        "evidence_ref",
    } <= schemas["trw_delivery_recover"]
