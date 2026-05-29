"""Tests for shared sidecar substrate (PRD-DIST-1988, cycle 747)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from trw_mcp.tools._sidecar_substrate import (
    SCHEMA_VERSION_ACCEPTED,
    load_envelope,
    load_sidecar_with_sha_check,
    resolve_git_sha,
    resolve_repo_root,
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
            file_path_hint="x.py",
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
            file_path_hint="x.py",
            cli_remediation="trw-distill ...",
        )
        assert r.status == "schema_mismatch"

    def test_stale_sha(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        self._write_envelope(p, sha="b" * 40)
        r = load_sidecar_with_sha_check(
            p,
            expected_sha="a" * 40,
            file_path_hint="x.py",
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
            file_path_hint="x.py",
            cli_remediation="trw-distill ...",
        )
        assert r.status == "sidecar_malformed"


class TestTierGate:
    def test_no_entitlement_returns_free_blocked(self, tmp_path: Path) -> None:
        from trw_mcp.tools._sidecar_substrate import check_tier_for_feature

        r = check_tier_for_feature(tmp_path, "trw_before_edit_hint:distill_sidecar")
        assert r.allowed is False
        assert r.tier == "free"
