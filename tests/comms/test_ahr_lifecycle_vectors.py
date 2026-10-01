"""PRD-CORE-349 FR04: the AHR lifecycle replay agrees with the reference on every lifecycle vector.

The vectors are vendored unchanged from ``specs/handoff/vectors/lifecycle`` (AHR 1.0-rc.1). The
expected outcomes follow the reference runner ``tools/run_vectors.sh``: a valid scenario replays
with no violation to the final state the reference prints (recorded below from a reference run on
2026-10-01), and an invalid scenario reports the rule its directory name starts with.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from trw_mcp.comms._ahr_state import AHR_MEDIA, Target, replay
from trw_mcp.handoff import digest, load

LIFECYCLE = Path(__file__).resolve().parents[1] / "handoff" / "vectors" / "lifecycle"
VALID = sorted(path for path in (LIFECYCLE / "valid").iterdir() if path.is_dir())
INVALID = sorted(path for path in (LIFECYCLE / "invalid").iterdir() if path.is_dir())

#: ``ahr_lifecycle.py`` output ``ok final_state=<state>`` per valid scenario (reference run, Observed).
FINAL_STATE = {
    "critical-human-pass-after-fail": "accepted",
    "critical-not-checkable-human-verdict": "accepted",
    "critical-verify-before-accept": "completed",
    "minimal-unaddressed-lifecycle": "completed",
    "standard-chained-events": "accepted",
    "standard-contradiction-answered": "accepted",
    "standard-declined": "declined",
    "standard-escalated-to-critical": "completed",
    "standard-expired": "expired",
    "standard-happy-path": "completed",
    "standard-pass-after-fail-same-readback": "accepted",
    "standard-returned-after-contradiction": "reported",
    "standard-withdrawn": "withdrawn",
    "superseded-before-accept": "superseded",
    "unaddressed-escalation-decline": "declined",
}


def _resolver(base: Path, ev: object) -> Callable[[], Target | None]:
    """The reference ``_target``: a ``file:<name>`` ref inside the scenario directory."""

    def resolve() -> Target | None:
        assert isinstance(ev, dict)
        ref = ev["ref"]
        uri = ref["uri"]
        if not uri.startswith("file:"):
            raise ValueError(f"scenario refs must be file: URIs, got {uri}")
        path = (base / uri[len("file:") :]).resolve()
        if base.resolve() not in path.parents:
            raise ValueError(f"ref escapes the scenario directory: {uri}")
        if ref.get("media_type") == AHR_MEDIA:
            doc = load(path)
            return doc, digest(doc)
        raw = path.read_bytes()
        return raw, "sha256:" + hashlib.sha256(raw).hexdigest()

    return resolve


def _replay(base: Path) -> tuple[str, list[str]]:
    lines = [line for line in (base / "events.jsonl").read_text(encoding="utf-8").split("\n") if line.strip()]
    events = [json.loads(line) for line in lines]
    return replay(load(base / "handoff.json"), [(ev, _resolver(base, ev)) for ev in events])


def test_every_valid_scenario_has_a_recorded_reference_outcome() -> None:
    assert sorted(path.name for path in VALID) == sorted(FINAL_STATE)
    assert INVALID and all("--" in path.name for path in INVALID), "each invalid scenario names its rule"


@pytest.mark.parametrize("scenario", VALID, ids=lambda path: path.name)
def test_valid_lifecycle_replays_to_the_reference_final_state(scenario: Path) -> None:
    state, errs = _replay(scenario)
    assert errs == []
    assert state == FINAL_STATE[scenario.name]


@pytest.mark.parametrize("scenario", INVALID, ids=lambda path: path.name)
def test_invalid_lifecycle_reports_the_named_rule(scenario: Path) -> None:
    want = scenario.name.split("--")[0]
    _state, errs = _replay(scenario)
    assert errs, "an invalid scenario replayed clean"
    assert any(e.startswith((f"{want} ", f"{want}:")) or f" {want} " in e for e in errs), errs
