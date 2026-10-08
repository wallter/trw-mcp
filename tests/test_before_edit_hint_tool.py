"""Tests for trw_code(mode="hint") (PRD-DIST-1983..1986, cycle 746; retargeted PRD-CORE-300)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from tests._structlog_capture import captured_structlog  # noqa: F401  (fixture, imported by name)
from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools._before_edit_hint_core import (
    _SCHEMA_VERSION_ACCEPTED,
    BeforeEditHintResult,
    BeforeYouEditHintPayload,
    LearningSummary,
    _select_distill_hint,
    compute_before_edit_hint,
)


def _make_unborn_git_repo(repo_path: Path) -> None:
    """Init a git repo with ZERO commits, so ``git rev-parse HEAD`` fails.

    This is the cheapest reproducible way to make HEAD unresolvable without
    removing git from PATH: an unborn HEAD exits 128. Real-world equivalents
    are a fresh ``git init`` before the first commit, a corrupt ``.git/``, and
    a machine with no git CLI.
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_path, check=True)


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

    def test_unresolvable_head_is_not_stale_sha(self, tmp_path: Path) -> None:
        """`git rev-parse HEAD` failing must NOT be reported as ``stale_sha``.

        ``stale_sha`` asserts a sidecar was read and its SHA disagreed with
        HEAD. When HEAD itself cannot be resolved, nothing was compared — only
        the action string ever admitted that, while ``distill_status`` (which
        is what lands in durable telemetry via ``emit_hint_delivered``) claimed
        a comparison had happened. The substrate's ``no_git_sha`` is the honest
        status, and four sibling tools already report it.
        """
        _make_unborn_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.distill_status == "no_git_sha"
        assert r.distill_status != "stale_sha"

    def test_unresolvable_head_and_stale_sidecar_are_distinguishable(self, tmp_path: Path) -> None:
        """Non-vacuity control for :meth:`test_unresolvable_head_is_not_stale_sha`.

        A ``compute_before_edit_hint`` that returned ``no_git_sha``
        unconditionally would satisfy that test. Both scenarios are exercised
        here in one test and asserted to differ, so collapsing either onto the
        other fails.
        """
        unborn = tmp_path / "unborn"
        _make_unborn_git_repo(unborn)
        _write_entitlement(unborn / ".trw", "pro")
        unborn_result = compute_before_edit_hint(file_path="foo.py", repo_root=str(unborn))

        stale = tmp_path / "stale"
        sha = _make_git_repo(stale)
        cache_dir = stale / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, "foo.py")
        # Rewrite the envelope's internal sha so a REAL comparison disagrees.
        sidecar_file = cache_dir / f"before-edit-hint-{sha}.json"
        envelope = json.loads(sidecar_file.read_text())
        envelope["sha"] = "0" * 40
        sidecar_file.write_text(json.dumps(envelope))
        _write_entitlement(stale / ".trw", "pro")
        stale_result = compute_before_edit_hint(file_path="foo.py", repo_root=str(stale))

        assert unborn_result.distill_status == "no_git_sha"
        assert stale_result.distill_status == "stale_sha"
        assert unborn_result.distill_status != stale_result.distill_status

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

    def test_payload_extra_field_ignored_hint_still_renders(self, tmp_path: Path) -> None:
        """A newer trw-distill's additive field must not sink the whole hint (extra="ignore")."""
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
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
        assert r.distill_status == "hint_available"
        assert r.distill_hint is not None
        assert r.distill_hint.target_path == "foo.py"

    def test_payload_known_field_wrong_type_still_malformed(self, tmp_path: Path) -> None:
        """Forward-compat tolerance for UNKNOWN fields must not weaken strictness on KNOWN ones."""
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(
            cache_dir,
            sha,
            "foo.py",
            hint_overrides={"risk_score": "not-a-float"},
        )
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(
            file_path="foo.py",
            repo_root=str(tmp_path),
        )
        assert r.distill_status == "sidecar_malformed"


class TestSelectHintHelper:
    """Direct unit tests of the payload-validation half, independent of git.

    Envelope/SHA/schema resolution now lives in ``_sidecar_substrate`` (see
    ``test_sidecar_substrate.py``); what remains here is this tool's own
    contract on an already-loaded payload.
    """

    def test_non_dict_payload_is_malformed(self) -> None:
        hint, status, action = _select_distill_hint(["not", "a", "dict"], "foo.py")
        assert hint is None
        assert status == "sidecar_malformed"
        assert action

    def test_other_target_returns_actionable_remediation(self) -> None:
        hint, status, action = _select_distill_hint({"target_path": "other.py"}, "foo.py")
        assert hint is None
        assert status == "target_not_in_sidecar"
        assert "trw-distill self-improve before-edit" in (action or "")

    def test_matching_payload_validates(self) -> None:
        hint, status, action = _select_distill_hint(
            {"target_path": "foo.py", "target_exists_in_map": True},
            "foo.py",
        )
        assert status == "hint_available"
        assert action is None
        assert hint is not None
        assert hint.target_path == "foo.py"


class TestEligibilityTelemetry:
    """PRD-CORE-231-FR01: ``hint_delivered`` must not claim undetermined eligibility."""

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
        emitted: list[dict[str, str]] = []

        def _fake(*, tier: str, distill_status: str, file_path: str, client: str | None = None) -> None:
            emitted.append({"tier": tier, "distill_status": distill_status, "file_path": file_path})

        monkeypatch.setattr(
            "trw_mcp.channels._distill_telemetry.emit_hint_delivered",
            _fake,
        )
        return emitted

    def test_unresolvable_repo_root_emits_no_eligible_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``no_repo_root`` means the entitlement gate never ran.

        ``emit_hint_delivered`` stamps ``eligible: True`` on every record it
        writes, so emitting one here would durably assert an entitlement check
        that did not happen.
        """
        emitted = self._capture(monkeypatch)
        # _deadline: root discovery now draws on the lookup's one git budget, so it is handed the deadline.
        monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.resolve_repo_root", lambda _root, _deadline=None: None)
        r = compute_before_edit_hint(file_path="foo.py")
        assert r.distill_status == "no_repo_root"
        assert emitted == []

    def test_resolvable_miss_still_emits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-vacuity control: a real MISS must still be recorded.

        Suppressing every emission would pass the test above; the >=90%
        delivery gate is only meaningful if misses reach telemetry too.
        """
        emitted = self._capture(monkeypatch)
        _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "pro")
        r = compute_before_edit_hint(file_path="foo.py", repo_root=str(tmp_path))
        assert r.distill_status == "sidecar_missing"
        assert [e["distill_status"] for e in emitted] == ["sidecar_missing"]


class TestModelContracts:
    def test_result_is_frozen(self) -> None:
        r = BeforeEditHintResult(file_path="x", tier="free")
        with pytest.raises(Exception):
            r.file_path = "y"  # type: ignore[misc]

    def test_payload_extra_ignored(self) -> None:
        """extra="ignore": an additive field from a newer trw-distill is dropped, not fatal."""
        hint = BeforeYouEditHintPayload(  # type: ignore[call-arg]
            target_path="x",
            target_exists_in_map=False,
            some_unknown_field="boom",
        )
        assert hint.target_path == "x"
        assert not hasattr(hint, "some_unknown_field")

    def test_payload_known_field_wrong_type_still_rejected(self) -> None:
        with pytest.raises(Exception):
            BeforeYouEditHintPayload(
                target_path="x",
                target_exists_in_map="not-a-bool",  # type: ignore[arg-type]
            )

    def test_learning_summary_minimal(self) -> None:
        ls = LearningSummary(id="L-abc", summary="hi")
        assert ls.impact == 0.0
        assert ls.tags == []


def _write_batch_sidecar(cache_dir: Path, sha: str, target_paths: list[str]) -> Path:
    """Write a ``before-edit-batch-<sha>.json`` covering *target_paths*."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    hints = [
        {
            "target_path": target,
            "target_exists_in_map": True,
            "importers": [f"importer_of_{target}"],
            "inferred_tests": [],
            "doc_references": [],
            "co_change_neighbors": [],
            "hotspot_warnings": [],
            "risk_score": 0.5,
        }
        for target in target_paths
    ]
    envelope = {
        "schema_version": _SCHEMA_VERSION_ACCEPTED,
        "sha": sha,
        "generated_at_unix": 1714000000.0,
        "payload": {
            "total_files": len(hints),
            "files_in_map": len(hints),
            "total_hotspot_warnings": 0,
            "hints": hints,
        },
    }
    path = cache_dir / f"before-edit-batch-{sha}.json"
    path.write_text(json.dumps(envelope, indent=2))
    return path


class TestBatchArtifactFallback:
    """The single-file artifact holds ONE hint per sha, so a multi-file commit
    can serve at most one of its files from it. Everything else must come from
    the batch artifact, which the post-commit refresh now writes.
    """

    def test_batch_serves_a_file_the_single_artifact_cannot(self, tmp_path: Path) -> None:
        # Exactly the reproduced production shape: a 3-file commit ran three
        # single-file invocations, all exiting 0, and the last one won.
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, target_path="c.py")
        _write_batch_sidecar(cache_dir, sha, ["a.py", "b.py", "c.py"])
        _write_entitlement(tmp_path / ".trw", "pro")

        for losing_file in ("a.py", "b.py"):
            result = compute_before_edit_hint(file_path=losing_file, repo_root=str(tmp_path))

            assert result.distill_status == "hint_available", losing_file
            assert result.distill_hint is not None
            assert result.distill_hint.target_path == losing_file
            assert result.distill_hint.importers == [f"importer_of_{losing_file}"]
            # Provenance is legible without a new response field.
            assert "before-edit-batch-" in (result.distill_sidecar_path or "")

    def test_absent_target_is_still_a_miss(self, tmp_path: Path) -> None:
        """Non-vacuity control.

        A fallback that returned the first batch entry, or that reported
        ``hint_available`` whenever a batch artifact existed, would pass the
        test above. A file the batch does not cover must still miss.
        """
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_batch_sidecar(cache_dir, sha, ["a.py", "b.py"])
        _write_entitlement(tmp_path / ".trw", "pro")

        result = compute_before_edit_hint(file_path="never_committed.py", repo_root=str(tmp_path))

        assert result.distill_status == "target_not_in_sidecar"
        assert result.distill_hint is None
        # The remediation must say WHICH artifact was consulted, or the reader
        # cannot tell an absent file from a broken one.
        assert "Batch sidecar does not cover" in (result.distill_action or "")

    def test_matching_single_artifact_still_wins(self, tmp_path: Path) -> None:
        """The batch path must not displace a correct single-file answer."""
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_sidecar(cache_dir, sha, target_path="foo.py")
        _write_batch_sidecar(cache_dir, sha, ["foo.py"])
        _write_entitlement(tmp_path / ".trw", "pro")

        result = compute_before_edit_hint(file_path="foo.py", repo_root=str(tmp_path))

        assert result.distill_status == "hint_available"
        assert result.distill_hint is not None
        # _write_sidecar's importers, not _write_batch_sidecar's.
        assert result.distill_hint.importers == ["bar.py", "baz.py"]
        assert "before-edit-hint-" in (result.distill_sidecar_path or "")

    def test_batch_only_and_no_single_artifact(self, tmp_path: Path) -> None:
        """The common case after the producer fix: only the batch artifact exists."""
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_batch_sidecar(cache_dir, sha, ["a.py", "b.py", "c.py"])
        _write_entitlement(tmp_path / ".trw", "pro")

        result = compute_before_edit_hint(file_path="b.py", repo_root=str(tmp_path))

        assert result.distill_status == "hint_available"

    @pytest.mark.parametrize(
        ("ancestor_enabled", "expected"), [("false", "sidecar_missing"), ("true", "sidecar_too_far_behind")]
    )
    def test_stale_batch_does_not_answer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor_enabled: str, expected: str
    ) -> None:
        """A batch artifact from a commit that is not HEAD's ancestor is not a fallback.

        Non-vacuity for the sha check: without it this test's batch file would
        satisfy the lookup and a hint from a previous commit's map would be
        served as current. With hint_sidecar_ancestor_enabled on, the refusal
        names why: the sha is not a proven ancestor of HEAD.
        """
        monkeypatch.setenv("TRW_HINT_SIDECAR_ANCESTOR_ENABLED", ancestor_enabled)
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_batch_sidecar(cache_dir, "0" * 40, ["a.py"])
        _write_entitlement(tmp_path / ".trw", "pro")
        assert (cache_dir / f"before-edit-batch-{'0' * 40}.json").exists()
        assert not (cache_dir / f"before-edit-batch-{sha}.json").exists()

        result = compute_before_edit_hint(file_path="a.py", repo_root=str(tmp_path))

        assert result.distill_status == expected
        assert result.distill_hint is None

    def test_tier_gate_is_not_reopened_by_the_fallback(self, tmp_path: Path) -> None:
        """A denied entitlement must not get a second lookup.

        ``tier_required`` means the gate ran and said no; retrying the batch
        artifact would both leak the feature and burn two more git subprocesses
        on every ungated edit.
        """
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_batch_sidecar(cache_dir, sha, ["a.py"])

        result = compute_before_edit_hint(file_path="a.py", repo_root=str(tmp_path))

        assert result.distill_status == "tier_required"
        assert result.distill_hint is None


# --- PRD-FIX-144 FR02 / NFR01: the hint records exposure with the edited file ---


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestExposureRecording:
    @pytest.fixture
    def project(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon) -> Path:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
        monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
        monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        # PRD-CORE-280 slice e1: this workspace is built directly (not via
        # ``daemon_checkout``), so pin it to the shared session daemon per the
        # fixture contract's "test that builds its own .trw" note.
        monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
        attach_checkout(trw_dir, memory_daemon)
        from trw_mcp.models.config import reload_config

        reload_config()
        return tmp_path

    @staticmethod
    def _learn(count: int) -> list[str]:
        from tests.conftest import extract_tool_fn, make_test_server

        learn = extract_tool_fn(make_test_server("learning"), "trw_learn")
        topics = ["config loading", "route wiring", "blocking IO", "error pages", "shutdown hooks"]
        return [
            learn(summary=f"app.py {topics[i]} gotcha", detail=f"app.py detail about {topics[i]}.", impact=0.7)[
                "learning_id"
            ]
            for i in range(count)
        ]

    @staticmethod
    def _logs(project: Path) -> tuple[Path, Path]:
        logs = project / ".trw" / "logs"
        return logs / "recall_tracking.jsonl", logs / "surface_tracking.jsonl"

    def test_hint_records_exposure_with_repo_relative_file(self, project: Path) -> None:
        (lid,) = self._learn(1)
        receipts_path, surface_path = self._logs(project)
        receipts_before, surface_before = len(_jsonl(receipts_path)), len(_jsonl(surface_path))

        result = compute_before_edit_hint(file_path="app.py")

        assert [item.id for item in result.learnings] == [lid]
        receipts = _jsonl(receipts_path)[receipts_before:]
        surface = _jsonl(surface_path)[surface_before:]
        assert len(receipts) == 1 and len(surface) == 1
        receipt, row = receipts[0], surface[0]
        assert receipt["learning_id"] == lid
        assert receipt["surface"] == "before_edit_hint"
        assert receipt["query"] == "app.py"
        assert receipt["files_context"] == ["app.py"]
        assert receipt["outcome"] is None
        assert row["learning_id"] == lid
        assert row["surface_type"] == "before_edit_hint"
        assert row["files_context"] == ["app.py"]
        assert row["session_id"] == receipt["session_id"] != ""

    def test_absolute_in_repo_path_is_recorded_repo_relative(self, project: Path) -> None:
        self._learn(1)
        receipts_path, surface_path = self._logs(project)
        compute_before_edit_hint(file_path=str(project / "pkg" / ".." / "app.py"))
        assert _jsonl(receipts_path)[-1]["files_context"] == ["app.py"]
        assert _jsonl(surface_path)[-1]["files_context"] == ["app.py"]

    def test_path_outside_root_records_empty_files_context(self, project: Path, tmp_path_factory: Any) -> None:
        self._learn(1)
        outside = tmp_path_factory.mktemp("elsewhere") / "app.py"
        receipts_path, surface_path = self._logs(project)
        result = compute_before_edit_hint(file_path=str(outside))
        assert result.learnings_count == 1
        receipt, row = _jsonl(receipts_path)[-1], _jsonl(surface_path)[-1]
        assert receipt["files_context"] == [] and receipt["query"] == ""
        assert row["files_context"] == []
        assert str(outside) not in receipts_path.read_text() + surface_path.read_text()

    def test_zero_learnings_writes_nothing(self, project: Path) -> None:
        receipts_path, surface_path = self._logs(project)
        result = compute_before_edit_hint(file_path="app.py")
        assert result.learnings_count == 0
        assert _jsonl(receipts_path) == [] and _jsonl(surface_path) == []

    def test_reviewer_role_records_nothing(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """PRD-CORE-300: under the reviewer role, ``compute_before_edit_hint`` now
        skips ``emit_hint_delivered`` and ``_record_exposure`` entirely (superseding
        PRD-SEC-015 NFR03's "one acknowledged residual write" — see
        test_reviewer_surface_purity.py for the byte-identical-tree sweep)."""
        self._learn(1)
        receipts_path, surface_path = self._logs(project)
        before = (len(_jsonl(receipts_path)), len(_jsonl(surface_path)))
        monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
        compute_before_edit_hint(file_path="app.py")
        after = (len(_jsonl(receipts_path)), len(_jsonl(surface_path)))
        assert after == before

    def test_hint_telemetry_appends_are_bounded_and_read_free(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NFR01: <= 2 appends per surfaced learning, 0 reads of the three logs."""
        import builtins

        from trw_mcp.tools._learnings_collector import DEFAULT_TOP_N

        self._learn(DEFAULT_TOP_N)
        watched = {"recall_tracking.jsonl", "surface_tracking.jsonl", "session_outcomes.jsonl"}
        opens: list[tuple[str, str]] = []
        real_path_open, real_open, real_os_open = Path.open, builtins.open, os.open

        def _note(target: object, mode: str) -> None:
            name = Path(str(target)).name
            if name in watched:
                opens.append((name, mode))

        def path_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            _note(self, mode)
            return real_path_open(self, mode, *args, **kwargs)

        def plain_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if isinstance(file, (str, Path)):
                _note(file, mode)
            return real_open(file, mode, *args, **kwargs)

        def os_open(file: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            # the checkout writer appends through os.open(..., dir_fd=) (AIKIDO 2a): O_APPEND counts as an append
            fd = real_os_open(file, flags, *args, **kwargs)  # a failed create-probe open is no append
            if isinstance(file, (str, Path)):
                _note(file, "a" if flags & os.O_APPEND else ("w" if flags & (os.O_WRONLY | os.O_RDWR) else "r"))
            return fd

        monkeypatch.setattr(Path, "open", path_open)
        monkeypatch.setattr(os, "open", os_open)
        monkeypatch.setattr(builtins, "open", plain_open)
        result = compute_before_edit_hint(file_path="app.py")
        monkeypatch.undo()

        assert result.learnings_count == DEFAULT_TOP_N
        appends = [o for o in opens if "a" in o[1]]
        reads = [o for o in opens if "a" not in o[1] and "w" not in o[1]]
        assert reads == []
        assert 0 < len(appends) <= 2 * DEFAULT_TOP_N
        receipts_path, surface_path = self._logs(project)
        assert len([r for r in _jsonl(receipts_path) if r.get("surface") == "before_edit_hint"]) == DEFAULT_TOP_N
        assert len([r for r in _jsonl(surface_path) if r["surface_type"] == "before_edit_hint"]) == DEFAULT_TOP_N

    def test_recording_adds_at_most_50ms_median(self, project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR02 hook-latency ceiling: median added wall time over 20 runs <= 50 ms."""
        import statistics
        import time

        import trw_mcp.tools._before_edit_hint_core as core
        from trw_mcp.tools._learnings_collector import DEFAULT_TOP_N

        self._learn(DEFAULT_TOP_N)
        real_record = core._record_exposure
        compute_before_edit_hint(file_path="app.py")  # warm caches and imports once

        def timed(recording: bool) -> float:
            monkeypatch.setattr(core, "_record_exposure", real_record if recording else lambda *_a: None)
            started = time.perf_counter()
            result = compute_before_edit_hint(file_path="app.py")
            elapsed = time.perf_counter() - started
            assert result.learnings_count == DEFAULT_TOP_N
            return elapsed

        on: list[float] = []
        off: list[float] = []
        for _ in range(20):  # interleaved so drift hits both arms alike
            on.append(timed(True))
            off.append(timed(False))
        added = statistics.median(on) - statistics.median(off)
        print(
            f"before_edit_hint recording: median on={statistics.median(on) * 1000:.2f}ms "
            f"off={statistics.median(off) * 1000:.2f}ms added={added * 1000:.2f}ms"
        )
        assert added <= 0.050

    def test_registered_tool_is_logged_and_response_unchanged(self, project: Path) -> None:
        """FR02: trw_code(mode="hint") reaches tool telemetry through the tool-call wrapper (PRD-FIX-150)."""
        from tests.conftest import extract_tool_fn, make_test_server
        from trw_mcp.telemetry.tool_call_timing import wrap_tool

        raw = extract_tool_fn(make_test_server("code"), "trw_code")
        fn = wrap_tool(
            raw,
            tool_name="trw_code",
            session_id_resolver=lambda: "s",
            run_dir_resolver=lambda: None,
            fallback_dir_resolver=lambda: project / ".trw" / "context",
        )
        response = fn(mode="hint", files="app.py")
        (hint,) = response["hints"]
        assert "status" not in response
        assert set(hint) >= {"file_path", "distill_status"}
        events = "".join(p.read_text() for p in (project / ".trw").rglob("*events*.jsonl"))
        assert "trw_code" in events


# --- PRD-CORE-332 FR07/FR08: the anchored lookup, its degrade, and its path key ---

_ANCHORED = "httpx/_client.py"


def _anchor_rows() -> list[Any]:
    from tests._anchor_daemon_fake import lesson

    return [
        lesson("L-text", "_client.py pools connections per host"),
        lesson("L-anchor", "Close the transport before retrying", anchors=(_ANCHORED,)),
    ]


def _events(logs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [entry for entry in logs if entry.get("event") == name]


def _text_only_learnings(file_path: str) -> list[LearningSummary]:
    from trw_mcp.tools._learnings_collector import build_file_queries, collect_learnings

    return collect_learnings(build_file_queries(file_path))


class TestAnchorPathKey:
    """FR08: the anchor key is the lexical repo-relative path, never through a symlink or outside the root."""

    def test_repo_relative_uses_repo_root_arg(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests._anchor_daemon_fake import AnchoredDaemon, use_daemon

        repo, elsewhere = tmp_path / "repo", tmp_path / "cwd-root"
        daemon = AnchoredDaemon(_anchor_rows())
        use_daemon(monkeypatch, elsewhere, daemon)  # the ambient project root is NOT the repo

        result = compute_before_edit_hint(file_path=str(repo / "httpx" / "_client.py"), repo_root=str(repo))

        assert daemon.files == [_ANCHORED]
        assert [item.id for item in result.learnings] == ["L-anchor", "L-text"]

    def test_path_outside_root_skips_anchor_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
    ) -> None:
        from tests._anchor_daemon_fake import AnchoredDaemon, use_daemon

        repo = tmp_path / "repo"
        daemon = AnchoredDaemon(_anchor_rows())
        use_daemon(monkeypatch, repo, daemon)

        result = compute_before_edit_hint(
            file_path=str(tmp_path / "elsewhere" / "httpx" / "_client.py"), repo_root=str(repo)
        )

        assert daemon.files == []
        assert [item.id for item in result.learnings] == ["L-text"]  # text queries still run
        assert [e["reason"] for e in _events(captured_structlog, "anchor_lookup_skipped")] == ["outside_root"]

    def test_symlinked_path_skips_anchor_lookup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
    ) -> None:
        from tests._anchor_daemon_fake import AnchoredDaemon, use_daemon

        repo = tmp_path / "repo"
        (repo / "httpx").mkdir(parents=True)
        (repo / "link").symlink_to(repo / "httpx", target_is_directory=True)
        daemon = AnchoredDaemon(_anchor_rows())
        use_daemon(monkeypatch, repo, daemon)

        result = compute_before_edit_hint(file_path=str(repo / "link" / "_client.py"), repo_root=str(repo))

        assert daemon.files == []
        assert [item.id for item in result.learnings] == ["L-text"]
        assert [e["reason"] for e in _events(captured_structlog, "anchor_lookup_skipped")] == ["symlink"]

    def test_relative_path_and_dot_segments_normalize(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests._anchor_daemon_fake import AnchoredDaemon, use_daemon

        repo = tmp_path / "repo"
        daemon = AnchoredDaemon(_anchor_rows())
        use_daemon(monkeypatch, repo, daemon)

        compute_before_edit_hint(file_path="./httpx/../httpx/_client.py", repo_root=str(repo))

        assert daemon.files == [_ANCHORED]


class TestAnchorLookupDegrade:
    """FR07: an older client or daemon yields exactly today's text-only hint, observably."""

    def test_hint_degrades_when_client_lacks_anchored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
    ) -> None:
        from tests._anchor_daemon_fake import TextOnlyDaemon, use_daemon

        repo = tmp_path / "repo"
        use_daemon(monkeypatch, repo, TextOnlyDaemon(_anchor_rows()))
        file_path = str(repo / "httpx" / "_client.py")

        result = compute_before_edit_hint(file_path=file_path, repo_root=str(repo))

        assert [item.id for item in result.learnings] == ["L-text"]
        assert result.learnings == _text_only_learnings(file_path)
        assert len(_events(captured_structlog, "anchor_lookup_unsupported")) == 1

    def test_hint_degrades_when_daemon_answers_unknown_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
    ) -> None:
        from fastmcp.exceptions import ToolError

        from tests._anchor_daemon_fake import AnchoredDaemon, use_daemon

        repo = tmp_path / "repo"
        daemon = AnchoredDaemon(_anchor_rows(), error=ToolError("Unknown tool: 'memory_anchored'"))
        use_daemon(monkeypatch, repo, daemon)
        file_path = str(repo / "httpx" / "_client.py")

        result = compute_before_edit_hint(file_path=file_path, repo_root=str(repo))

        assert daemon.files == [_ANCHORED]
        assert [item.id for item in result.learnings] == ["L-text"]
        assert result.learnings == _text_only_learnings(file_path)
        assert len(_events(captured_structlog, "anchor_lookup_unsupported")) == 1
        assert _events(captured_structlog, "recall_learnings_failed") == []

    @pytest.mark.parametrize(
        "message",
        [
            "Error calling tool 'memory_anchored': file exceeds 4096 characters",
            "Unknown tool: 'memory_recall'",
            "memory_anchored refused: Unknown tool: 'memory_anchored'",
        ],
    )
    def test_other_tool_error_is_not_unsupported(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        captured_structlog: list[dict[str, Any]],
        message: str,
    ) -> None:
        from fastmcp.exceptions import ToolError

        from tests._anchor_daemon_fake import AnchoredDaemon, use_daemon

        repo = tmp_path / "repo"
        use_daemon(monkeypatch, repo, AnchoredDaemon(_anchor_rows(), error=ToolError(message)))

        result = compute_before_edit_hint(file_path=str(repo / "httpx" / "_client.py"), repo_root=str(repo))

        assert _events(captured_structlog, "anchor_lookup_unsupported") == []
        assert len(_events(captured_structlog, "recall_learnings_failed")) == 1
        assert [item.id for item in result.learnings] == ["L-text"]

    async def test_fastmcp_unknown_tool_text_is_what_the_probe_matches(self) -> None:
        """Pins fastmcp's wording: a change here fails this test instead of silently disabling the probe."""
        from fastmcp import Client, FastMCP
        from fastmcp.exceptions import ToolError

        from trw_mcp.state._anchored_lookup import UNKNOWN_TOOL_PREFIX

        server = FastMCP("no-anchored")

        @server.tool()
        def memory_recall(query: str) -> str:
            return query

        async with Client(server) as client:
            with pytest.raises(ToolError) as raised:
                await client.call_tool("memory_anchored", {"namespace": "n", "file": "a.py"})
        assert str(raised.value).startswith(UNKNOWN_TOOL_PREFIX)


class TestAnchorLookupAgainstTheInstalledDaemon:
    """FR07 against the real trw-memory: before S2 its client has no ``anchored``, and the hint is text-only."""

    def test_installed_daemon_hint(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        memory_daemon: MemoryDaemon,
        captured_structlog: list[dict[str, Any]],
    ) -> None:
        import asyncio

        from trw_memory.daemon.client import DaemonClient

        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
        monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
        # INSTALLED-DAEMON-HINT-FLAKE: the hint's recall runs under hint_recall_deadline_ms (600 ms default) and
        # returns no learnings past it -- correct product behaviour, but a loaded release gate (-n 8 beside other
        # suites) pushed this real-daemon recall past 600 ms and the test saw an empty list. What this test pins
        # is WHICH learnings lead, not the latency budget (test_hint_recall_budget.py owns that), so it runs at
        # the 10 s maximum.
        monkeypatch.setenv("TRW_HINT_RECALL_DEADLINE_MS", "10000")
        monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
        namespace, client = attach_checkout(tmp_path / ".trw", memory_daemon)
        from trw_mcp.models.config import reload_config

        reload_config()
        anchor = {"file": _ANCHORED, "symbol_name": "Client.send"}
        asyncio.run(client.store("Close the transport before retrying", namespace, learning={"anchors": [anchor]}))
        asyncio.run(client.store("_client.py pools connections per host", namespace))
        file_path = str(tmp_path / "httpx" / "_client.py")

        result = compute_before_edit_hint(file_path=file_path)
        assert result.learnings_status == "ok", "the recall timed out: the test is measuring load, not ranking"
        shown = [item.summary for item in result.learnings]

        if hasattr(DaemonClient, "anchored"):  # PRD-CORE-332 S2 has landed: the anchored lesson leads
            assert shown[0] == "Close the transport before retrying"
            assert _events(captured_structlog, "anchor_lookup_unsupported") == []
        else:
            assert shown == [item.summary for item in _text_only_learnings(file_path)]
            assert shown == ["_client.py pools connections per host"]
            assert len(_events(captured_structlog, "anchor_lookup_unsupported")) == 1


# ---------------------------------------------------------------------------
# PRD-CORE-336-FR04: every client profile delivers the pre-edit hint, or falls back
# ---------------------------------------------------------------------------


def test_every_profile_wired_or_fallback() -> None:
    """Each registered profile is wired to a documented model-visible hook channel, or listed as fallback.

    The profile set is the registry's (``builtin_client_ids``), never a copied
    list, so a ninth profile added without a pre-edit decision fails here.
    """
    from trw_mcp.bootstrap._utils import _DATA_DIR
    from trw_mcp.models.config._pre_edit_channels import PRE_EDIT_HINT_CHANNELS, unclassified_profiles
    from trw_mcp.models.config._profiles import builtin_client_ids

    registry = builtin_client_ids()
    assert unclassified_profiles(registry) == ()
    assert set(PRE_EDIT_HINT_CHANNELS) == set(registry), "an entry for a profile the registry does not have"
    for client_id, entry in PRE_EDIT_HINT_CHANNELS.items():
        assert entry.doc_url.startswith("https://"), client_id
        if entry.delivery == "hook":
            assert entry.hook is not None and (_DATA_DIR / entry.hook).is_file(), client_id
            assert entry.output_path, f"{client_id}: a wired hook must pin the model-visible output field"
            assert entry.live_check, f"{client_id}: a wired client needs a recorded live visibility check"
        else:
            assert entry.hook is None and entry.output_path == (), client_id
    # Non-vacuity: both classes are populated, so neither branch above was skipped.
    assert {entry.delivery for entry in PRE_EDIT_HINT_CHANNELS.values()} == {"hook", "instruction_fallback"}


@pytest.mark.parametrize("extra", ["brand-new-client", "claude-code-2"])
def test_a_profile_without_a_decision_is_reported(extra: str) -> None:
    """Boundary: a registry id the matrix does not classify is named, not silently passed."""
    from trw_mcp.models.config._pre_edit_channels import unclassified_profiles
    from trw_mcp.models.config._profiles import builtin_client_ids

    assert unclassified_profiles((*builtin_client_ids(), extra)) == (extra,)


#: One PreToolUse payload per hook-wired client, in that client's own wire shape.
#: Claude Code names the file; Codex sends apply_patch with the patch text.
_WIRED_CLIENT_PAYLOADS: dict[str, dict[str, object]] = {
    "claude-code": {"tool_use_id": "toolu-c1", "tool_name": "Edit", "tool_input": {"file_path": "src/app.py"}},
    "codex": {
        "tool_use_id": "call-c1",
        "tool_name": "apply_patch",
        "turn_id": "turn-1",
        "tool_input": {"command": "*** Begin Patch\n*** Update File: src/app.py\n@@\n-x = 1\n+x = 2\n*** End Patch\n"},
    },
}


def _run_cc03(project: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    from tests.channels.claude_code._distill_hint_support import deploy_distill_hint

    (project / ".trw").mkdir(parents=True, exist_ok=True)
    (project / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    return subprocess.run(
        ["sh", str(deploy_distill_hint(project))],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TRW_PROJECT_DIR": str(project)},
    )


def _run_cc03_with_hint(project: Path, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    """The wired hook against a real T2 sidecar for src/module.py: the hook prints only when it has something to say."""
    from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint
    from tests.channels.claude_code.test_cc03_json_output import _project

    _project(project, ["w"])
    return subprocess.run(
        ["sh", str(deploy_distill_hint(project))],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "PYTHONPATH": CHECKOUT_PYTHONPATH,
            "TRW_PROJECT_DIR": str(project),
            "HOME": str(project),
        },
    )


def _wired() -> list[str]:
    from trw_mcp.models.config._pre_edit_channels import PRE_EDIT_HINT_CHANNELS

    return sorted(cid for cid, entry in PRE_EDIT_HINT_CHANNELS.items() if entry.delivery == "hook")


def test_every_wired_client_has_a_contract_payload() -> None:
    """A newly wired client without a payload below would skip its contract test."""
    assert set(_wired()) == set(_WIRED_CLIENT_PAYLOADS)


@pytest.mark.parametrize("client_id", _wired())
def test_wired_hook_prints_the_pinned_output_shape(client_id: str, tmp_path: Path) -> None:
    """The hook prints exactly one JSON object: the documented model-visible field and nothing else."""
    from trw_mcp.models.config._pre_edit_channels import PRE_EDIT_HINT_CHANNELS

    payload = json.loads(json.dumps(_WIRED_CLIENT_PAYLOADS[client_id]).replace("src/app.py", "src/module.py"))
    result = _run_cc03_with_hint(tmp_path, payload)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1, result.stdout
    payload = json.loads(lines[0])
    assert set(payload) == {"hookSpecificOutput"}
    assert set(payload["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
    assert payload["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    field: Any = payload
    for key in PRE_EDIT_HINT_CHANNELS[client_id].output_path:
        field = field[key]
    assert isinstance(field, str) and field.startswith("[TRW")


@pytest.mark.parametrize(
    ("tool_name", "command"),
    [
        ("apply_patch", "*** Begin Patch\n*** Update File: README.md\n@@\n-a\n+b\n*** End Patch\n"),
        ("apply_patch", "*** Begin Patch\n*** Delete File: src/app.py\n*** End Patch\n"),
        ("Bash", "*** Update File: src/app.py"),
    ],
    ids=["safe-extension", "delete-only", "not-apply-patch"],
)
def test_codex_payload_without_an_editable_code_file_prints_nothing(
    tool_name: str, command: str, tmp_path: Path
) -> None:
    """Boundary: only an apply_patch that updates or adds a code file is hinted."""
    payload = {"tool_use_id": "call-c2", "tool_name": tool_name, "tool_input": {"command": command}}

    result = _run_cc03(tmp_path, payload)

    assert result.returncode == 0
    assert result.stdout == ""


def test_codex_patch_hints_every_code_file_it_touches(tmp_path: Path) -> None:
    """A multi-file apply_patch hints each Update/Add target once (a repeat header is deduped); docs are skipped."""
    command = (
        "*** Begin Patch\n*** Add File: pkg/new_mod.py\n+x = 1\n*** Update File: other.py\n@@\n-a\n+b\n"
        "*** Update File: README.md\n@@\n-a\n+b\n*** Update File: other.py\n@@\n-c\n+d\n*** End Patch\n"
    )

    result = _run_cc03(
        tmp_path, {"tool_use_id": "call-c3", "tool_name": "apply_patch", "tool_input": {"command": command}}
    )

    assert result.returncode == 0
    markers = sorted(p.name.split(".py-")[0] for p in (tmp_path / ".trw" / "context" / "cc03-debounce").iterdir())
    assert markers == ["other", "pkg_new_mod"]


@pytest.mark.parametrize("count", [6, 9])
def test_codex_patch_hints_at_most_five_files(tmp_path: Path, count: int) -> None:
    """Boundary: past the cap, further files are neither hinted nor debounced."""
    headers = "".join(f"*** Add File: m{i}.py\n+x = {i}\n" for i in range(count))
    command = f"*** Begin Patch\n{headers}*** End Patch\n"

    _run_cc03(tmp_path, {"tool_use_id": "call-c4", "tool_name": "apply_patch", "tool_input": {"command": command}})

    markers = {p.name.split(".py-")[0] for p in (tmp_path / ".trw" / "context" / "cc03-debounce").iterdir()}
    assert markers == {f"m{i}" for i in range(5)}


def test_codex_patch_budget_skip_does_not_debounce_the_skipped_file(tmp_path: Path) -> None:
    """CORE-336-S3-KI (b): the in-process 1.6s batch budget stops the hook from

    computing every candidate file, distinct from the shell-side 5-file cap
    above. A file the batch budget skips was never actually attempted, so
    debouncing it here would silently swallow its NEXT edit for 180s too --
    only files the subprocess actually attempted may get a marker.
    """
    import sys

    from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint

    (tmp_path / ".trw" / "channels").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    # Pin the interpreter to the one running this test suite (real trw_mcp
    # importable), not whatever bare "python3" the restricted PATH resolves --
    # PRD-FIX-155's _get_python_path falls back to a python3 that usually
    # cannot import trw_mcp, which would exercise only the exception branch.
    (tmp_path / ".trw" / "channels" / "cc03-python.txt").write_text(sys.executable, encoding="utf-8")
    command = "*** Begin Patch\n*** Add File: first.py\n+x = 1\n*** Add File: second.py\n+x = 2\n*** End Patch\n"

    result = subprocess.run(
        ["sh", str(deploy_distill_hint(tmp_path))],
        input=json.dumps(
            {"tool_use_id": "call-budget", "tool_name": "apply_patch", "tool_input": {"command": command}}
        ),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "PYTHONPATH": CHECKOUT_PYTHONPATH,
            "TRW_PROJECT_DIR": str(tmp_path),
            "HOME": str(tmp_path),
            # Force every file after the first to trip the in-process batch
            # budget check immediately (any elapsed time exceeds a negative
            # budget), without needing a real slow computation to be flaky.
            "TRW_CC03_BATCH_BUDGET_S": "-1",
        },
    )

    assert result.returncode == 0
    markers = {p.name.split(".py-")[0] for p in (tmp_path / ".trw" / "context" / "cc03-debounce").iterdir()}
    assert markers == {"first"}, (
        f"second.py was never attempted (batch budget skip) and must not be debounced; got markers={markers}"
    )


def test_processed_files_journal_does_not_follow_a_symlinked_hints_dir(tmp_path: Path) -> None:
    """Codex review core336-s3ki r1 of 6fb90b972 (BLOCK): the

    TRW_CC03_PROCESSED_FILE journal used to sit at ``${_hints_dir}/.cc03-processed-$$``
    -- a checkout-influenced path. A crafted checkout shipping
    ``.trw/context/cc03-hints`` (or an ancestor) as a symlink could redirect
    that write anywhere the symlink pointed, no race required. The journal is
    now a private ``mktemp`` file outside the checkout entirely, so it can
    never land inside whatever a symlinked hints dir points at.
    """
    import sys

    from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint

    project = tmp_path / "project"
    project.mkdir()
    (project / ".trw" / "channels").mkdir(parents=True, exist_ok=True)
    (project / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    (project / ".trw" / "channels" / "cc03-python.txt").write_text(sys.executable, encoding="utf-8")

    outside_hints_dir = tmp_path / "attacker_outside_hints_dir"
    outside_hints_dir.mkdir()
    (project / ".trw" / "context").mkdir(parents=True, exist_ok=True)
    (project / ".trw" / "context" / "cc03-hints").symlink_to(outside_hints_dir, target_is_directory=True)

    command = "*** Begin Patch\n*** Add File: first.py\n+x = 1\n*** Add File: second.py\n+x = 2\n*** End Patch\n"
    result = subprocess.run(
        ["sh", str(deploy_distill_hint(project))],
        input=json.dumps(
            {"tool_use_id": "call-symlink", "tool_name": "apply_patch", "tool_input": {"command": command}}
        ),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TRW_PROJECT_DIR": str(project),
            "HOME": str(project),
            "PYTHONPATH": CHECKOUT_PYTHONPATH,
        },
    )

    assert result.returncode == 0, result.stderr
    leaked = list(outside_hints_dir.glob("*cc03-processed*")) + list(outside_hints_dir.glob(".cc03-processed*"))
    assert leaked == [], (
        f"the processed-files journal wrote through the symlinked cc03-hints dir into the attacker directory: {leaked}"
    )


def test_multi_file_batch_emits_earlier_hints_when_a_later_file_raises(tmp_path: Path) -> None:
    """CORE-336-S3-KI (a): a later file's compute failing must not discard hints

    already completed for earlier files in the same Codex multi-file batch.
    """
    import sys

    from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint

    shim_dir = tmp_path / "_shim"
    shim_dir.mkdir()
    # A sitecustomize.py runs at interpreter startup (site init, before the
    # hook's own -c program executes) whenever PYTHONPATH is honored -- which
    # is every hint subprocess this hook spawns, since it never passes -S.
    # Patching the module attribute here means the hook's own
    # `from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint`
    # binds the patched function, deterministically, with no timing race. The
    # shim directory holds ONLY sitecustomize.py -- a stub trw_mcp/ package
    # here would shadow the real one for every submodule, not just this one
    # attribute, and shatter the rest of the import.
    (shim_dir / "sitecustomize.py").write_text(
        "import os\n"
        '_boom = os.environ.get("TRW_TEST_CC03_BOOM_FILE")\n'
        "if _boom:\n"
        "    import trw_mcp.tools._before_edit_hint_core as _m\n"
        "\n"
        "    class _FakeLearning:\n"
        "        def __init__(self, summary):\n"
        "            self.summary = summary\n"
        "\n"
        "    class _FakeResult:\n"
        "        def __init__(self):\n"
        '            self.distill_status = "no_sidecar"\n'
        "            self.distill_hint = None\n"
        # Every BeforeEditHintResult field the hook reads (distill_as_of since 8.2 S3):
        # a fake missing one raises AttributeError on the FIRST file and masks the
        # isolation this test exists to prove.
        "            self.distill_as_of = None\n"
        # distill_action: read by the once-per-session sidecar remedy line the hook now prints.
        "            self.distill_action = None\n"
        '            self.learnings = [_FakeLearning("a fake recorded learning")]\n'
        "\n"
        "    def _wrapped(*, file_path, **kwargs):\n"
        "        if file_path == _boom:\n"
        '            raise RuntimeError("boom-for-test")\n'
        "        return _FakeResult()\n"
        "\n"
        "    _m.compute_before_edit_hint = _wrapped\n"
    )

    project = tmp_path / "project"
    project.mkdir()
    (project / ".trw" / "channels").mkdir(parents=True, exist_ok=True)
    (project / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    (project / ".trw" / "channels" / "cc03-python.txt").write_text(sys.executable, encoding="utf-8")

    command = "*** Begin Patch\n*** Add File: safe0.py\n+x = 1\n*** Add File: boom.py\n+x = 2\n*** End Patch\n"
    result = subprocess.run(
        ["sh", str(deploy_distill_hint(project))],
        input=json.dumps({"tool_use_id": "call-boom", "tool_name": "apply_patch", "tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TRW_PROJECT_DIR": str(project),
            "HOME": str(project),
            "PYTHONPATH": os.pathsep.join([str(shim_dir), CHECKOUT_PYTHONPATH]),
            "TRW_TEST_CC03_BOOM_FILE": "boom.py",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout, "expected an additionalContext hint despite the later file raising"
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "safe0.py" in context and "a fake recorded learning" in context, (
        f"the hint completed for safe0.py before boom.py raised must still be emitted; got: {context!r}"
    )
