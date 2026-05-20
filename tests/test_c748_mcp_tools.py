"""Tests for c748 ordering-compare + cross-repo-ordering tools + tier CLI."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.server._subcommands_tier import run_tier
from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools._sidecar_substrate import SCHEMA_VERSION_ACCEPTED
from trw_mcp.tools.cross_repo_ordering import (
    CrossRepoOrderingResult,
    compute_cross_repo_ordering,
)
from trw_mcp.tools.ordering_compare import (
    OrderingCompareResult,
    RiskOrderingComparisonPayload,
    compute_ordering_compare,
)


def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
        )
        .decode()
        .strip()
    )


def _write_entitlement(trw_dir: Path, tier: str) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(
        tier=tier,
        issued_to="t@t",
        expires_at=future,  # type: ignore[arg-type]
    )
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: {tier}\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n",
    )


def _write_envelope(path: Path, sha: str, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": SCHEMA_VERSION_ACCEPTED,
        "sha": sha,
        "generated_at_unix": 1714000000.0,
        "payload": payload,
    }
    path.write_text(json.dumps(envelope))


def _valid_comparison_payload() -> dict:
    return {
        "label_a": "high_composite_risk_paths",
        "label_b": "high_risk_paths",
        "n_a": 10,
        "n_b": 10,
        "n_intersection": 5,
        "n_union": 15,
        "jaccard": 0.333,
        "kendall_tau_b": 0.85,
        "only_in_a": ["a.py", "b.py"],
        "only_in_b": ["c.py", "d.py"],
        "overlap_status": "overlap",
    }


class TestOrderingCompareTool:
    def test_free_tier_blocked(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        r = compute_ordering_compare(repo_root=str(tmp_path))
        assert r.tier == "free"
        assert r.distill_status == "tier_required"

    def test_pro_no_sidecar(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_ordering_compare(repo_root=str(tmp_path))
        assert r.distill_status == "sidecar_missing"
        assert "risk-ordering-compare" in (r.distill_action or "")

    def test_pro_happy(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"ordering-compare-{sha}.json",
            sha,
            _valid_comparison_payload(),
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_ordering_compare(repo_root=str(tmp_path))
        assert r.distill_status == "hint_available"
        assert r.comparison is not None
        assert r.comparison.jaccard == 0.333
        assert r.comparison.overlap_status == "overlap"

    def test_pro_malformed(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"ordering-compare-{sha}.json",
            sha,
            {"unexpected": "boom"},
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_ordering_compare(repo_root=str(tmp_path))
        assert r.distill_status == "sidecar_malformed"


class TestCrossRepoOrderingTool:
    def test_free_tier_blocked(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        sidecar_dir = tmp_path / "shared"
        sidecar_dir.mkdir()
        _write_envelope(
            sidecar_dir / "cross-repo-aggregate-abc.json",
            "abc",
            {"n_repos": 0, "per_repo": [], "overlap_status_counts": {}, "summary_verdict": "insufficient"},
        )
        r = compute_cross_repo_ordering(
            repo_root=str(tmp_path),
            sidecar_dir=str(sidecar_dir),
        )
        assert r.tier == "free"
        assert r.distill_status == "tier_required"

    def test_pro_no_sidecar(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_cross_repo_ordering(repo_root=str(tmp_path))
        assert r.distill_status == "sidecar_missing"

    def test_pro_happy_explicit_path(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        sidecar = tmp_path / "shared" / "cross-repo-aggregate-abc123.json"
        _write_envelope(
            sidecar,
            "abc123",
            {
                "n_repos": 2,
                "per_repo": [
                    {
                        "repo_label": "r1",
                        "comparison": _valid_comparison_payload(),
                    },
                ],
                "mean_jaccard": 0.5,
                "median_jaccard": 0.5,
                "stdev_jaccard": 0.1,
                "n_tau_defined": 1,
                "overlap_status_counts": {"overlap": 1},
                "summary_verdict": "consistent_overlap",
            },
        )
        r = compute_cross_repo_ordering(
            repo_root=str(tmp_path),
            sidecar_path=str(sidecar),
        )
        assert r.distill_status == "hint_available"
        assert r.aggregate is not None
        assert r.aggregate.n_repos == 2
        assert r.aggregate.summary_verdict == "consistent_overlap"

    def test_pro_happy_latest_in_dir(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        sidecar_dir = tmp_path / ".trw" / "distill" / "map-cache"
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        # Two sidecars; tool picks the latest by mtime
        for tag in ("aaa", "bbb"):
            _write_envelope(
                sidecar_dir / f"cross-repo-aggregate-{tag}.json",
                tag,
                {
                    "n_repos": 1,
                    "per_repo": [],
                    "overlap_status_counts": {},
                    "summary_verdict": "insufficient",
                },
            )
        r = compute_cross_repo_ordering(repo_root=str(tmp_path))
        assert r.distill_status == "hint_available"

    def test_no_repo_no_sidecar_args(self, tmp_path: Path, monkeypatch) -> None:
        # No repo, no sidecar_path, no sidecar_dir → sidecar_path_required
        # Need to avoid the test runner's git directory; use explicit None args
        # and a non-git tmp_path.
        monkeypatch.chdir(tmp_path)
        r = compute_cross_repo_ordering()
        # May resolve git via parent search; not asserting exact status
        # because the test runner is itself in a git repo.
        assert r.distill_status in (
            "sidecar_path_required",
            "tier_required",
            "sidecar_missing",
        )


class TestTierIssueCLI:
    def test_print_only(self, tmp_path: Path, capsys) -> None:
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

    def test_writes_file(self, tmp_path: Path, capsys) -> None:
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

    def test_iso_datetime_accepted(self, tmp_path: Path, capsys) -> None:
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

    def test_show_missing(self, tmp_path: Path, capsys) -> None:
        args = argparse.Namespace(
            tier_command="show",
            trw_dir=str(tmp_path / ".trw"),
        )
        run_tier(args)
        captured = capsys.readouterr()
        assert "tier:      free" in captured.out
        assert "reason:    missing" in captured.out

    def test_show_pro(self, tmp_path: Path, capsys) -> None:
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
        assert "trw_before_edit_hint:distill_sidecar" in captured.out


class TestModelContracts:
    def test_ordering_compare_frozen(self) -> None:
        r = OrderingCompareResult(tier="free")
        with pytest.raises(Exception):
            r.tier = "pro"  # type: ignore[misc]

    def test_cross_repo_frozen(self) -> None:
        r = CrossRepoOrderingResult(tier="free")
        with pytest.raises(Exception):
            r.tier = "pro"  # type: ignore[misc]

    def test_comparison_extra_forbid(self) -> None:
        with pytest.raises(Exception):
            RiskOrderingComparisonPayload(  # type: ignore[call-arg]
                label_a="a",
                label_b="b",
                n_a=0,
                n_b=0,
                n_intersection=0,
                n_union=0,
                jaccard=0.0,
                overlap_status="overlap",
                unknown_field="boom",
            )
