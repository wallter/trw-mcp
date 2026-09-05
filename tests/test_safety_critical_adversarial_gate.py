"""PRD-CORE-255-FR03/FR04 + NFR01/NFR02 — the blocking adversarial-audit gate.

The 2026-06-16 "Potemkin gate" incident passed BOTH mandatory gates and was
caught only by the OPTIONAL adversarial pass. Nothing required that pass and
nothing checked it happened. These tests drive the REAL ``trw_deliver`` path —
no gate function is mocked — and assert that a safety-critical PRD scope, or one
naming a PRD whose file cannot be read, cannot deliver without a settled,
independently-verified adversarial-audit receipt.

Operator policy (2026-09-04 amendment to FR03/FR04): a run that declares NO PRD
scope is INERT — it reports ``safety_critical: not_declared`` and delivers. Only
a scope that NAMES an unshowable PRD fails closed.

Fails before the change: neither ``resolve_safety_critical_scope`` nor
``safety_critical_gate_result`` existed, ``safety_critical`` was not a
frontmatter key anywhere in the schema, and ``check_delivery_gates`` had no
adversarial requirement for any PRD at any risk level.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import structlog

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.review_signoffs import append_review_signoff
from trw_mcp.tools._delivery_helpers import check_delivery_gates
from trw_mcp.tools._delivery_safety_critical_gate import (
    NOT_DECLARED,
    UNKNOWN_SCOPE,
    find_satisfying_adversarial_receipt,
    resolve_safety_critical_scope,
    safety_critical_gate_result,
)
from trw_mcp.tools._review_manual import handle_manual_mode

_BLOCK_KEY = "safety_critical_adversarial_block"


def _write_prd(project: Path, prd_id: str, *, safety_critical: bool, body: str = "# body\n") -> Path:
    prds = project / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    path = prds / f"{prd_id}.md"
    path.write_text(
        f'---\nprd:\n  id: {prd_id}\n  title: "t"\n  safety_critical: {str(safety_critical).lower()}\n---\n\n{body}',
        encoding="utf-8",
    )
    return path


def _seed_run(project: Path, *, scope: str, task_type: str = "coding") -> Path:
    trw = project / ".trw"
    for sub in ("learnings/entries", "reflections", "context"):
        (trw / sub).mkdir(parents=True, exist_ok=True)
    run_id = "20260903T000000Z-sc"
    run = trw / "runs" / "task" / run_id
    (run / "meta").mkdir(parents=True, exist_ok=True)
    source = project / "src" / "gate.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    (run / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\nstatus: active\nphase: deliver\nprd_scope: {scope}\n"
        f"task_type: {task_type}\ncomplexity_class: MINIMAL\n",
        encoding="utf-8",
    )
    (run / "meta" / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-09-03T00:00:00Z", "event": "session_start"}),
                json.dumps({"ts": "2026-09-03T00:00:01Z", "event": "file_modified", "file": str(source)}),
                json.dumps(
                    {
                        "ts": "2026-09-03T00:00:02Z",
                        "event": "build_check_complete",
                        "test_count": 12,
                        "scope": "pytest tests",
                        "tests_passed": True,
                        "static_checks_clean": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return run


def _external_artifact(project: Path, body: str = "codex adversarial audit: 1 P1\n") -> tuple[str, str]:
    artifact = project / "reports" / "codex-audit.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(body, encoding="utf-8")
    return "reports/codex-audit.md", hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# FR03 — the scope resolver
# ---------------------------------------------------------------------------


class TestResolveSafetyCriticalScope:
    def test_safety_critical_frontmatter_flag_is_read_from_prd_scope(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")

        assert resolve_safety_critical_scope(run, tmp_path) is True

    def test_non_flagged_prd_scope_resolves_false(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-CORE-901", safety_critical=False)
        run = _seed_run(tmp_path, scope="[PRD-CORE-901]")

        assert resolve_safety_critical_scope(run, tmp_path) is False

    def test_receipt_recorded_prd_ids_join_the_union(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR03: the union includes ReviewReceipt.prd_ids, not just run.yaml."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        handle_manual_mode(
            [], run, "rv-1", datetime.now(timezone.utc).isoformat(), ["PRD-SEC-900"], review_completed=True
        )

        assert resolve_safety_critical_scope(run, tmp_path) is True

    def test_empty_scope_resolves_not_declared(self, tmp_path: Path) -> None:
        """FR03 (2026-09-04 amendment): no declared scope is INERT, not unknown.

        A PRD opts IN via ``safety_critical: true``; a run that names no PRD has
        nothing to opt in, so the gate reports rather than blocks. Fails before
        the amendment: the resolver returned ``UNKNOWN_SCOPE`` here.
        """
        empty = _seed_run(tmp_path, scope="[]")
        assert resolve_safety_critical_scope(empty, tmp_path) == NOT_DECLARED

    def test_named_prd_that_cannot_be_read_resolves_unknown_and_fails_closed(self, tmp_path: Path) -> None:
        """FR03: a run that CLAIMS a governing PRD it cannot show is the misrepresentation."""
        # A scope entry naming no PRD file on disk is unresolved, not "safe".
        unresolvable = _seed_run(tmp_path, scope="[PRD-CORE-902]")
        assert resolve_safety_critical_scope(unresolvable, tmp_path) == UNKNOWN_SCOPE

        # A PRD whose frontmatter cannot be parsed is unreadable, not "safe".
        prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds.mkdir(parents=True, exist_ok=True)
        (prds / "PRD-CORE-903.md").write_text("no frontmatter at all\n", encoding="utf-8")
        malformed = _seed_run(tmp_path, scope="[PRD-CORE-903]")
        assert resolve_safety_critical_scope(malformed, tmp_path) == UNKNOWN_SCOPE

    def test_one_readable_and_one_unreadable_entry_still_resolves_unknown(self, tmp_path: Path) -> None:
        """A resolvable benign PRD does not launder an unreadable sibling."""
        _write_prd(tmp_path, "PRD-CORE-901", safety_critical=False)
        run = _seed_run(tmp_path, scope="[PRD-CORE-901, PRD-CORE-902]")

        assert resolve_safety_critical_scope(run, tmp_path) == UNKNOWN_SCOPE

    def test_one_flagged_prd_in_a_mixed_scope_is_enough(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-CORE-901", safety_critical=False)
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-CORE-901, PRD-SEC-900]")

        assert resolve_safety_critical_scope(run, tmp_path) is True

    def test_absent_run_resolves_not_declared(self, tmp_path: Path) -> None:
        """With no pinned run there is no scope-declaration surface to read."""
        assert resolve_safety_critical_scope(None, tmp_path) == NOT_DECLARED


# ---------------------------------------------------------------------------
# FR04 — the blocking gate on the REAL deliver path
# ---------------------------------------------------------------------------


def _deliver(tmp_path: Path, run: Path, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> dict[str, Any]:
    tools = make_ceremony_server(monkeypatch, tmp_path)
    trw_dir = tmp_path / ".trw"
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run),
        patch(
            "trw_mcp.tools.ceremony._do_instruction_sync",
            return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        # These fixtures carry LEGACY build events, so opt into observe mode for
        # the build gate only. Without it ``delivery_blocked`` (which precedes
        # this PRD's gate in _GATE_TABLE) fires first and the assertions below
        # would pass for the wrong reason.
        patch(
            "trw_mcp.tools._delivery_helpers.get_config",
            return_value=TRWConfig().model_copy(update={"evidence_receipt_mode": "observe"}),
        ),
    ):
        return dict(tools["trw_deliver"].fn(run_path=str(run), skip_reflect=True, **kwargs))


def _operator_signoff(tmp_path: Path, review_ref: str, *, approver: str = "operator@example") -> str:
    """Mint an operator sign-off through the SHIPPED out-of-band mint path.

    ``append_review_signoff`` is the one writer the ``python -m
    trw_mcp.state.review_signoffs approve`` CLI calls, so a test can never prove
    a token shape the real mint step does not produce. Before the 2026-09-04
    FR04 amendment this helper did not exist and the literal string
    ``"operator-token-1"`` satisfied the gate.
    """
    return append_review_signoff(tmp_path / ".trw", review_ref=review_ref, approver=approver).approval_id


def _record_adversarial_review(
    tmp_path: Path,
    run: Path,
    *,
    prd_id: str,
    verified: bool = True,
    findings: list[dict[str, str]] | None = None,
    adversarial_pass: bool = False,
    reviewer_source: str = "cross_model",
    review_id: str = "rv-adv",
    operator_receipt_id: str | None = None,
) -> dict[str, Any]:
    """Relay an adversarial audit through the REAL trw_review manual path."""
    rel, digest = _external_artifact(tmp_path)
    receipt_id = digest if verified else "0" * 64
    if reviewer_source == "operator":
        receipt_id = operator_receipt_id if operator_receipt_id is not None else _operator_signoff(tmp_path, review_id)
    # A CRITICAL finding, not a warning: _compute_verdict maps warning-only to
    # verdict="warn", and FR04 requires a SETTLED verdict (pass|block). The two
    # settled shapes are "found something blocking" (critical => block) and
    # "found nothing, and earned the adversarial_pass flag" (=> pass).
    default_findings = [{"category": "security", "severity": "critical", "description": "unbounded read"}]
    return dict(
        handle_manual_mode(
            findings if findings is not None else default_findings,
            run,
            review_id,
            datetime.now(timezone.utc).isoformat(),
            [prd_id],
            reviewer_source=reviewer_source,
            reviewer_receipt_id=receipt_id,
            external_receipt_path=rel if verified else None,
            adversarial_pass=adversarial_pass,
            review_completed=True,
        )
    )


class TestSafetyCriticalAdversarialGate:
    def test_missing_adversarial_receipt_blocks_delivery(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR04 acceptance: safety-critical scope + no adversarial receipt => blocked."""
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")

        gates = check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")
        assert _BLOCK_KEY in gates
        assert "safety_critical_adversarial_audit_missing" in gates[_BLOCK_KEY]

        result = _deliver(tmp_path, run, monkeypatch)
        assert result["success"] is False
        assert _BLOCK_KEY in result

    def test_unreadable_named_prd_blocks_exactly_like_safety_critical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR03/FR04: a scope that NAMES a PRD it cannot show is never treated as safe.

        The remedy must name the offending id, so the caller can fix the claim
        rather than guess at it.
        """
        run = _seed_run(tmp_path, scope="[PRD-CORE-902]")

        result = _deliver(tmp_path, run, monkeypatch)

        assert result["success"] is False
        assert "PRD-CORE-902" in result[_BLOCK_KEY]
        assert "could not be read or parsed (unknown)" in result[_BLOCK_KEY]

    def test_undeclared_scope_is_inert_and_says_so(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR03/FR04 (2026-09-04 amendment): no declared scope => no block, but not silence.

        Fails before the amendment: an empty ``prd_scope`` resolved ``unknown``
        and hard-blocked every unscoped delivery in the repo.
        """
        run = _seed_run(tmp_path, scope="[]")

        gates = check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")
        assert _BLOCK_KEY not in gates
        assert gates["safety_critical"] == "not_declared"
        assert "scope the run to its PRDs" in gates["safety_critical_adversarial_advisory"]

        result = _deliver(tmp_path, run, monkeypatch)

        assert _BLOCK_KEY not in result
        assert result["success"] is True, result.get("errors")
        # The advisory reaches the CALLER, not just the gate dict.
        assert result["safety_critical"] == "not_declared"
        assert result["safety_critical_adversarial_advisory"]

    def test_non_safety_critical_scope_never_evaluates_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: an ordinary PRD's delivery is untouched by FR04."""
        _write_prd(tmp_path, "PRD-CORE-901", safety_critical=False)
        run = _seed_run(tmp_path, scope="[PRD-CORE-901]")

        result = _deliver(tmp_path, run, monkeypatch)

        assert _BLOCK_KEY not in result
        assert result["success"] is True, result.get("errors")

    def test_verified_cross_model_adversarial_receipt_satisfies_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        review = _record_adversarial_review(tmp_path, run, prd_id="PRD-SEC-900")
        assert review["reviewer_family"] == "cross_model", "precondition: the family must have VERIFIED"

        satisfying = find_satisfying_adversarial_receipt(run)

        assert satisfying == review["review_receipt_id"]
        assert _BLOCK_KEY not in check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")

    def test_self_declared_cross_model_without_digest_does_not_satisfy_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B2: the strongest family label must not be mintable by assertion."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        review = _record_adversarial_review(tmp_path, run, prd_id="PRD-SEC-900", verified=False)
        assert review["family_downgraded_reason"], "precondition: the claim must have been refused"

        assert find_satisfying_adversarial_receipt(run) is None
        assert _BLOCK_KEY in check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")

    def test_operator_receipted_review_satisfies_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The legit operator flow still delivers — through a real signed sign-off."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        review = _record_adversarial_review(tmp_path, run, prd_id="PRD-SEC-900", reviewer_source="operator")
        # Omitted-when-empty (the result TypedDict is total=False): no downgrade recorded.
        assert review.get("family_downgraded_reason", "") == "", "precondition: the sign-off must have resolved"

        assert find_satisfying_adversarial_receipt(run) is not None
        assert _BLOCK_KEY not in check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")

    def test_self_minted_operator_receipt_does_not_satisfy_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The P0 this amendment closes: an in-process caller minting its own sign-off.

        ``trw_review(mode="manual", findings=[], review_completed=True,
        adversarial_pass=True, reviewer_identity={"reviewer_source": "operator",
        "reviewer_receipt_id": "anything"})`` used to satisfy EVERY FR04
        condition — the reviewed agent certified its own adversarial audit.
        Fails before the fix: ``find_satisfying_adversarial_receipt`` returned the
        receipt id and the gate did not block.
        """
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        review = _record_adversarial_review(
            tmp_path,
            run,
            prd_id="PRD-SEC-900",
            reviewer_source="operator",
            operator_receipt_id="anything",
            findings=[],
            adversarial_pass=True,
        )

        assert review["family_downgraded_reason"] == "operator_receipt_unresolved"
        from trw_mcp.tools._review_receipt_writer import load_latest_review_evidence

        _, receipt = load_latest_review_evidence(run, tmp_path)
        assert receipt is not None
        assert receipt.adversarial_pass is False
        assert "adversarial_audit" not in receipt.realized_rubric_ids
        assert find_satisfying_adversarial_receipt(run) is None

        result = _deliver(tmp_path, run, monkeypatch)
        assert result["success"] is False
        assert _BLOCK_KEY in result

    def test_operator_signoff_for_another_review_does_not_transfer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One approval authorizes ONE review — replay against a second is refused."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        borrowed = _operator_signoff(tmp_path, "rv-some-other-review")
        review = _record_adversarial_review(
            tmp_path,
            run,
            prd_id="PRD-SEC-900",
            reviewer_source="operator",
            review_id="rv-adv",
            operator_receipt_id=borrowed,
            findings=[],
            adversarial_pass=True,
        )

        assert review["family_downgraded_reason"] == "operator_approval_scope_mismatch"
        assert find_satisfying_adversarial_receipt(run) is None
        assert _BLOCK_KEY in check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")

    def test_gate_re_resolves_the_signoff_and_refuses_when_it_is_gone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gate re-resolves; it never trusts the receipt's own operator stamp.

        A receipt minted under a valid sign-off must stop authorizing delivery
        once that approval no longer resolves (revoked, expired, or a journal
        that has been replaced). Fails before the fix: the gate's predicate only
        asked whether ``reviewer_receipt_id`` was a non-empty string, which the
        persisted receipt always satisfies.
        """
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        _record_adversarial_review(tmp_path, run, prd_id="PRD-SEC-900", reviewer_source="operator")
        assert find_satisfying_adversarial_receipt(run) is not None, "precondition: valid while it resolves"

        (tmp_path / ".trw" / "approvals" / "review-signoffs.jsonl").unlink()

        assert find_satisfying_adversarial_receipt(run) is None
        assert _BLOCK_KEY in check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")

    def test_info_only_findings_do_not_settle_the_audit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR04 condition (4): severity must reach warning, or adversarial_pass must be earned."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        _record_adversarial_review(
            tmp_path,
            run,
            prd_id="PRD-SEC-900",
            findings=[{"category": "style", "severity": "info", "description": "nit"}],
        )

        assert find_satisfying_adversarial_receipt(run) is None

    def test_verified_adversarial_pass_settles_a_zero_finding_audit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        _record_adversarial_review(tmp_path, run, prd_id="PRD-SEC-900", findings=[], adversarial_pass=True)

        assert find_satisfying_adversarial_receipt(run) is not None

    def test_unverified_adversarial_pass_flag_is_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A caller cannot self-grant the pass flag from an unverified posture."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        _record_adversarial_review(
            tmp_path, run, prd_id="PRD-SEC-900", verified=False, findings=[], adversarial_pass=True
        )

        from trw_mcp.tools._review_receipt_writer import load_latest_review_evidence

        _, receipt = load_latest_review_evidence(run, tmp_path)
        assert receipt is not None
        assert receipt.adversarial_pass is False
        assert find_satisfying_adversarial_receipt(run) is None

    def test_legacy_review_yaml_without_a_typed_receipt_is_absent_evidence(self, tmp_path: Path) -> None:
        """NFR03: a legacy meta/review.yaml realizes no adversarial rubric."""
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")
        (run / "meta" / "review.yaml").write_text(
            "substantive: true\nverdict: pass\ncritical_count: 0\n", encoding="utf-8"
        )

        gates = check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")

        assert _BLOCK_KEY in gates
        assert "trw_review" in gates[_BLOCK_KEY]
        assert "/mcp reconnect" in gates[_BLOCK_KEY]


# ---------------------------------------------------------------------------
# NFR01 / NFR02 — fail-closed, and logged AND returned
# ---------------------------------------------------------------------------


class TestFailClosedAndObservable:
    def test_unreadable_ttl_config_fails_closed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """NFR01: a config read that RAISES expires the receipt, never grants forever."""
        from trw_mcp.state import _evidence_gates

        def _boom() -> TRWConfig:
            raise RuntimeError("config unreadable")

        monkeypatch.setattr("trw_mcp.models.config.get_config", _boom)

        assert _evidence_gates.review_verdict_is_expired(datetime.now(timezone.utc).isoformat()) is True

    def test_unreadable_named_prd_never_downgrades_to_not_critical(self, tmp_path: Path) -> None:
        """NFR01: the fail-closed branch is a NAMED PRD that cannot be shown.

        It resolves ``unknown`` -- never ``False`` -- so no read failure can turn
        a block into a pass.
        """
        prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds.mkdir(parents=True, exist_ok=True)
        (prds / "PRD-CORE-903.md").write_text("no frontmatter at all\n", encoding="utf-8")
        run = _seed_run(tmp_path, scope="[PRD-CORE-903]")

        assert resolve_safety_critical_scope(run, tmp_path) == UNKNOWN_SCOPE

    def test_missing_run_yaml_is_not_declared_not_a_bypass(self, tmp_path: Path) -> None:
        """An absent scope SOURCE declares no scope -- and gains an evader nothing.

        Deleting run.yaml reaches the same inert state as writing ``prd_scope:
        []``, which policy already permits, so this is not a new escape hatch. It
        is asserted rather than assumed so a future "unreadable source => False"
        regression (the one downgrade NFR01 forbids) is caught.
        """
        run = _seed_run(tmp_path, scope="[]")
        (run / "meta" / "run.yaml").unlink()

        assert resolve_safety_critical_scope(run, tmp_path) == NOT_DECLARED

    def test_gate_decision_logged_and_returned(self, tmp_path: Path) -> None:
        """NFR02: the block is a structlog WARNING *and* a response key."""
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]")

        with structlog.testing.capture_logs() as logs:
            outcome = safety_critical_gate_result(run)

        assert outcome.should_block is True
        assert outcome.message
        events = [entry for entry in logs if entry.get("event") == "safety_critical_adversarial_block"]
        assert events and events[0]["log_level"] == "warning"
        assert events[0]["reason_code"] == "safety_critical_adversarial_audit_missing"
        # And the same decision reaches the caller-visible gate result.
        assert check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")[_BLOCK_KEY] == outcome.message

    def test_advisory_mode_reports_the_shortfall_without_blocking(self, tmp_path: Path) -> None:
        """FR04 fires only under block_coding/block_all; an advisory project still SEES it."""
        _write_prd(tmp_path, "PRD-SEC-900", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-900]", task_type="docs")
        config = TRWConfig().model_copy(update={"deliver_gate_mode": "advisory"})
        with patch("trw_mcp.tools._deliver_gate_mode.get_config", return_value=config):
            outcome = safety_critical_gate_result(run)

        assert outcome.should_block is False
        assert "Delivery advisory" in outcome.advisory
