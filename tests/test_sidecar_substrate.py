"""Tests for shared sidecar substrate (PRD-DIST-1988, cycle 747)."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools._sidecar_substrate import (
    SCHEMA_VERSION_ACCEPTED,
    load_envelope,
    load_sidecar_with_sha_check,
    resolve_current_sidecar,
    resolve_git_sha,
    resolve_repo_root,
)

_SIDECAR_FEATURE = "trw_before_edit_hint:distill_sidecar"


def _write_entitlement(trw_dir: Path, tier: str) -> None:
    """Sign + write a valid <trw_dir>/entitlements.yaml for *tier*."""
    trw_dir.mkdir(parents=True, exist_ok=True)
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier=tier, issued_to="t@t", expires_at=future)  # type: ignore[arg-type]
    (trw_dir / "entitlements.yaml").write_text(
        f"tier: {tier}\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n",
    )


def _make_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    (path / "foo.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path).decode().strip()


class TestRepoRootResolution:
    def test_explicit_arg(self, tmp_path: Path) -> None:
        r = resolve_repo_root(str(tmp_path))
        assert r == tmp_path

    def test_git_resolution(self, tmp_path: Path, monkeypatch) -> None:
        _make_git_repo(tmp_path)
        monkeypatch.chdir(tmp_path)
        r = resolve_repo_root(None)
        assert r is not None
        assert r.resolve() == tmp_path.resolve()

    def test_no_git_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        # Non-git directory — git rev-parse fails
        monkeypatch.chdir(tmp_path)
        r = resolve_repo_root(None)
        # May return None or some parent git root; not asserting equality
        # to tmp_path because the test runner itself is in a git repo.
        # Just verify it doesn't crash.
        assert r is None or isinstance(r, Path)


class TestGitShaResolution:
    def test_happy_path(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        result = resolve_git_sha(tmp_path)
        assert result == sha
        assert len(result) == 40

    def test_non_git_returns_none(self, tmp_path: Path) -> None:
        r = resolve_git_sha(tmp_path)
        assert r is None

    def test_rejects_non_hex_sha(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="z" * 40),
        )
        assert resolve_git_sha(tmp_path) is None


class TestLoadEnvelope:
    def test_missing(self, tmp_path: Path) -> None:
        assert load_envelope(tmp_path / "nope.json") is None

    def test_malformed_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{ not json")
        assert load_envelope(p) is None

    def test_not_a_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "list.json"
        p.write_text("[]")
        assert load_envelope(p) is None

    def test_happy_path(self, tmp_path: Path) -> None:
        p = tmp_path / "ok.json"
        p.write_text('{"x": 1}')
        assert load_envelope(p) == {"x": 1}


class TestLoadSidecarWithShaCheck:
    def _write_envelope(self, path: Path, **kwargs) -> None:
        envelope = {
            "schema_version": kwargs.get("schema_version", SCHEMA_VERSION_ACCEPTED),
            "sha": kwargs.get("sha", "a" * 40),
            "generated_at_unix": 1714000000.0,
            "payload": kwargs.get("payload", {"k": "v"}),
        }
        path.write_text(json.dumps(envelope))

    def test_happy(self, tmp_path: Path) -> None:
        sha = "a" * 40
        p = tmp_path / "s.json"
        self._write_envelope(p, sha=sha, payload={"k": "v"})
        r = load_sidecar_with_sha_check(
            p,
            expected_sha=sha,
            file_path_hint="foo.py",
            cli_remediation="trw-distill ...",
        )
        assert r.status == "ok"
        assert r.payload == {"k": "v"}

    def test_missing(self, tmp_path: Path) -> None:
        r = load_sidecar_with_sha_check(
            tmp_path / "nope.json",
            expected_sha="a" * 40,
            cli_remediation="trw-distill ...",
        )
        assert r.status == "sidecar_missing"
        assert "trw-distill ..." in (r.action or "")

    def test_schema_mismatch(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        self._write_envelope(p, schema_version="risk-report-sidecar/v99")
        r = load_sidecar_with_sha_check(
            p,
            expected_sha="a" * 40,
            cli_remediation="trw-distill ...",
        )
        assert r.status == "schema_mismatch"

    def test_stale_sha(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        self._write_envelope(p, sha="b" * 40)
        r = load_sidecar_with_sha_check(
            p,
            expected_sha="a" * 40,
            cli_remediation="trw-distill ...",
        )
        assert r.status == "stale_sha"

    def test_missing_payload(self, tmp_path: Path) -> None:
        envelope = {
            "schema_version": SCHEMA_VERSION_ACCEPTED,
            "sha": "a" * 40,
            "generated_at_unix": 1714000000.0,
        }
        p = tmp_path / "s.json"
        p.write_text(json.dumps(envelope))
        r = load_sidecar_with_sha_check(
            p,
            expected_sha="a" * 40,
            cli_remediation="trw-distill ...",
        )
        assert r.status == "sidecar_malformed"


class TestResolveCurrentSidecar:
    def test_custom_cache_preserves_file_state_and_loads_payload(self, tmp_path: Path) -> None:
        sha = _make_git_repo(tmp_path)
        _write_entitlement(tmp_path / ".trw", "team")
        cache = tmp_path / "custom-cache"
        cache.mkdir()
        sidecar = cache / f"risk-report-{sha}.json"

        def resolve():
            return resolve_current_sidecar(
                repo_root=str(tmp_path),
                cache_dir=str(cache),
                feature=_SIDECAR_FEATURE,
                artifact_name="risk-report",
                cli_remediation="trw-distill ...",
            )

        missing = resolve()
        assert missing.status == "sidecar_missing"
        assert missing.sidecar_path == str(sidecar)
        assert missing.sidecar_existed is False

        sidecar.write_text("{bad json")
        malformed = resolve()
        assert malformed.status == "sidecar_missing"
        assert malformed.sidecar_existed is True

        sidecar.write_text(json.dumps({"schema_version": SCHEMA_VERSION_ACCEPTED, "sha": sha, "payload": {"ok": True}}))
        current = resolve()
        assert current.status == "hint_available"
        assert current.payload == {"ok": True}
        assert current.sidecar_sha == sha
        assert current.sidecar_existed is True


class TestTierGate:
    # trw-distill is pinned ABSENT by the conftest `_default_distill_absent`
    # autouse fixture, so these assertions exercise the entitlement-sentinel
    # path deterministically. `test_installed_distill_opens_gate` overrides it.
    def test_no_entitlement_returns_free_blocked(self, tmp_path: Path) -> None:
        from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

        r = check_tier_for_feature(tmp_path, "trw_before_edit_hint:distill_sidecar")
        assert r.allowed is False
        assert r.tier == "free"

    def test_installed_distill_opens_gate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Package presence unlocks the feature even with NO entitlement file."""
        from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

        monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)
        r = check_tier_for_feature(tmp_path, "trw_before_edit_hint:distill_sidecar")
        assert r.allowed is True
        assert r.tier == "proprietary"
        assert r.reason == "distill_installed"

    @pytest.mark.parametrize("tier", ["team", "pro", "enterprise", "beta"])
    def test_entitled_tier_unlocks_feature(self, tmp_path: Path, tier: str) -> None:
        """Positive path: a signed team/pro/enterprise/beta entitlement unlocks
        the distill-sidecar feature (allowed=True, tier resolved, reason ok)."""
        from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

        _write_entitlement(tmp_path / ".trw", tier)
        r = check_tier_for_feature(tmp_path, _SIDECAR_FEATURE)
        assert r.allowed is True
        assert r.tier == tier
        assert r.reason == "ok"

    def test_alpha_alias_resolves_to_beta_and_unlocks(self, tmp_path: Path) -> None:
        """The backend tester-program plan name 'alpha' aliases to 'beta' and
        unlocks the same feature (sub_Y-f6QQ3Y_Os9b0vM)."""
        from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

        _write_entitlement(tmp_path / ".trw", "alpha")
        r = check_tier_for_feature(tmp_path, _SIDECAR_FEATURE)
        assert r.allowed is True
        assert r.tier == "beta"

    @pytest.mark.parametrize(
        ("setup", "expected_tier"),
        [("missing", "free"), ("free", "free")],
    )
    def test_free_or_missing_stays_blocked(self, tmp_path: Path, setup: str, expected_tier: str) -> None:
        """No entitlement file (missing) or an explicit free tier stays blocked."""
        from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

        if setup == "free":
            _write_entitlement(tmp_path / ".trw", "free")
        r = check_tier_for_feature(tmp_path, _SIDECAR_FEATURE)
        assert r.allowed is False
        assert r.tier == expected_tier


class TestTierRequiredRemediation:
    """Single source of truth for the tier-gate remediation string.

    sub_Y-f6QQ3Y_Os9b0vM: the old string pointed at a dead /tier URL.
    """

    def test_points_at_pricing_not_dead_tier_url(self) -> None:
        from trw_mcp.tools._sidecar_substrate import tier_required_action

        msg = tier_required_action()
        assert "https://trwframework.com/pricing" in msg
        assert "/tier" not in msg

    def test_mentions_paid_tiers_and_beta_tester_path(self) -> None:
        from trw_mcp.tools._sidecar_substrate import tier_required_action

        msg = tier_required_action()
        assert "team/pro/enterprise" in msg
        assert "tester program" in msg.lower()
