"""FR-01/FR-10 — ``trw-mcp probe run`` and ``trw-mcp probe budget`` (PRD-CORE-144, PRD-CORE-300-FR04).

Proves the probe command returns a real ProbeResult through the sandbox, the
budget is enforced across invocations of one run and observable, the budget
command is read-only, and an identical probe is served from the run's cache.
Each invocation is its own process in real use, so the run's budget and cache
persist between calls under ``.trw/runtime/probe/``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    # FR-06: probes are gated OFF by default; the flag-state tests override this.
    monkeypatch.setenv("TRW_PROBE_ENABLED", "1")
    return tmp_path


def _cli(argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    from trw_mcp.server._cli import main

    monkeypatch.setattr(sys, "argv", ["trw-mcp", *argv, "--json"])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    return code, json.loads(capsys.readouterr().out)


def _probe(run_id: str, *extra: str, command: str = "print('x')") -> list[str]:
    return [
        "probe",
        "run",
        "--hypothesis",
        "prints",
        "--command",
        f'{sys.executable} -c "{command}"',
        "--run-id",
        run_id,
        "--timeout-s",
        "10",
        *extra,
    ]


def _state_files(root: Path) -> list[Path]:
    return sorted((root / ".trw").rglob("*.json")) if (root / ".trw").exists() else []


def test_probe_run_returns_a_real_result(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = _cli(_probe("run-A"), monkeypatch, capsys)
    assert code == 0
    assert out["verdict"] == "supports"
    assert "x" in out["evidence"]["stdout"]


def test_budget_reconciles_with_usage_across_invocations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mode = ("--planning-mode", "TRIANGULATED")
    _cli(_probe("run-B", "--hypothesis-id", "H1", *mode), monkeypatch, capsys)

    code, snap = _cli(["probe", "budget", "--run-id", "run-B", *mode], monkeypatch, capsys)
    # FR-10 A1: counts reconciled with usage recorded by an earlier invocation.
    assert code == 0
    assert (snap["used"], snap["remaining"], snap["total"]) == (1, 1, 2)
    assert snap["by_hypothesis_id"] == {"H1": 1}


def test_budget_is_enforced_across_invocations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DUAL_DRAFT allows one probe per run; a second invocation of the same run is refused."""
    mode = ("--planning-mode", "DUAL_DRAFT")
    assert _cli(_probe("run-C", *mode, command="print(1)"), monkeypatch, capsys)[0] == 0

    code, out = _cli(_probe("run-C", *mode, command="print(2)"), monkeypatch, capsys)
    assert code == 1
    assert out["error"] == "probe_budget_exhausted"
    assert out["remaining"] == 0


def test_budget_exhaustion_returns_a_typed_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # DIRECT mode -> budget 0 -> the first probe is exhausted.
    code, out = _cli(_probe("run-D", "--planning-mode", "DIRECT"), monkeypatch, capsys)
    assert code == 1
    assert out["error"] == "probe_budget_exhausted"


def test_budget_on_an_unknown_run_is_read_only(
    _project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-10 — a budget query for a run that never probed writes nothing."""
    monkeypatch.setenv("TRW_PROBE_ENABLED", "0")  # the gate is irrelevant to status
    code, snap = _cli(
        ["probe", "budget", "--run-id", "never-probed", "--planning-mode", "TRIANGULATED"], monkeypatch, capsys
    )
    assert code == 0
    assert snap["used"] == 0
    assert snap["remaining"] == snap["total"] == 2
    assert snap["by_hypothesis_id"] == {}
    assert _state_files(_project) == []


def test_a_run_id_cannot_escape_the_state_directory(
    _project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _cli(_probe("../../escape"), monkeypatch, capsys)[0] == 0
    written = _state_files(_project)
    assert len(written) == 1
    assert written[0].parent == _project / ".trw" / "runtime" / "probe"


def test_probe_disabled_when_flag_off(
    _project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-06 — with TRW_PROBE_ENABLED off the command is inert: no spawn, no budget."""
    monkeypatch.setenv("TRW_PROBE_ENABLED", "0")
    code, out = _cli(_probe("run-OFF", command="print('NOPE')"), monkeypatch, capsys)
    assert code == 1
    assert out["error"] == "probe_disabled"
    assert "TRW_PROBE_ENABLED" in out["remediation"]
    assert _state_files(_project) == []


def test_a_validation_error_refunds_the_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mode = ("--planning-mode", "TRIANGULATED")
    code, out = _cli(_probe("run-V", "--timeout-s", "0", *mode), monkeypatch, capsys)
    assert code == 1
    assert out["error"] == "probe_validation_error"

    _, snap = _cli(["probe", "budget", "--run-id", "run-V", *mode], monkeypatch, capsys)
    assert snap["used"] == 0


def test_probe_event_published_to_the_telemetry_pipeline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-09 — a completed probe publishes a ProbeEvent through the pipeline."""
    import trw_mcp.telemetry.pipeline as pipeline_mod

    enqueued: list[dict[str, object]] = []

    class _FakePipeline:
        def enqueue(self, event: dict[str, object]) -> None:
            enqueued.append(event)

    monkeypatch.setattr(pipeline_mod.TelemetryPipeline, "get_instance", classmethod(lambda cls: _FakePipeline()))
    _cli(_probe("run-PUB", "--planning-mode", "TRIANGULATED"), monkeypatch, capsys)

    assert len(enqueued) == 1, enqueued
    event = enqueued[0]
    assert (event["event_type"], event["emitter"], event["run_id"]) == ("probe", "probe_harness", "run-PUB")
    payload = event["payload"]
    assert isinstance(payload, dict)
    assert payload["verdict"] == "supports"
    assert payload["decisive"] is True


def test_a_telemetry_failure_does_not_break_the_probe(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import trw_mcp.telemetry.pipeline as pipeline_mod

    def _boom(cls: object) -> object:
        raise RuntimeError("pipeline down")

    monkeypatch.setattr(pipeline_mod.TelemetryPipeline, "get_instance", classmethod(_boom))
    code, out = _cli(_probe("run-PUB-FAIL"), monkeypatch, capsys)
    assert code == 0
    assert out["verdict"] == "supports"


def test_an_identical_probe_is_served_from_the_run_cache_across_invocations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mode = ("--planning-mode", "TRIANGULATED")
    _, first = _cli(_probe("run-E", *mode, command="print('cached')"), monkeypatch, capsys)
    _, second = _cli(_probe("run-E", *mode, command="print('cached')"), monkeypatch, capsys)
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True

    _, snap = _cli(["probe", "budget", "--run-id", "run-E", *mode], monkeypatch, capsys)
    assert snap["used"] == 1  # the cache hit did not consume a second slot
