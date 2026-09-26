"""SAFE-001 operator surfaces: ``trw-mcp telemetry classify`` (PRD-CORE-300
slice S3a) and ``trw-mcp meta-tune propose|rollback`` (PRD-CORE-300-FR04)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import reload_config
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def _cli(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *, group: str = "meta-tune"
) -> tuple[int, dict]:
    from trw_mcp.server._cli import main

    monkeypatch.setattr(sys, "argv", ["trw-mcp", group, *argv, "--json"])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    return code, json.loads(capsys.readouterr().out)


def _promoted_proposal(tmp_path: Path) -> tuple[Path, Path]:
    state_dir = tmp_path / "state"
    live_file = tmp_path / "CLAUDE.md"
    backup_file = tmp_path / "backup.md"
    live_file.write_text("mutated")
    backup_file.write_text("original")
    state_dir.mkdir()
    (state_dir / "prop-1.json").write_text(
        json.dumps(
            {
                "proposal_id": "prop-1",
                "target_path": str(live_file),
                "backup_path": str(backup_file),
                "promotion_ts": datetime.now(timezone.utc).isoformat(),
                "promotion_session_id": "sess-1",
            }
        )
    )
    return state_dir, live_file


def test_classify_command_is_the_only_meta_tune_ops_cli_surface(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """PRD-CORE-300 slice S3a: the former classify tool moved to a CLI verb."""
    code, result = _cli(["classify", "--path", "x"], monkeypatch, capsys, group="telemetry")
    assert code == 0
    assert set(result) == {"path", "classification", "surfaces", "rationale"}


def test_classify_command_reports_control_for_trw_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    code, result = _cli(["classify", "--path", ".trw/config.yaml"], monkeypatch, capsys, group="telemetry")

    assert code == 0
    assert result["classification"] == "control"
    assert "config" in result["surfaces"]


def test_rollback_restores_target_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir, live_file = _promoted_proposal(tmp_path)
    # The kill switch must be enabled in config; an audit log path never enables it.
    reload_config(TRWConfig(meta_tune=MetaTuneConfig(enabled=True)))
    try:
        code, result = _cli(
            ["rollback", "--proposal-id", "prop-1", "--state-dir", str(state_dir),
             "--audit-log-path", str(tmp_path / "audit.jsonl")],
            monkeypatch,
            capsys,
        )  # fmt: skip
    finally:
        reload_config(None)

    assert code == 0
    assert result["status"] == "rolled_back"
    assert live_file.read_text() == "original"


def test_rollback_stays_disabled_when_kill_switch_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SAFE-001 FR-7/FR-13: an audit log path never overrides the global kill switch."""
    state_dir, live_file = _promoted_proposal(tmp_path)
    reload_config(TRWConfig(meta_tune=MetaTuneConfig(enabled=False)))
    try:
        code, result = _cli(
            ["rollback", "--proposal-id", "prop-1", "--state-dir", str(state_dir),
             "--audit-log-path", str(tmp_path / "audit.jsonl")],
            monkeypatch,
            capsys,
        )  # fmt: skip
    finally:
        reload_config(None)

    assert code == 0
    assert (result["status"], result["reason"]) == ("disabled", "kill_switch_off")
    assert live_file.read_text() == "mutated"


def test_rollback_error_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "prop-1.json").write_text("{not json")
    reload_config(TRWConfig(meta_tune=MetaTuneConfig(enabled=True)))
    try:
        code, result = _cli(["rollback", "--proposal-id", "prop-1", "--state-dir", str(state_dir)], monkeypatch, capsys)
    finally:
        reload_config(None)

    assert result["status"] == "error"
    assert code == 1


@pytest.mark.parametrize("source", ["content", "file"])
def test_propose_dispatches_to_the_real_entrypoint(
    source: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.meta_tune import promote

    target = tmp_path / "CLAUDE.md"
    target.write_text("before")
    candidate = tmp_path / "candidate.md"
    candidate.write_text("after")
    captured: dict[str, object] = {}

    class _FakeResult:
        def model_dump(self) -> dict[str, object]:
            return {"decision": "approve"}

    def _fake_promote_candidate(**kwargs: object) -> object:
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(promote, "promote_candidate", _fake_promote_candidate)
    candidate_args = ["--candidate-content", "after"] if source == "content" else ["--candidate-file", str(candidate)]
    reload_config(TRWConfig(meta_tune=MetaTuneConfig(enabled=True, audit_log_path=str(tmp_path / "audit.jsonl"))))
    try:
        code, result = _cli(
            ["propose", "--target-path", str(target), *candidate_args, "--proposer-id", "agent-1",
             "--sandbox-command", "python -c \"print('ok')\"", "--reviewer-id", "alice",
             "--approval-ts", "2026-04-24T00:00:00Z", "--declared-metric-delta", "0.25"],
            monkeypatch,
            capsys,
        )  # fmt: skip
    finally:
        reload_config(None)

    assert code == 0
    assert result["decision"] == "approve"
    assert captured["target_path"] == target
    assert captured["candidate_content"] == "after"
    assert captured["reviewer_id"] == "alice"
    assert captured["sandbox_command"] == ["python", "-c", "print('ok')"]
    assert captured["approval_ts"] == datetime(2026, 4, 24, tzinfo=timezone.utc)
    assert captured["declared_metric_delta"] == 0.25


def test_meta_tune_on_a_non_linux_host_warns_and_stays_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI reads the resolved config, so the Linux-only platform gate still applies."""
    from unittest.mock import patch

    state_dir, live_file = _promoted_proposal(tmp_path)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_META_TUNE_ENABLED", "true")
    monkeypatch.setattr("trw_mcp.models.config._loader.platform.system", lambda: "Darwin")
    reload_config(None)
    try:
        with patch("trw_mcp.models.config._loader.logger") as log:
            code, result = _cli(
                ["rollback", "--proposal-id", "prop-1", "--state-dir", str(state_dir)], monkeypatch, capsys
            )
    finally:
        reload_config(None)

    assert log.warning.call_args.args[0] == "meta_tune_disabled_unsupported_platform"
    assert result["status"] == "disabled"
    assert code == 0
    assert live_file.read_text() == "mutated"
