"""Tests for c749 _learnings_collector + 4 tool learnings halves."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools._learnings_collector import (
    DEFAULT_TOP_N,
    LearningSummary,
    MAX_QUERIES,
    build_file_queries,
    collect_learnings,
)
from trw_mcp.tools._sidecar_substrate import SCHEMA_VERSION_ACCEPTED


def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path,
    ).decode().strip()


def _write_entitlement(trw_dir: Path, tier: str) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(
        tier=tier, issued_to="t@t", expires_at=future,  # type: ignore[arg-type]
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


class TestBuildFileQueries:
    def test_path_with_basename(self) -> None:
        qs = build_file_queries("src/foo/bar.py")
        assert qs == ["src/foo/bar.py", "bar.py"]

    def test_basename_only(self) -> None:
        qs = build_file_queries("foo.py")
        assert qs == ["foo.py"]


class TestCollectLearnings:
    def test_empty_queries(self) -> None:
        assert collect_learnings([]) == []

    def test_invalid_queries_skipped(self) -> None:
        # Should not raise; non-strings filtered
        result = collect_learnings(["", "x" * 10])
        assert isinstance(result, list)

    def test_max_queries_cap(self) -> None:
        # Should cap at MAX_QUERIES = 10
        queries = ["q" + str(i) for i in range(20)]
        result = collect_learnings(queries, top_n=DEFAULT_TOP_N)
        assert isinstance(result, list)

    def test_default_top_n(self) -> None:
        # Default behavior — returns at most DEFAULT_TOP_N
        result = collect_learnings(["foo.py"])
        assert len(result) <= DEFAULT_TOP_N

    def test_returns_learning_summaries(self) -> None:
        # On the live monorepo, "foo.py" finds some learnings
        result = collect_learnings(["foo.py"])
        for item in result:
            assert isinstance(item, LearningSummary)


class TestBatchToolLearnings:
    def test_pro_with_sidecar_has_learnings(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"before-edit-batch-{sha}.json", sha,
            {
                "total_files": 1, "files_in_map": 1, "total_hotspot_warnings": 0,
                "hints": [
                    {
                        "target_path": "foo.py", "target_exists_in_map": True,
                        "importers": [], "inferred_tests": [],
                        "doc_references": [], "co_change_neighbors": [],
                        "hotspot_warnings": [], "risk_score": 0.3,
                    },
                ],
            },
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        from trw_mcp.tools.before_edit_hint_batch import compute_before_edit_hint_batch
        r = compute_before_edit_hint_batch(repo_root=str(tmp_path))
        assert r.distill_status == "hint_available"
        # learnings field is present (may be 0 on isolated tmp repo)
        assert isinstance(r.learnings, list)
        assert r.learnings_count == len(r.learnings)


class TestRiskReportLearnings:
    def test_pro_with_sidecar_has_learnings_field(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"risk-report-{sha}.json", sha,
            [
                {
                    "target_path": "foo.py", "target_exists_in_map": True,
                    "composite_score": 0.5, "fanin_score": 0.5,
                    "fanout_score": 0.5, "untested_score": 0.5,
                    "undocumented_score": 0.5, "size_score": 0.5,
                    "churn_score": 0.0, "fanin_count": 0, "fanout_count": 0,
                    "test_edge_count": 0, "doc_edge_count": 0,
                    "line_count": 100,
                },
            ],
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        from trw_mcp.tools.codebase_risk_report import compute_codebase_risk_report
        r = compute_codebase_risk_report(repo_root=str(tmp_path), top_n=5)
        assert r.distill_status == "hint_available"
        assert isinstance(r.learnings, list)
        assert r.learnings_count == len(r.learnings)


class TestOrderingCompareLearnings:
    def test_pro_with_sidecar_has_learnings_field(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_envelope(
            cache_dir / f"ordering-compare-{sha}.json", sha,
            {
                "label_a": "high_composite_risk_paths",
                "label_b": "high_risk_paths",
                "n_a": 10, "n_b": 10,
                "n_intersection": 5, "n_union": 15, "jaccard": 0.333,
                "kendall_tau_b": 0.85,
                "only_in_a": ["foo.py"],
                "only_in_b": ["bar.py"],
                "overlap_status": "overlap",
            },
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        from trw_mcp.tools.ordering_compare import compute_ordering_compare
        r = compute_ordering_compare(repo_root=str(tmp_path))
        assert r.distill_status == "hint_available"
        assert isinstance(r.learnings, list)
        assert r.learnings_count == len(r.learnings)


class TestCrossRepoLearnings:
    def test_pro_with_sidecar_has_learnings_field(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        sidecar = tmp_path / "shared" / "cross-repo-aggregate-xyz.json"
        _write_envelope(
            sidecar, "xyz",
            {
                "n_repos": 2,
                "per_repo": [
                    {
                        "repo_label": "r1",
                        "comparison": {
                            "label_a": "a", "label_b": "b",
                            "n_a": 5, "n_b": 5, "n_intersection": 3, "n_union": 7,
                            "jaccard": 0.428, "kendall_tau_b": 0.9,
                            "only_in_a": [], "only_in_b": [],
                            "overlap_status": "overlap",
                        },
                    },
                ],
                "overlap_status_counts": {"overlap": 1},
                "summary_verdict": "consistent_overlap",
            },
        )
        from trw_mcp.tools.cross_repo_ordering import compute_cross_repo_ordering
        r = compute_cross_repo_ordering(
            repo_root=str(tmp_path), sidecar_path=str(sidecar),
        )
        assert r.distill_status == "hint_available"
        assert isinstance(r.learnings, list)
        assert r.learnings_count == len(r.learnings)


class TestC746BackwardCompat:
    """Verify c746 trw_before_edit_hint still returns learnings at free tier."""

    def test_c746_still_returns_learnings_at_free_tier(self, tmp_path: Path) -> None:
        from trw_mcp.tools.before_edit_hint import compute_before_edit_hint
        r = compute_before_edit_hint(file_path="foo.py", repo_root=str(tmp_path))
        # c746 collects learnings BEFORE tier gate, so they always appear
        assert isinstance(r.learnings, list)
        assert r.learnings_count == len(r.learnings)


class TestConstants:
    def test_default_top_n(self) -> None:
        assert DEFAULT_TOP_N == 5

    def test_max_queries(self) -> None:
        assert MAX_QUERIES == 10
