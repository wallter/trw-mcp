"""Tier CLI beta/tester-program coverage (sub_Y-f6QQ3Y_Os9b0vM).

Complements ``test_c748_mcp_tools.py`` with the beta-tier issue + status
paths added when the tester program was bridged into the entitlement model.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from trw_mcp.server._subcommands_tier import (
    _format_tier_status_table,
    _tier_status_rows,
    run_tier,
)
from trw_mcp.state._entitlements import Entitlement, load_entitlement


class TestTierIssueBeta:
    def test_issue_beta_writes_resolvable_entitlement(self, tmp_path: Path, capsys) -> None:
        trw_dir = tmp_path / ".trw"
        args = argparse.Namespace(
            tier_command="issue",
            tier="beta",
            issued_to="tester@trw",
            expires="2030-01-01",
            trw_dir=str(trw_dir),
            print_only=False,
        )
        run_tier(args)
        capsys.readouterr()

        path = trw_dir / "entitlements.yaml"
        assert path.exists()
        assert "tier: beta" in path.read_text()

        # Round-trip: the issued file resolves to a beta entitlement that
        # unlocks the distill sidecar (the whole point of the tester bridge).
        e = load_entitlement(trw_dir)
        assert e.tier == "beta"
        assert e.reason == "ok"
        assert e.has_feature("trw_before_edit_hint:distill_sidecar")

    def test_issue_beta_print_only(self, tmp_path: Path, capsys) -> None:
        args = argparse.Namespace(
            tier_command="issue",
            tier="beta",
            issued_to="tester@trw",
            expires="2030-01-01",
            trw_dir=str(tmp_path / ".trw"),
            print_only=True,
        )
        run_tier(args)
        out = capsys.readouterr().out
        assert "tier: beta" in out
        assert "signature:" in out
        assert not (tmp_path / ".trw" / "entitlements.yaml").exists()


class TestTierStatusBetaRows:
    def test_beta_status_rows_render_without_keyerror(self) -> None:
        e = Entitlement(tier="beta", reason="ok", expires_at_iso="2030-01-01T00:00:00+00:00")
        rows = _tier_status_rows(e)
        # active row: (state, tier, limit, expires)
        active = rows[0]
        assert active[0] == "active"
        assert active[1] == "beta"
        assert active[2] == "beta (tester)"

    def test_status_table_includes_beta(self) -> None:
        e = Entitlement(tier="beta", reason="ok", expires_at_iso="2030-01-01T00:00:00+00:00")
        table = _format_tier_status_table(e)
        assert "beta" in table
        assert "beta (tester)" in table

    def test_show_beta_lists_distill_feature(self, tmp_path: Path, capsys) -> None:
        # Issue a beta entitlement, then `tier show` should list the feature.
        trw_dir = tmp_path / ".trw"
        run_tier(
            argparse.Namespace(
                tier_command="issue",
                tier="beta",
                issued_to="tester@trw",
                expires="2030-01-01",
                trw_dir=str(trw_dir),
                print_only=False,
            )
        )
        capsys.readouterr()
        run_tier(argparse.Namespace(tier_command="show", trw_dir=str(trw_dir)))
        out = capsys.readouterr().out
        assert "tier:      beta" in out
        assert "trw_before_edit_hint:distill_sidecar" in out
