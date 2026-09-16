"""PRD-CORE-205 FR03 wiring — typed-present-invalid review receipt blocks legacy rescue."""

from __future__ import annotations

from pathlib import Path

import yaml

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_review_gate import _check_review_gate


def _write_legacy_substantive_review(run: Path) -> None:
    meta = run / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    # A real run HAS a run.yaml. Without one the gate cannot establish the run's
    # complexity and now says so in a warning rather than silently taking the
    # lenient path — correct behaviour, but not what these tests are about, and a
    # fixture that omits it was testing the broken-run case by accident.
    (meta / "run.yaml").write_text(yaml.safe_dump({"complexity_class": "MINIMAL"}), encoding="utf-8")
    (meta / "review.yaml").write_text(
        yaml.safe_dump({"substantive": True, "verdict": "pass", "critical_count": 0}),
        encoding="utf-8",
    )


def _write_malformed_typed_receipt(run: Path) -> None:
    rdir = run / "meta" / "receipts" / "review"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "review-corrupt.json").write_text("{ this is not valid json", encoding="utf-8")


class TestTypedPresentInvalidBlocksLegacyRescue:
    def test_legacy_substantive_cannot_replace_typed_receipt(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _write_legacy_substantive_review(run)
        block, warning, advisory = _check_review_gate(run, FileStateReader())
        # Legacy prose remains telemetry and cannot fabricate typed review evidence.
        assert block is None and warning is None
        assert advisory is not None and "No substantive trw_review" in advisory

    def test_typed_present_invalid_forces_review_absent(self, tmp_path: Path) -> None:
        run = tmp_path / "run"
        _write_legacy_substantive_review(run)
        _write_malformed_typed_receipt(run)
        block, warning, advisory = _check_review_gate(run, FileStateReader())
        # The malformed typed receipt blocks the legacy positive from rescuing it;
        # the run is now treated as having NO substantive review, so the missing-
        # review policy fires (some non-None channel).
        assert any(msg is not None for msg in (block, warning, advisory))


class TestUnreadableComplexityIsNotSilentLeniency:
    """A gate that could not classify the run must say so, not just relax.

    ``_read_complexity_class`` returned ``""`` for BOTH "this run carries no
    complexity_class" and "run.yaml could not be read", and every decision point
    tests membership in ``("STANDARD", "COMPREHENSIVE")`` — which ``""`` can
    never satisfy. So an unreadable run.yaml silently took the lenient branch at
    all three: a ``block`` verdict with critical findings became a warning, and a
    missing review became an advisory even under ``review_gate_mode=block``.

    That is sampling bias, not a fall-through. A run whose own metadata cannot be
    read is not a random run — it is a broken one, and broken runs are the
    population a delivery gate exists to catch. The fail-open comment said the
    read "must not block delivery"; the effect was to UNBLOCK a delivery a review
    had explicitly blocked.

    Reported with a measured downgrade table by a cross-family sweep 2026-09-12.
    """

    @staticmethod
    def _run_without_metadata(tmp_path: Path, *, malformed: bool) -> Path:
        run = tmp_path / "run"
        (run / "meta").mkdir(parents=True, exist_ok=True)
        if malformed:
            (run / "meta" / "run.yaml").write_text("{ this: is: not: yaml", encoding="utf-8")
        return run

    def test_a_malformed_run_yaml_surfaces_the_reason(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_review_gate import COMPLEXITY_UNREADABLE_REASON

        run = self._run_without_metadata(tmp_path, malformed=True)
        block, warning, advisory = _check_review_gate(run, FileStateReader())

        # Asserting the REASON, not the field. The outcome has three fields and
        # several distinct causes collapse into each, so "is it still advisory?"
        # cannot discriminate this case from any other.
        assert warning is not None and COMPLEXITY_UNREADABLE_REASON in warning
        assert advisory is None
        assert block is None, "an unreadable run.yaml must not BLOCK either — that inverts the risk"

    def test_an_absent_run_yaml_surfaces_the_same_reason(self, tmp_path: Path) -> None:
        from trw_mcp.tools._delivery_review_gate import COMPLEXITY_UNREADABLE_REASON

        run = self._run_without_metadata(tmp_path, malformed=False)
        _block, warning, _advisory = _check_review_gate(run, FileStateReader())
        assert warning is not None and COMPLEXITY_UNREADABLE_REASON in warning

    def test_a_readable_minimal_run_keeps_the_quiet_advisory(self, tmp_path: Path) -> None:
        """Non-vacuity partner. A genuinely MINIMAL run is classified and its
        leniency is EARNED, so it must stay an advisory — otherwise the fix would
        have promoted every small run to a warning and the signal would be noise.
        """
        from trw_mcp.tools._delivery_review_gate import COMPLEXITY_UNREADABLE_REASON

        run = tmp_path / "run"
        (run / "meta").mkdir(parents=True, exist_ok=True)
        (run / "meta" / "run.yaml").write_text(yaml.safe_dump({"complexity_class": "MINIMAL"}), encoding="utf-8")

        _block, warning, advisory = _check_review_gate(run, FileStateReader())
        assert advisory is not None and "No substantive trw_review" in advisory
        assert warning is None or COMPLEXITY_UNREADABLE_REASON not in warning
