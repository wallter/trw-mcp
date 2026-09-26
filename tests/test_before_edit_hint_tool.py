"""Tests for trw_code(mode="hint") (PRD-DIST-1983..1986, cycle 746; retargeted PRD-CORE-300)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
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
        real_path_open, real_open = Path.open, builtins.open

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

        monkeypatch.setattr(Path, "open", path_open)
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
        assert response["status"] == "ok"
        assert response["count"] == 1
        hint = response["hints"][0]
        assert set(hint) >= {"file_path", "learnings", "learnings_count", "distill_status", "tier"}
        events = "".join(p.read_text() for p in (project / ".trw").rglob("*events*.jsonl"))
        assert "trw_code" in events
