"""The offline deliver path must say that it evaluated no gate.

``trw_deliver`` (MCP) runs a six-gate table with three override policies and
validates a structured ``AcceptableFailureRecord``. ``trw-mcp local deliver`` —
the documented fallback when the MCP server is unreachable — does none of that:
it sets ``status`` and stamps a time.

That is defensible as a fallback. What was not defensible is that the resulting
``run.yaml`` was **byte-identical** to a gated delivery, so a run delivered with
no evidence at all read exactly like one that passed every gate. An unevaluated
check that looks like a passed check is the shape this codebase fixed four times
in the same window — in a shell allow/deny gate, a PRD proof-path check, the
config-consumer gate, and a sidecar refresh count. It was live in the delivery
record itself.

Found by an independent canon review, which reproduced it: ``local init``
followed immediately by ``local deliver`` exits 0 with no warning and leaves a run
marked ``delivered`` while still in ``phase: research``.

This does not weaken CONSTITUTION §1.a. The obligation binds the agent whichever
surface records the delivery; the stamp is what lets a later reader tell which
surface did.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def delivered_run(tmp_path: Path) -> Path:
    """A run taken straight from init to deliver, with no evidence of any kind."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    from trw_mcp.services.orchestration_service import mark_local_delivered, scaffold_run_directory

    created = scaffold_run_directory("probe", trw_dir=tmp_path / ".trw")
    run_path = Path(created["run_path"])
    mark_local_delivered("no evidence at all", run_path=run_path)
    return run_path


def test_the_run_is_delivered_without_leaving_research(delivered_run: Path) -> None:
    """Non-vacuity control: the gap this test is about must still be reachable.

    If the offline path ever starts enforcing a phase or a build check, this fails
    and the disclosure below becomes unnecessary — which is the good outcome, and
    should be noticed rather than silently kept.
    """
    from trw_mcp.state.persistence import FileStateReader

    run = FileStateReader().read_yaml(delivered_run / "meta" / "run.yaml")

    assert run["status"] == "delivered"
    assert run["phase"] == "research", "the offline path now enforces a phase; revisit this module"


def test_an_ungated_delivery_is_distinguishable_from_a_gated_one(delivered_run: Path) -> None:
    """The fix. RED before: run.yaml carried no signal that no gate ran."""
    from trw_mcp.state.persistence import FileStateReader

    run = FileStateReader().read_yaml(delivered_run / "meta" / "run.yaml")

    assert run["gate_evaluated"] is False, "an ungated delivery must positively say so"
    assert run["delivery_surface"] == "local_cli"


def test_the_deliver_event_carries_the_same_disclosure(delivered_run: Path) -> None:
    """run.yaml is the current state; the event log is the audit trail. A reader
    reconstructing what happened from events alone must see it too."""
    import json

    events = [
        json.loads(line)
        for line in (delivered_run / "meta" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    deliver = [e for e in events if e.get("event") == "deliver" or e.get("event_type") == "deliver"]

    assert deliver, f"no deliver event recorded; saw {[e.get('event') or e.get('event_type') for e in events]}"
    payload = deliver[-1]
    flat = {**payload, **(payload.get("data") or {}), **(payload.get("payload") or {})}
    assert flat.get("gate_evaluated") is False, "the audit trail does not disclose the ungated path"
