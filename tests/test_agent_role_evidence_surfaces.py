"""Keep helper completion evidence aligned with each role's actual work."""

from __future__ import annotations

from pathlib import Path

AGENT = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "agents" / "trw-tester.md"


def test_tester_reports_tested_not_implemented() -> None:
    content = AGENT.read_text(encoding="utf-8")
    artifact = content.split("test_coverage:", 1)[1].split("```", 1)[0]
    assert "status: tested" in artifact
    assert "status: implemented" not in artifact
    assert "never claim production implementation" in artifact
