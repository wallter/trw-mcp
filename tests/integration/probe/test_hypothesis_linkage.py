"""FR-05 — hypothesis linkage: verdict write-back (PRD-CORE-144)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from trw_mcp.models.probe import ProbeAssumption
from trw_mcp.probe.harness import run_probe
from trw_mcp.probe.linkage import write_verdict_back


def test_verdict_written_back_atomically(tmp_path: Path) -> None:
    # FR-05: probe verdict is written back into the assumption record.
    assumption = ProbeAssumption(hypothesis_id="HYP-PLAN-042-A1", claim="parse 50MB <5s", polarity="positive")
    result = run_probe(
        hypothesis="parse 50MB <5s",
        command=f"{sys.executable} -c \"print('done')\"",
        run_id="run-42",
        timeout_s=10,
        hypothesis_id="HYP-PLAN-042-A1",
    )
    dest = tmp_path / "branch-A" / "assumption.json"
    updated = write_verdict_back(assumption, result, dest=dest)

    # FR-05 A3: write is atomic (file exists complete, no .tmp left behind).
    assert dest.exists()
    assert not list(tmp_path.glob("**/*.tmp"))
    on_disk = json.loads(dest.read_text())
    assert on_disk["probe_result_ref"] == "run-42"
    assert updated.probe_result_ref == "run-42"


def test_verdict_write_back_overwrites_prior(tmp_path: Path) -> None:
    assumption = ProbeAssumption(hypothesis_id="H1", claim="c")
    dest = tmp_path / "a.json"
    dest.write_text("STALE")
    result = run_probe(
        hypothesis="c",
        command=f'{sys.executable} -c "pass"',
        run_id="run-99",
        timeout_s=10,
    )
    write_verdict_back(assumption, result, dest=dest)
    assert json.loads(dest.read_text())["probe_result_ref"] == "run-99"
