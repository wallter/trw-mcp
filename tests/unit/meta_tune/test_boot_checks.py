"""Tests for meta_tune.boot_checks — PRD-HPO-SAFE-001 FR-13 / FR-15."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.meta_tune.boot_checks import (
    audit_defaults,
    resolve_kill_switch_path,
    validate_defaults,
)
from trw_mcp.meta_tune.errors import (
    KillSwitchNotFoundError,
    MetaTuneBootValidationError,
)
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _enabled_config(tmp_path: Path) -> TRWConfig:
    fixture_root = Path(__file__).resolve().parents[2] / "fixtures" / "meta_tune"
    return TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            kill_switch_path=".trw/config.yaml",
            audit_log_path=".trw/meta_tune/meta_tune_audit.jsonl",
            corpus_path=str(fixture_root / "corpora"),
            eval_gaming_fixture_path=str(fixture_root / "dgm_attacks"),
        )
    )


def test_audit_defaults_returns_report_structure(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".trw").mkdir(parents=True)
    (repo_root / ".trw" / "config.yaml").write_text("meta_tune:\n  enabled: false\n")
    corpus = repo_root / "fixtures" / "corpora" / "v1"
    corpus.mkdir(parents=True)
    (corpus / "task.txt").write_text("ok")
    fixtures = repo_root / "fixtures" / "dgm_attacks"
    fixtures.mkdir(parents=True)
    for i in range(5):
        (fixtures / f"{i}.yaml").write_text("name: attack\n")

    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            kill_switch_path=".trw/config.yaml",
            audit_log_path=".trw/meta_tune/meta_tune_audit.jsonl",
            corpus_path="fixtures/corpora",
            eval_gaming_fixture_path="fixtures/dgm_attacks",
        )
    )
    report = audit_defaults(cfg, repo_root=repo_root)
    assert "kill_switch_path" in report
    assert report["kill_switch_path"]["parent_writable"] is True
    assert "audit_log_path" in report
    assert "corpus_path" in report
    assert "eval_gaming_fixture_path" in report
    assert "sandbox" in report


def test_validate_defaults_passes_when_safe_defaults_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".trw").mkdir(parents=True)
    (repo_root / ".trw" / "config.yaml").write_text("meta_tune:\n  enabled: false\n")
    corpus = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "corpora" / "v1"
    corpus.mkdir(parents=True)
    (corpus / "task.txt").write_text("ok")
    fixtures = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "dgm_attacks"
    fixtures.mkdir(parents=True)
    for i in range(5):
        (fixtures / f"{i}.yaml").write_text("name: attack\n")

    cfg = _enabled_config(tmp_path)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._HAS_SECCOMP", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._IS_LINUX", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks.shutil.which", lambda _: "/usr/bin/unshare")

    validate_defaults(cfg, repo_root=repo_root)


def test_validate_defaults_raises_when_kill_switch_missing(tmp_path: Path) -> None:
    cfg = _enabled_config(tmp_path)
    with pytest.raises(MetaTuneBootValidationError) as ei:
        validate_defaults(cfg, repo_root=tmp_path)
    assert "kill_switch_path" in str(ei.value)


def test_validate_defaults_raises_when_fixture_count_too_low(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".trw").mkdir(parents=True)
    (repo_root / ".trw" / "config.yaml").write_text("meta_tune:\n  enabled: false\n")
    corpus = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "corpora" / "v1"
    corpus.mkdir(parents=True)
    (corpus / "task.txt").write_text("ok")
    fixtures = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "dgm_attacks"
    fixtures.mkdir(parents=True)
    (fixtures / "1.yaml").write_text("name: attack\n")

    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            kill_switch_path=".trw/config.yaml",
            audit_log_path=".trw/meta_tune/meta_tune_audit.jsonl",
            corpus_path="trw-mcp/tests/fixtures/meta_tune/corpora",
            eval_gaming_fixture_path="trw-mcp/tests/fixtures/meta_tune/dgm_attacks",
        )
    )
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._HAS_SECCOMP", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._IS_LINUX", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks.shutil.which", lambda _: "/usr/bin/unshare")

    with pytest.raises(MetaTuneBootValidationError) as ei:
        validate_defaults(cfg, repo_root=repo_root)
    assert "eval_gaming_fixture_path" in str(ei.value)


def test_validate_defaults_raises_for_unsupported_sandbox_image_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / ".trw").mkdir(parents=True)
    (repo_root / ".trw" / "config.yaml").write_text("meta_tune:\n  enabled: false\n")
    corpus = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "corpora" / "v1"
    corpus.mkdir(parents=True)
    (corpus / "task.txt").write_text("ok")
    fixtures = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "dgm_attacks"
    fixtures.mkdir(parents=True)
    for i in range(5):
        (fixtures / f"{i}.yaml").write_text("name: attack\n")

    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            sandbox_image_tag="weird-runtime",
            kill_switch_path=".trw/config.yaml",
            audit_log_path=".trw/meta_tune/meta_tune_audit.jsonl",
            corpus_path="trw-mcp/tests/fixtures/meta_tune/corpora",
            eval_gaming_fixture_path="trw-mcp/tests/fixtures/meta_tune/dgm_attacks",
        )
    )
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._HAS_SECCOMP", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._IS_LINUX", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks.shutil.which", lambda _: "/usr/bin/unshare")

    with pytest.raises(MetaTuneBootValidationError) as ei:
        validate_defaults(cfg, repo_root=repo_root)
    assert "sandbox" in str(ei.value)


def test_resolve_kill_switch_path_uses_upward_search(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    nested = repo_root / "trw-mcp" / "src"
    nested.mkdir(parents=True)
    (repo_root / ".trw").mkdir()
    (repo_root / ".trw" / "config.yaml").write_text("meta_tune:\n  enabled: false\n")

    resolved = resolve_kill_switch_path(
        MetaTuneConfig(kill_switch_path=".trw/config.yaml"),
        cwd=nested,
    )
    assert resolved == repo_root / ".trw" / "config.yaml"


def test_resolve_kill_switch_path_raises_without_anchor(tmp_path: Path) -> None:
    with pytest.raises(KillSwitchNotFoundError):
        resolve_kill_switch_path(
            MetaTuneConfig(kill_switch_path=".trw/config.yaml"),
            cwd=tmp_path,
        )


def test_validate_defaults_is_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR-10: ≤2s wall-clock."""
    import time

    repo_root = tmp_path / "repo"
    (repo_root / ".trw").mkdir(parents=True)
    (repo_root / ".trw" / "config.yaml").write_text("meta_tune:\n  enabled: false\n")
    corpus = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "corpora" / "v1"
    corpus.mkdir(parents=True)
    (corpus / "task.txt").write_text("ok")
    fixtures = repo_root / "trw-mcp" / "tests" / "fixtures" / "meta_tune" / "dgm_attacks"
    fixtures.mkdir(parents=True)
    for i in range(5):
        (fixtures / f"{i}.yaml").write_text("name: attack\n")

    cfg = _enabled_config(tmp_path)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._HAS_SECCOMP", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._IS_LINUX", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks.shutil.which", lambda _: "/usr/bin/unshare")

    start = time.monotonic()
    validate_defaults(cfg, repo_root=repo_root)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0
