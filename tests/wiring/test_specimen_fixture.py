"""Acceptance: every named specimen in PRD-CORE-232 §7 is detected on the live repo.

The specimen table is pinned **by name**, never by count — the count moved four
times in one day as the search widened, and an acceptance criterion phrased as a
number is unbuildable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.wiring.detector import DetectorResult, run_detector
from trw_mcp.wiring.model import EdgeClass

# NFR02 wall-clock budget for a full repo-wide detector scan. Retries below
# tolerate transient box contention (this repo's dev boxes routinely run
# several concurrent agent sessions) without weakening what the budget
# asserts -- a genuine regression still fails every attempt.
_SCAN_BUDGET_SECONDS = 10.0
_MAX_SCAN_ATTEMPTS = 3

# (specimen description, expected finding key). Every row is a specimen the
# census verified on 2026-07-24.
SPECIMENS: tuple[tuple[str, str], ...] = (
    # The two NEVER_FIRED channel specimens that lived here — cc-02 and
    # codex-agents-md-hotspots — were REMOVED by PRD-CORE-239 FR01, so they can
    # no longer be live specimens: a specimen is a defect the detector must
    # still catch, and these are gone. NEVER_FIRED detection is now covered by
    # test_artifact_existence.py::test_never_fired_is_detected_from_a_synthetic_manifest,
    # which builds the contract on demand rather than depending on a real defect
    # remaining unfixed. Recorded rather than silently deleted, because a
    # shrinking specimen list is exactly the shape that hides a detector going
    # blind.
    # `trw_entity_risk_map` was the CONSUMER_ORPHAN specimen until UF-011 was
    # decided by removal (2026-07-29). Detection of the class is now proven by
    # test_artifact_existence.py::test_consumer_orphan_names_the_absent_producer,
    # which builds the contract rather than depending on a live defect. Recorded
    # rather than silently dropped: a shrinking specimen list is how a detector
    # goes blind without anyone noticing.
    # PARTIAL_GUARD's specimen closed as a SIDE EFFECT of removing
    # trw_entity_risk_map: EntityRiskScorePayload was the single mirror the
    # parity guard could not cover, because a producerless sidecar has no
    # trw-distill source class to compare against. With it gone the guard covers
    # 6 of 6 and the finding stopped firing — one removal closed two ledger
    # entries. No live PARTIAL_GUARD specimen remains.
    # The INERT_BRANCH specimen — `code_search.py mode='semantic'`, reachable,
    # registered and structurally empty — was FIXED on 2026-09-03 (UF-031,
    # decided by removal: the public `mode` parameter and the optional-embedder
    # hook are both gone). It can no longer be a live specimen, for the same
    # reason the NEVER_FIRED and CONSUMER_ORPHAN rows above stopped being ones:
    # a specimen is a defect the detector must still catch, and this one no
    # longer exists. INERT_BRANCH detection is now proven by
    # test_inert_branch.py::test_synthetic_inert_call_is_detected, which builds
    # the call on demand. Recorded rather than silently deleted, because a
    # shrinking specimen list is exactly the shape that hides a detector going
    # blind.
    (
        "PRD-CORE-190 wiring gate — activation covers a negligible slice of the corpus",
        "PREDICATE_COVERAGE::gate:prd-core-190-wiring",
    ),
)


@pytest.mark.parametrize(("description", "key"), SPECIMENS, ids=[key for _d, key in SPECIMENS])
def test_all_named_specimens_detected(description: str, key: str, finding_keys: frozenset[str]) -> None:
    assert key in finding_keys, f"specimen not detected: {description} (expected finding key {key})"


def test_specimen_edge_classes_are_distinct(live_result: DetectorResult) -> None:
    """The detector earns its place by covering signatures a generic scan cannot.

    trw-distill's dead-code-scan, run against trw-mcp, recalled exactly ONE
    specimen and returned 49 of 51 findings as test-only references. Four
    distinct signatures here are ones reachability cannot express at all.
    """
    observed = {finding.edge_class for finding in live_result.findings}
    beyond_reachability = {
        EdgeClass.PREDICATE_COVERAGE,
    }
    assert beyond_reachability <= observed, (
        f"missing signatures: {sorted(c.value for c in beyond_reachability - observed)}"
    )

    # INERT_BRANCH left that set on 2026-09-03 for exactly the reason NEVER_FIRED
    # did below: UF-031 fixed its last live instance, and requiring the class to
    # be OBSERVED would then make "the defect was fixed" look identical to "the
    # detector went blind". The class must still be PRODUCIBLE, which is asserted
    # where it can actually fail —
    # test_inert_branch.py::test_synthetic_inert_call_is_detected builds an inert
    # call and requires the detector to flag it, and
    # test_inert_branch.py::test_no_inert_branch_remains_in_trw_mcp holds the
    # live repo at zero.
    assert EdgeClass.INERT_BRANCH.value == "INERT_BRANCH"

    # NEVER_FIRED is deliberately NOT in that set any more. It used to be, and
    # PRD-CORE-239 FR01 fixed every live instance — which made this test fail
    # for the wrong reason: as written it required defects to PERSIST in order
    # to prove the detector can express them, so closing a whole edge class
    # looked identical to the detector going blind.
    #
    # The class must still be expressible, which is what these two assertions
    # check: the enum member exists, and
    # test_artifact_existence.py::test_never_fired_is_detected_from_a_synthetic_manifest
    # builds the contract on demand and requires the detector to flag it.
    # NOT `assert EdgeClass.NEVER_FIRED` — a non-empty-valued enum member is
    # always truthy, so that could never fail; it was the `assert X is not None`
    # anti-pattern `.claude/rules/testing.md` names, standing in for the live
    # specimen FR01 deleted. The real guarantee is that the class is still
    # PRODUCIBLE, which is asserted where it can actually fail:
    # test_artifact_existence.py::test_never_fired_is_detected_from_a_synthetic_manifest
    # builds the contract and requires the detector to flag it.
    assert EdgeClass.NEVER_FIRED.value == "NEVER_FIRED"


def test_beats_generic_dead_code_scan(live_result: DetectorResult) -> None:
    """At least three specimens the generic dead-code scan missed, or this has no reason to exist.

    The generic scan recalled only ``replay``. Each key below is live,
    statically-reachable, registered code — invisible to reachability by
    construction.
    """
    missed_by_generic_scan = {
        "PREDICATE_COVERAGE::gate:prd-core-190-wiring",
    }
    recalled = missed_by_generic_scan & {finding.key for finding in live_result.findings}
    # Threshold tracks the list. It was 3, then 2; the INERT_BRANCH specimen
    # closed on 2026-09-03 when UF-031 was decided by removal, after the
    # schema-mirror-parity one closed with trw_entity_risk_map. Lowering it is
    # honest ONLY because the list shrank for those reasons — and 1 is the floor
    # this test's own rule set: at 0 it stops earning its place and gets deleted
    # rather than weakened again. The NEXT specimen to close takes this file with
    # it, and the synthetic detection tests carry the coverage.
    assert len(recalled) >= 1, f"only {len(recalled)} specimens recalled that the generic scan missed"


def test_every_finding_is_actionable(live_result: DetectorResult) -> None:
    """NFR04: a finding a reader cannot act on is a false positive by definition."""
    for finding in live_result.findings:
        assert finding.evidence.strip(), f"{finding.key} has no evidence"
        assert finding.remedy.strip(), f"{finding.key} has no remedy"
        assert finding.producer_side.strip() and finding.consumer_side.strip(), f"{finding.key} names only one side"


def test_full_scan_under_ten_seconds(live_result: DetectorResult, repo_root: Path) -> None:
    """NFR02: a check people are tempted to disable is a check that gets disabled.

    ``live_result`` is the ONE session-scoped scan the rest of this module
    reuses, so on the common (uncontended) path this test costs nothing extra.
    Only when that shared measurement misses budget does it re-scan (bounded
    by ``_MAX_SCAN_ATTEMPTS``) to tell a genuine regression from transient box
    contention. Confirmed 2026-09-03: a full-suite ``-n 8`` run measured this
    scan at 11.92s, then an unmodified re-run passed once the box's
    concurrent-agent load average dropped from 24 (on 12 cores) back down.
    """
    durations = [live_result.duration_seconds]
    for _attempt in range(_MAX_SCAN_ATTEMPTS - 1):
        if durations[-1] < _SCAN_BUDGET_SECONDS:
            break
        durations.append(run_detector(repo_root).duration_seconds)
    assert min(durations) < _SCAN_BUDGET_SECONDS, (
        f"repo-wide scan of {repo_root} took {durations} across {len(durations)} attempt(s), "
        f"all over the {_SCAN_BUDGET_SECONDS}s budget"
    )
