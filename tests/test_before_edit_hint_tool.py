"""Tests for trw_before_edit_hint MCP tool (PRD-DIST-1983..1986, cycle 746)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools.before_edit_hint import (
    _SCHEMA_VERSION_ACCEPTED,
    BeforeEditHintResult,
    BeforeYouEditHintPayload,
    LearningSummary,
    _select_distill_hint,
    compute_before_edit_hint,
)


def _make_git_repo(repo_path: Path) -> str:
    """Init minimal git repo; return HEAD SHA."""
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_path, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_path, check=True)
    sha = (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
        )
        .decode()
        .strip()
    )
    return sha


def _write_entitlement(trw_dir: Path, tier: str) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier=tier, issued_to="t@t", expires_at=future)  # type: ignore[arg-type]
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: {tier}\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n",
    )


def _write_sidecar(
    cache_dir: Path,
    sha: str,
    target_path: str,
    *,
    schema_version: str = _SCHEMA_VERSION_ACCEPTED,
    hint_overrides: dict | None = None,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "target_path": target_path,
        "target_exists_in_map": True,
        "importers": ["bar.py", "baz.py"],
        "inferred_tests": ["tests/test_foo.py"],
        "doc_references": [],
        "co_change_neighbors": [],
        "hotspot_warnings": ["non-trivial fan-in (2 importers)"],
        "risk_score": 0.42,
    }
    if hint_overrides:
        payload.update(hint_overrides)
    envelope = {
        "schema_version": schema_version,
        "sha": sha,
        "generated_at_unix": 1714000000.0,
        "payload": payload,
    }
    path = cache_dir / f"before-edit-hint-{sha}.json"
    path.write_text(json.dumps(envelope, indent=2))
    return path


class TestFreeTier:
    def test_no_entitlement_and_no_distill_is_quiet(self, tmp_path: Path) -> None:
        # trw-distill NOT installed (pinned absent by the conftest autouse
        # fixture) + no entitlement sentinel: the sidecar feature is unavailable,
        # but the tool must stay QUIET — no paid-tier remediation nag (it would
        # burn caller tokens on every edit).
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, "foo.py")  # exists but not consumable
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.tier == "free"
        assert r.distill_status == "tier_required"
        assert r.distill_hint is None
        assert r.distill_action is None  # no nag when the feature is unavailable

    def test_installed_distill_unlocks_without_a_sentinel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The 2026-07-19 fix: trw-distill installed => entitled, even with no
        # .trw/entitlements.yaml (the installer never wrote one). The gate opens
        # and the tool consumes the sidecar instead of nagging about tiers.
        monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, "foo.py")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.tier == "proprietary"
        assert r.distill_status != "tier_required"

    def test_free_tier_still_returns_learnings(self, tmp_path: Path) -> None:
        # Learnings half is always available — operator gets value at free tier.
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        # Live trw_recall against the repo's memory may return entries
        # for "foo.py" — we assert the type rather than the count to
        # stay robust to local memory state.
        assert isinstance(r.learnings, list)
        assert r.learnings_count == len(r.learnings)


class TestPaidTierHappyPath:
    def test_pro_with_sidecar(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, "foo.py")
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.tier == "pro"
        assert r.distill_status == "hint_available"
        assert r.distill_hint is not None
        assert r.distill_hint.target_path == "foo.py"
        assert r.distill_hint.importers == ["bar.py", "baz.py"]
        assert r.distill_sidecar_sha == sha

    def test_enterprise_tier(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, "foo.py")
        _write_entitlement(tmp_path / ".trw", "enterprise")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.tier == "enterprise"
        assert r.distill_status == "hint_available"

    def test_beta_tester_tier_unlocks_sidecar(self, tmp_path: Path) -> None:
        # sub_Y-f6QQ3Y_Os9b0vM: tester-program (beta) users must NOT get the
        # paid-tier remediation — the feature is unlocked for them.
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, "foo.py")
        _write_entitlement(tmp_path / ".trw", "beta")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.tier == "beta"
        assert r.distill_status != "tier_required"
        assert r.distill_status == "hint_available"
        assert r.distill_hint is not None


class TestPaidTierGracefulFailures:
    def test_sidecar_missing(self, tmp_path: Path) -> None:
        _make_git_repo(tmp_path)
        # No sidecar written
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.tier == "pro"
        assert r.distill_status == "sidecar_missing"
        assert "trw-distill self-improve before-edit" in (r.distill_action or "")

    def test_stale_sha(self, tmp_path: Path) -> None:
        # Write a sidecar at the CURRENT SHA's path but with a different
        # internal envelope SHA — that's the real "stale" failure mode
        # (sidecar file present but generated against an older HEAD).
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        stale_sha = "0" * 40
        envelope = {
            "schema_version": _SCHEMA_VERSION_ACCEPTED,
            "sha": stale_sha,  # stale value INSIDE envelope
            "generated_at_unix": 1714000000.0,
            "payload": {
                "target_path": "foo.py",
                "target_exists_in_map": True,
                "importers": [],
                "inferred_tests": [],
                "doc_references": [],
                "co_change_neighbors": [],
                "hotspot_warnings": [],
                "risk_score": None,
            },
        }
        (cache_dir / f"before-edit-hint-{sha}.json").write_text(json.dumps(envelope))
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.distill_status == "stale_sha"

    def test_target_not_in_sidecar(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, target_path="other.py")
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.distill_status == "target_not_in_sidecar"

    def test_schema_mismatch(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(
            cache_dir,
            sha,
            "foo.py",
            schema_version="risk-report-sidecar/v99",
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.distill_status == "schema_mismatch"

    def test_sidecar_malformed(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / f"before-edit-hint-{sha}.json").write_text("{ not json")
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.distill_status == "sidecar_missing"  # JSON load fails → treated as missing

    def test_payload_validation_failure(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        # Valid envelope, but payload extra field (Pydantic extra=forbid)
        _write_sidecar(
            cache_dir,
            sha,
            "foo.py",
            hint_overrides={"unexpected_field": "boom"},
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.distill_status == "sidecar_malformed"


class TestSelectHintHelper:
    def test_returns_action_strings(self, tmp_path: Path) -> None:
        # Direct unit test of helper independent of git/entitlement
        sidecar = tmp_path / "nope.json"
        hint, status, action = _select_distill_hint(sidecar, "foo.py", "abc" * 13 + "a")
        assert hint is None
        assert status == "sidecar_missing"
        assert "trw-distill self-improve before-edit" in (action or "")


class TestModelContracts:
    def test_result_is_frozen(self) -> None:
        r = BeforeEditHintResult(file_path="x", tier="free")
        with pytest.raises(Exception):
            r.file_path = "y"  # type: ignore[misc]

    def test_payload_extra_forbid(self) -> None:
        with pytest.raises(Exception):
            BeforeYouEditHintPayload(  # type: ignore[call-arg]
                target_path="x",
                target_exists_in_map=False,
                some_unknown_field="boom",
            )

    def test_learning_summary_minimal(self) -> None:
        ls = LearningSummary(id="L-abc", summary="hi")
        assert ls.impact == 0.0
        assert ls.tags == []
