"""End-to-end SAFE-001 config resolution tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.meta_tune import dispatch
from trw_mcp.meta_tune.errors import MetaTuneBootValidationError
from trw_mcp.meta_tune.rollback import rollback_proposal
from trw_mcp.meta_tune.sandbox import SandboxResult
from trw_mcp.models.config import get_config, reload_config
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig
from trw_mcp.server import _app


def _project_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo_root = tmp_path / "repo"
    kill_switch = repo_root / ".trw" / "custom-meta-tune.yaml"
    audit_log = repo_root / "custom-audit" / "meta_tune.jsonl"
    corpus = repo_root / "fixtures" / "corpora" / "v1"
    fixtures = repo_root / "fixtures" / "dgm_attacks"
    kill_switch.parent.mkdir(parents=True)
    audit_log.parent.mkdir(parents=True)
    corpus.mkdir(parents=True)
    fixtures.mkdir(parents=True)
    kill_switch.write_text("meta_tune:\n  enabled: true\n", encoding="utf-8")
    (corpus / "task.txt").write_text("ok", encoding="utf-8")
    for idx in range(5):
        (fixtures / f"{idx}.yaml").write_text("name: attack\n", encoding="utf-8")
    return repo_root, kill_switch, audit_log


def _sandbox_success() -> SandboxResult:
    return SandboxResult(
        exit_code=0,
        stdout=json.dumps(
            {
                "declared_metric_delta": 0.2,
                "outcome_trace": [
                    {"task": "t1", "score": 0.4},
                    {"task": "t2", "score": 0.45},
                    {"task": "t3", "score": 0.55},
                    {"task": "t4", "score": 0.6},
                    {"task": "t5", "score": 0.7},
                ],
            }
        ),
        stderr="",
        wall_ms=10.0,
        rss_peak_mb=1.0,
        network_attempted=False,
        writes_outside_tmp=[],
        timed_out=False,
    )


def test_startup_validation_activates_from_legacy_flat_meta_tune_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root, kill_switch, _ = _project_layout(tmp_path)
    config_path = repo_root / ".trw" / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "meta_tune_enabled: true",
                "meta_tune:",
                f"  kill_switch_path: {kill_switch.relative_to(repo_root)}",
                "  audit_log_path: custom-audit/meta_tune.jsonl",
                "  corpus_path: fixtures/corpora",
                "  eval_gaming_fixture_path: fixtures/dgm_attacks",
            ]
        ),
        encoding="utf-8",
    )

    validate_calls: list[bool] = []
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: repo_root)
    monkeypatch.setattr(_app, "validate_meta_tune_defaults", lambda cfg: validate_calls.append(cfg.meta_tune.enabled))
    reload_config(None)
    try:
        cfg = get_config()
        _app._build_middleware()
    finally:
        reload_config(None)

    assert cfg.meta_tune_enabled is True
    assert cfg.meta_tune.enabled is True
    assert validate_calls == [True]


def test_promote_candidate_uses_non_default_timeout_and_audit_log_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root, _, audit_log = _project_layout(tmp_path)
    target = repo_root / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")
    captured_timeout: list[float] = []

    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            sandbox_timeout_seconds=1.25,
            audit_log_path=str(audit_log),
            corpus_path=str(repo_root / "fixtures" / "corpora"),
        )
    )

    def _fake_run_sandboxed(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured_timeout.append(kwargs["timeout_s"])
        return _sandbox_success()

    monkeypatch.setattr(dispatch, "run_sandboxed", _fake_run_sandboxed)

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-1",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused')"],
        _config=cfg,
        state_dir=repo_root / ".trw" / "meta_tune" / "state",
    )

    assert result.promoted is True
    assert captured_timeout == [1.25]
    assert audit_log.exists()


def test_promote_candidate_uses_non_default_consensus_quorum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root, _, audit_log = _project_layout(tmp_path)
    target = repo_root / "CLAUDE.md"
    target.write_text("before\n", encoding="utf-8")
    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            promotion_gate_consensus_quorum=5,
            audit_log_path=str(audit_log),
            corpus_path=str(repo_root / "fixtures" / "corpora"),
        )
    )

    monkeypatch.setattr(dispatch, "run_sandboxed", lambda *args, **kwargs: _sandbox_success())

    result = dispatch.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent-1",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "print('unused')"],
        _config=cfg,
        state_dir=repo_root / ".trw" / "meta_tune" / "state",
    )

    assert result.promoted is False
    assert result.decision == "needs_human_review"
    assert target.read_text(encoding="utf-8") == "before\n"


def test_rollback_uses_non_default_max_attempts(tmp_path: Path) -> None:
    repo_root, _, audit_log = _project_layout(tmp_path)
    state_dir = repo_root / ".trw" / "meta_tune" / "state"
    state_dir.mkdir(parents=True)
    live_file = repo_root / "CLAUDE.md"
    live_file.write_text("mutated\n", encoding="utf-8")
    snapshot = {
        "proposal_id": "prop-1",
        "target_path": str(live_file),
        "backup_path": str(repo_root / "missing.md"),
        "promotion_ts": datetime.now(timezone.utc).isoformat(),
        "promotion_session_id": "sess-1",
        "rollback_attempts": 0,
    }
    (state_dir / "prop-1.json").write_text(json.dumps(snapshot), encoding="utf-8")
    cfg = TRWConfig(
        meta_tune=MetaTuneConfig(
            enabled=True,
            rollback_max_attempts=1,
            audit_log_path=str(audit_log),
        )
    )

    first = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)
    second = rollback_proposal("prop-1", state_dir=state_dir, _config=cfg)

    assert first.status == "error"
    assert second.reason == "rollback_attempt_limit_exceeded"


def test_startup_validation_uses_custom_kill_switch_path_and_sandbox_image_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root, kill_switch, _ = _project_layout(tmp_path)
    config_path = repo_root / ".trw" / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "meta_tune:",
                "  enabled: true",
                f"  kill_switch_path: {kill_switch.relative_to(repo_root)}",
                "  audit_log_path: custom-audit/meta_tune.jsonl",
                "  corpus_path: fixtures/corpora",
                "  eval_gaming_fixture_path: fixtures/dgm_attacks",
                "  sandbox_image_tag: unsupported-runtime",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: repo_root)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._HAS_SECCOMP", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks._IS_LINUX", True)
    monkeypatch.setattr("trw_mcp.meta_tune.boot_checks.shutil.which", lambda _: "/usr/bin/unshare")
    reload_config(None)

    try:
        with pytest.raises(MetaTuneBootValidationError):
            _app._build_middleware()
    finally:
        reload_config(None)
