"""Tests for the ``trw-mcp tier`` entitlement-issuance CLI.

Extracted from ``test_c748_mcp_tools.py`` (PRD-CORE-300-FR06 slice S4): that
file's ordering-compare and cross-repo-ordering tests were deleted along with
the tools they covered, but ``TestTierIssueCLI`` is unrelated to either — it
covers ``trw-mcp tier issue`` / ``tier show`` — so it moved here rather than
being deleted with them.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.server._subcommands_tier import run_tier


class TestTierIssueCLI:
    def test_print_only(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(
            tier_command="issue",
            tier="pro",
            issued_to="x@y",
            expires="2027-01-01",
            trw_dir=str(tmp_path / ".trw"),
            print_only=True,
        )
        run_tier(args)
        captured = capsys.readouterr()
        assert "tier: pro" in captured.out
        assert "signature:" in captured.out

    def test_writes_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(
            tier_command="issue",
            tier="enterprise",
            issued_to="x@y",
            expires="2027-01-01",
            trw_dir=str(tmp_path / ".trw"),
            print_only=False,
        )
        run_tier(args)
        path = tmp_path / ".trw" / "entitlements.yaml"
        assert path.exists()
        text = path.read_text()
        assert "tier: enterprise" in text

    def test_iso_datetime_accepted(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(
            tier_command="issue",
            tier="team",
            issued_to="x@y",
            expires="2027-06-15T12:00:00+00:00",
            trw_dir=str(tmp_path / ".trw"),
            print_only=True,
        )
        run_tier(args)
        captured = capsys.readouterr()
        assert "expires_at: '2027-06-15T12:00:00+00:00'" in captured.out

    def test_invalid_expires(self, tmp_path: Path) -> None:
        args = argparse.Namespace(
            tier_command="issue",
            tier="pro",
            issued_to="x@y",
            expires="not-a-date",
            trw_dir=str(tmp_path / ".trw"),
            print_only=True,
        )
        with pytest.raises(SystemExit):
            run_tier(args)

    def test_show_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(
            tier_command="show",
            trw_dir=str(tmp_path / ".trw"),
        )
        run_tier(args)
        captured = capsys.readouterr()
        assert "tier:      free" in captured.out
        assert "reason:    missing" in captured.out

    def test_show_pro(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Issue then show
        args_issue = argparse.Namespace(
            tier_command="issue",
            tier="pro",
            issued_to="x@y",
            expires="2027-01-01",
            trw_dir=str(tmp_path / ".trw"),
            print_only=False,
        )
        run_tier(args_issue)
        capsys.readouterr()  # drain
        args_show = argparse.Namespace(
            tier_command="show",
            trw_dir=str(tmp_path / ".trw"),
        )
        run_tier(args_show)
        captured = capsys.readouterr()
        assert "tier:      pro" in captured.out
        assert "trw_code:distill_sidecar" in captured.out
