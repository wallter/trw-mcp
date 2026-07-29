"""Cross-model review must report the critical count its verdict was built from.

The delivery gate does not act on the verdict alone. ``_delivery_review_gate``
blocks only on ``verdict == "block" AND critical_count > 0``, reading both keys
off ``review.yaml``; ``tools/review.py`` likewise derives the ceremony
``p0_count`` from ``response.get("critical_count", 0)``.

``handle_cross_model_mode`` never emitted ``critical_count``. Both consumers
defaulted it to 0, so a substantive cross-model review that found real critical
findings and returned ``verdict='block'``:

  * did NOT block delivery (measured: ``_check_review_gate`` returned
    ``block=None`` for that exact artifact, while the byte-identical artifact
    with ``critical_count: 2`` blocked), and
  * recorded ``review_p0_count=0``, which suppresses the "P0 findings detected.
    A separate agent MUST remediate" nudge in favour of "NEXT: trw_deliver()".

These tests pin the count on both coverage paths, pin the
``critical_count > 0 iff verdict == 'block'`` invariant that keeps the two from
drifting apart, and pin the end-to-end delivery block that the missing key
disabled.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._review_helpers_support import _make_config
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_cross_model import handle_cross_model_mode

from ._review_helpers_support import run_dir  # noqa: F401

_HELPERS = "trw_mcp.tools._review_helpers"

_TWO_CRITICALS = [
    {"category": "security", "severity": "critical", "description": "auth bypass in login handler"},
    {"category": "security", "severity": "P0", "description": "token leaked into the response body"},
]


def _cross_family(run_path: Path, findings: list[dict[str, str]]) -> dict[str, object]:
    """Run the mode with a reachable provider that returns *findings*."""
    config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
    with (
        patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
        patch(f"{_HELPERS}._invoke_cross_model_review", return_value=findings),
    ):
        return dict(handle_cross_model_mode(config, run_path, "rev-cc", "2026-03-01T00:00:00Z"))


class TestCriticalCountIsReported:
    def test_blocking_cross_family_review_reports_its_critical_count(self, run_dir: Path) -> None:
        result = _cross_family(run_dir, _TWO_CRITICALS)

        assert result["verdict"] == "block"
        # Both P0-alias severities normalize to critical and must be counted.
        assert result["critical_count"] == 2

    def test_blocking_same_family_fallback_reports_its_critical_count(self, run_dir: Path) -> None:
        """The degraded path computes its verdict from fallback findings — count those."""
        config = _make_config(cross_model_enabled=False)
        fallback = {
            "reviewer_roles_run": ["security"],
            "reviewer_errors": [],
            "findings": _TWO_CRITICALS,
            "auto_analysis_limited": False,
            "limited_reason": "",
        }
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._run_multi_reviewer_analysis", return_value=fallback),
        ):
            result = dict(handle_cross_model_mode(config, run_dir, "rev-fb-cc", "2026-03-01T00:00:00Z"))

        assert result["review_family_coverage"] == "single_family"
        assert result["verdict"] == "block"
        assert result["critical_count"] == 2

    @pytest.mark.parametrize(
        ("findings", "expected_verdict", "expected_critical"),
        [
            ([{"category": "c", "severity": "critical", "description": "x"}], "block", 1),
            ([{"category": "c", "severity": "warning", "description": "x"}], "warn", 0),
            ([{"category": "c", "severity": "info", "description": "x"}], "pass", 0),
            (
                [
                    {"category": "c", "severity": "critical", "description": "x"},
                    {"category": "c", "severity": "warning", "description": "y"},
                ],
                "block",
                1,
            ),
        ],
    )
    def test_critical_count_is_nonzero_exactly_when_verdict_blocks(
        self,
        run_dir: Path,
        findings: list[dict[str, str]],
        expected_verdict: str,
        expected_critical: int,
    ) -> None:
        """The count and the verdict come from one list, so they cannot disagree.

        A count that drifted below the verdict would silently reopen the gate
        again, since the gate needs BOTH signals to block.
        """
        result = _cross_family(run_dir, findings)

        assert result["verdict"] == expected_verdict
        assert result["critical_count"] == expected_critical
        assert (result["critical_count"] > 0) is (result["verdict"] == "block")

    def test_critical_count_reaches_the_persisted_artifact(self, run_dir: Path) -> None:
        """review.yaml is what the delivery gate reads — the response alone is not enough."""
        _cross_family(run_dir, _TWO_CRITICALS)

        persisted = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert persisted["verdict"] == "block"
        assert persisted["critical_count"] == 2


def _observe_mode_artifact(run_dir: Path, *, keep_count: bool) -> FileStateReader:
    """Persist a substantive blocking artifact with no typed receipt beside it.

    Reproduces the legacy-projection case the OBSERVE branch of
    ``_check_review_gate`` exists to serve: ``evidence_receipt_mode=observe``
    plus a receipt that could not be written (unverifiable scope, no run pin),
    so the gate falls back to reading ``verdict`` + ``critical_count`` out of
    ``review.yaml``. That fallback is the branch the missing key disabled.
    """
    import shutil

    from trw_mcp.state.persistence import FileStateWriter

    _cross_family(run_dir, _TWO_CRITICALS)
    shutil.rmtree(run_dir / "meta" / "receipts", ignore_errors=True)
    reader = FileStateReader()
    review_path = run_dir / "meta" / "review.yaml"
    data = reader.read_yaml(review_path)
    # The handler already derived substantive=True from realized cross-family
    # findings; the receipt writer is what clears it, and it is not under test.
    data["substantive"] = True
    if not keep_count:
        del data["critical_count"]
    FileStateWriter().write_yaml(review_path, data)
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: helpers-test\nstatus: active\nphase: review\ncomplexity_class: STANDARD\n",
        encoding="utf-8",
    )
    return reader


class TestBlockingCrossModelReviewBlocksDelivery:
    """End-to-end on the legacy-projection branch, where the count is the gate."""

    def test_persisted_blocking_review_blocks_the_delivery_gate(
        self,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from trw_mcp.tools._delivery_helpers import _check_review_gate

        reader = _observe_mode_artifact(run_dir, keep_count=True)
        monkeypatch.setattr(
            "trw_mcp.tools._delivery_helpers.get_config",
            lambda: TRWConfig(review_gate_mode="block", evidence_receipt_mode="observe"),
        )

        block, _warning, _advisory = _check_review_gate(run_dir, reader)

        assert block is not None
        assert "2 critical finding(s)" in block

    def test_gate_stays_open_without_the_count(
        self,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-vacuity control: the same artifact minus critical_count does NOT block.

        This is the measured pre-fix behaviour, kept so the test above cannot go
        green for an unrelated reason. If this one ever starts blocking too, the
        assertion above stops proving that the count is what closed the gate.
        """
        from trw_mcp.tools._delivery_helpers import _check_review_gate

        reader = _observe_mode_artifact(run_dir, keep_count=False)
        monkeypatch.setattr(
            "trw_mcp.tools._delivery_helpers.get_config",
            lambda: TRWConfig(review_gate_mode="block", evidence_receipt_mode="observe"),
        )

        block, _warning, _advisory = _check_review_gate(run_dir, reader)

        assert block is None


class TestCeremonyP0CountIsRecorded:
    """``tools/review.py`` derives the ceremony P0 count from this key.

    That path is unconditional — it does not consult the typed receipt — so a
    missing ``critical_count`` recorded ``review_p0_count=0`` for every blocking
    cross-model review, and the nudge engine then emitted
    "NEXT: trw_deliver()" instead of "P0 findings detected. A separate agent MUST
    remediate".
    """

    def test_cross_model_block_records_its_p0_count(
        self,
        tmp_path: Path,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tests._ceremony_helpers import make_ceremony_server
        from trw_mcp.models.config import _reset_config

        tools = make_ceremony_server(monkeypatch, tmp_path)
        _reset_config(TRWConfig(cross_model_review_enabled=True, cross_model_provider="gpt-4o"))
        recorded: dict[str, object] = {}

        def _capture(_trw_dir: Path, verdict: str, p0_count: int = 0, *, substantive: bool = True) -> None:
            recorded.update(verdict=verdict, p0_count=p0_count, substantive=substantive)

        monkeypatch.setattr("trw_mcp.state.ceremony_progress.mark_review", _capture)
        with (
            patch("trw_mcp.tools.review.find_active_run", return_value=run_dir),
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=_TWO_CRITICALS),
        ):
            result = tools["trw_review"].fn(mode="cross_model")

        assert result["verdict"] == "block"
        assert recorded["verdict"] == "block"
        assert recorded["p0_count"] == 2
