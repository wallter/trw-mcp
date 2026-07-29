"""Reconcile mode must not report a PRD it could not read as reconciled.

``handle_reconcile_mode`` skipped an unreadable PRD with only a ``logger.warning``
and then reported ``prd_count`` as the number of PRDs *requested*. Two missing
PRDs therefore came back ``verdict='clean'``, ``prd_count=2``, ``mismatch_count=0``
— a claim that two PRDs reconciled cleanly when neither had been opened. Same
silent-discard class as the review accept-list: input dropped, success reported,
nothing in the response naming what was lost.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._review_helpers_support import _make_config
from trw_mcp.tools._review_manual import handle_reconcile_mode


def _reconcile(prd_ids: list[str], run_path: Path | None = None) -> dict[str, object]:
    with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
        return dict(
            handle_reconcile_mode(
                _make_config(),  # type: ignore[arg-type]
                run_path,
                "review-reconcile",
                "2026-03-01T00:00:00Z",
                prd_ids,
            )
        )


class TestUnreadablePrdsAreReported:
    def test_missing_prds_are_named_in_the_response(self) -> None:
        result = _reconcile(["PRD-DOES-NOT-EXIST-001", "PRD-ALSO-MISSING-002"])
        assert result["prds_not_read"] == ["PRD-DOES-NOT-EXIST-001", "PRD-ALSO-MISSING-002"]
        assert result["prds_not_read_count"] == 2
        # The count of PRDs actually opened must not be inflated by the ones skipped.
        assert result["prds_read_count"] == 0

    def test_clean_verdict_cannot_be_read_as_verified_when_nothing_was_read(self) -> None:
        result = _reconcile(["PRD-DOES-NOT-EXIST-001"])
        # The verdict vocabulary is unchanged, but a consumer now has a
        # machine-readable reason not to treat 'clean' as coverage.
        assert result["verdict"] == "clean"
        assert result["reason"] == "no_prd_could_be_read_nothing_reconciled"

    def test_partial_read_reports_only_the_unreadable_one(self, tmp_path: Path) -> None:
        # A real PRD alongside a missing one: the readable one is reconciled,
        # the missing one is named rather than quietly folded into prd_count.
        config = _make_config()
        prds_dir = tmp_path / config.prds_relative_path
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-REAL-001.md").write_text(
            "## Functional Requirements\n\nFR1: adds `some_identifier` to the module\n",
            encoding="utf-8",
        )
        with (
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = dict(
                handle_reconcile_mode(
                    config,
                    None,
                    "review-reconcile",
                    "2026-03-01T00:00:00Z",
                    ["PRD-REAL-001", "PRD-MISSING-002"],
                )
            )
        assert result["prds_read_count"] == 1
        assert result["prds_not_read"] == ["PRD-MISSING-002"]
        # Something was genuinely reconciled, so the all-unread reason is absent.
        assert "reason" not in result

    def test_no_advisory_keys_when_every_prd_was_read(self, tmp_path: Path) -> None:
        config = _make_config()
        prds_dir = tmp_path / config.prds_relative_path
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-REAL-001.md").write_text(
            "## Functional Requirements\n\nFR1: adds `some_identifier`\n",
            encoding="utf-8",
        )
        with (
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""),
        ):
            result = dict(handle_reconcile_mode(config, None, "r", "2026-03-01T00:00:00Z", ["PRD-REAL-001"]))
        assert result["prds_read_count"] == 1
        assert "prds_not_read" not in result
        assert "prds_not_read_count" not in result
