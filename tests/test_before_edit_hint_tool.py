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
        monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.resolve_repo_root", lambda _root: None)
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

    def test_stale_batch_does_not_answer(self, tmp_path: Path) -> None:
        """A batch artifact from an older commit is not a fallback.

        Non-vacuity for the sha check: without it this test's batch file would
        satisfy the lookup and a hint from a previous commit's map would be
        served as current.
        """
        sha = _make_git_repo(tmp_path)
        cache_dir = tmp_path / ".trw" / "distill" / "map-cache"
        _write_batch_sidecar(cache_dir, "0" * 40, ["a.py"])
        _write_entitlement(tmp_path / ".trw", "pro")
        assert (cache_dir / f"before-edit-batch-{'0' * 40}.json").exists()
        assert not (cache_dir / f"before-edit-batch-{sha}.json").exists()

        result = compute_before_edit_hint(file_path="a.py", repo_root=str(tmp_path))

        assert result.distill_status == "sidecar_missing"
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
