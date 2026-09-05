"""PRD-CORE-255-FR04 (2026-09-04 amendment) — operator sign-offs are not self-mintable.

The defect these tests pin: ``adversarial_source_is_verified`` accepted an
``operator`` posture whenever ``reviewer_receipt_id`` was any non-empty string.
Combined with ``substantive = bool(validated) or review_completed``, the agent
under review could call ``trw_review(mode="manual", findings=[],
review_completed=True, adversarial_pass=True, reviewer_identity={
"reviewer_source": "operator", "reviewer_receipt_id": "anything"})`` and mint a
receipt that satisfied every FR04 condition — self-certifying the very
adversarial audit the gate exists to require.

Every test here goes red if the fix is reverted: on HEAD-before, the resolver
module did not exist and the predicate returned ``True`` for all of these.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.state import review_signoffs
from trw_mcp.state.review_signoffs import (
    REASON_EXPIRED,
    REASON_POLICY_UNREADABLE,
    REASON_SCOPE_MISMATCH,
    REASON_SIGNATURE_INVALID,
    REASON_TTL_EXCEEDED,
    REASON_UNREADABLE,
    REASON_UNRESOLVED,
    append_review_signoff,
    main,
    resolve_review_signoff,
    signoffs_path,
    trw_dir_for_run,
)
from trw_mcp.tools._review_adversarial_source import (
    REASON_NOT_INDEPENDENT,
    adversarial_source_is_verified,
)
from trw_mcp.tools._review_reviewer_family import ReviewerFields

_REF = "rv-signoff-1"


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    directory = tmp_path / ".trw"
    directory.mkdir()
    return directory


class TestOperatorSignoffResolution:
    def test_valid_signoff_bound_to_this_review_verifies(self, trw_dir: Path) -> None:
        record = append_review_signoff(trw_dir, review_ref=_REF, approver="ops@example")

        resolution = resolve_review_signoff(trw_dir, record.approval_id, [_REF])

        assert resolution.verified is True
        assert resolution.reason == ""
        assert resolution.approver == "ops@example"

    def test_scope_digest_is_an_accepted_binding_reference(self, trw_dir: Path) -> None:
        """FR04 binds an approval to the review_id OR its content-binding digest."""
        digest = "a" * 64
        record = append_review_signoff(trw_dir, review_ref=digest, approver="ops@example")

        assert resolve_review_signoff(trw_dir, record.approval_id, [_REF, digest]).verified is True

    def test_self_minted_token_that_resolves_to_nothing_is_refused(self, trw_dir: Path) -> None:
        """The exact P0 payload: a caller-invented receipt id."""
        resolution = resolve_review_signoff(trw_dir, "anything", [_REF])

        assert resolution.verified is False
        assert resolution.reason == REASON_UNRESOLVED

    def test_empty_receipt_id_is_refused(self, trw_dir: Path) -> None:
        assert resolve_review_signoff(trw_dir, "   ", [_REF]).reason == REASON_UNRESOLVED

    def test_signoff_for_another_review_is_refused(self, trw_dir: Path) -> None:
        """One approval cannot satisfy a different review."""
        record = append_review_signoff(trw_dir, review_ref="rv-other", approver="ops@example")

        resolution = resolve_review_signoff(trw_dir, record.approval_id, [_REF])

        assert resolution.verified is False
        assert resolution.reason == REASON_SCOPE_MISMATCH

    def test_tampered_signature_is_refused(self, trw_dir: Path) -> None:
        record = append_review_signoff(trw_dir, review_ref=_REF, approver="ops@example")
        path = signoffs_path(trw_dir)
        entry = json.loads(path.read_text(encoding="utf-8").strip())
        entry["signature"] = ("0" if entry["signature"][0] != "0" else "1") + entry["signature"][1:]
        path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

        assert resolve_review_signoff(trw_dir, record.approval_id, [_REF]).reason == REASON_SIGNATURE_INVALID

    def test_tampered_review_ref_is_reported_as_tampering_not_mismatch(self, trw_dir: Path) -> None:
        """Rebinding a signed approval to another review breaks its signature.

        The signature covers ``review_ref``, so editing the journal to point a
        real approval at a different review is caught as forgery — it can never
        be laundered into an innocent-looking scope mismatch.
        """
        record = append_review_signoff(trw_dir, review_ref="rv-other", approver="ops@example")
        path = signoffs_path(trw_dir)
        entry = json.loads(path.read_text(encoding="utf-8").strip())
        entry["review_ref"] = _REF
        path.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

        assert resolve_review_signoff(trw_dir, record.approval_id, [_REF]).reason == REASON_SIGNATURE_INVALID

    def test_expired_signoff_is_refused(self, trw_dir: Path) -> None:
        issued = datetime.now(tz=timezone.utc) - timedelta(hours=30)
        record = append_review_signoff(trw_dir, review_ref=_REF, approver="ops@example", now=issued)

        assert resolve_review_signoff(trw_dir, record.approval_id, [_REF]).reason == REASON_EXPIRED

    def test_window_longer_than_configured_ttl_is_refused(self, trw_dir: Path) -> None:
        """A hand-written approval claiming a longer life than policy allows.

        ``review_signoff_ttl_hours`` is enforced at VERIFICATION, not only as a
        mint-time default, so editing ``expires_at`` cannot buy a longer window.
        """
        record = append_review_signoff(trw_dir, review_ref=_REF, approver="ops@example")
        approved = datetime.fromisoformat(record.approved_at)
        forged_expiry = (approved + timedelta(days=90)).isoformat()
        entry = {
            "approval_id": record.approval_id,
            "review_ref": _REF,
            "approver": "ops@example",
            "approved_at": record.approved_at,
            "expires_at": forged_expiry,
            "payload_version": "v1",
            "signature": review_signoffs.sign_review_signoff(
                approval_id=record.approval_id,
                review_ref=_REF,
                approver="ops@example",
                approved_at=record.approved_at,
                expires_at=forged_expiry,
            ),
        }
        signoffs_path(trw_dir).write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")

        assert resolve_review_signoff(trw_dir, record.approval_id, [_REF]).reason == REASON_TTL_EXCEEDED

    def test_unreadable_journal_refuses_with_its_own_reason(self, trw_dir: Path) -> None:
        """NFR01: a present-but-unreadable control file must never read as approval."""
        path = signoffs_path(trw_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()  # a directory where the journal belongs: readable stat, unreadable content

        resolution = resolve_review_signoff(trw_dir, "opsign-whatever", [_REF])

        assert resolution.verified is False
        assert resolution.reason == REASON_UNREADABLE

    def test_oversized_journal_refuses_rather_than_stalling(self, trw_dir: Path) -> None:
        path = signoffs_path(trw_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * (review_signoffs.MAX_SIGNOFF_FILE_BYTES + 1))

        assert resolve_review_signoff(trw_dir, "opsign-whatever", [_REF]).reason == REASON_UNREADABLE

    def test_unreadable_ttl_policy_refuses_instead_of_guessing_a_cap(
        self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = append_review_signoff(trw_dir, review_ref=_REF, approver="ops@example")

        def _boom() -> object:
            raise RuntimeError("config unreadable")

        monkeypatch.setattr("trw_mcp.models.config.get_config", _boom)

        assert resolve_review_signoff(trw_dir, record.approval_id, [_REF]).reason == REASON_POLICY_UNREADABLE

    def test_torn_journal_line_does_not_hide_a_valid_neighbour(self, trw_dir: Path) -> None:
        record = append_review_signoff(trw_dir, review_ref=_REF, approver="ops@example")
        path = signoffs_path(trw_dir)
        path.write_text("{not json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

        assert resolve_review_signoff(trw_dir, record.approval_id, [_REF]).verified is True

    def test_mint_rejects_an_empty_reference_or_approver(self, trw_dir: Path) -> None:
        with pytest.raises(ValueError):
            append_review_signoff(trw_dir, review_ref="  ", approver="ops@example")
        with pytest.raises(ValueError):
            append_review_signoff(trw_dir, review_ref=_REF, approver="  ")


class TestAdversarialSourcePredicate:
    """The ONE predicate both FR04 call sites share."""

    def test_operator_posture_requires_a_resolvable_signoff(self, trw_dir: Path) -> None:
        fields = ReviewerFields(origin="operator", identity="anything", family="human_or_self")

        verification = adversarial_source_is_verified(fields, review_refs=[_REF], trw_dir=trw_dir)

        assert verification.verified is False
        assert verification.reason == REASON_UNRESOLVED

    def test_operator_posture_with_a_bound_signoff_verifies(self, trw_dir: Path) -> None:
        record = append_review_signoff(trw_dir, review_ref=_REF, approver="ops@example")
        fields = ReviewerFields(origin="operator", identity=record.approval_id, family="human_or_self")

        assert adversarial_source_is_verified(fields, review_refs=[_REF], trw_dir=trw_dir).verified is True

    def test_digest_verified_cross_model_still_qualifies_without_any_signoff(self, trw_dir: Path) -> None:
        """Regression: the cross_model leg is untouched by the operator fix."""
        fields = ReviewerFields(
            origin="cross_model",
            identity="d" * 64,
            family="cross_model",
            external_receipt_digest="d" * 64,
        )

        assert adversarial_source_is_verified(fields, review_refs=[_REF], trw_dir=trw_dir).verified is True

    def test_self_posture_is_refused_with_a_named_reason(self, trw_dir: Path) -> None:
        fields = ReviewerFields(origin="self", identity="run-1", family="human_or_self")

        verification = adversarial_source_is_verified(fields, review_refs=[_REF], trw_dir=trw_dir)

        assert verification.verified is False
        assert verification.reason == REASON_NOT_INDEPENDENT


class TestMintCli:
    def test_approve_subcommand_writes_a_resolvable_signoff(
        self, trw_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The shipped out-of-band mint step, exercised end to end."""
        code = main(["approve", "--review-ref", _REF, "--approver", "ops@example", "--trw-dir", str(trw_dir)])
        approval_id = capsys.readouterr().out.strip()

        assert code == 0
        assert approval_id.startswith("opsign-")
        assert resolve_review_signoff(trw_dir, approval_id, [_REF]).verified is True

    def test_approve_refuses_an_empty_approver_without_writing(
        self, trw_dir: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["approve", "--review-ref", _REF, "--approver", " ", "--trw-dir", str(trw_dir)])

        assert code == 2
        assert "REFUSED" in capsys.readouterr().err
        assert not signoffs_path(trw_dir).exists()


class TestJournalLocation:
    def test_journal_is_derived_from_the_run_s_own_trw_tree(self, tmp_path: Path) -> None:
        """Mint and gate must read the SAME file regardless of ambient CWD."""
        run = tmp_path / ".trw" / "runs" / "task" / "20260904T000000Z-x"
        run.mkdir(parents=True)

        assert trw_dir_for_run(run) == tmp_path / ".trw"
