"""PRD-CORE-340 FR11/FR12: the one experimental switch, the banner and the time-box.

Every factory entry point is driven through its public seam in each state
(disabled, enabled, overdue, config error) with before/after filesystem
snapshots; ordinary checkpoint/build/formation behaviour must not change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from trw_mcp.formation import FormationError, add_orchestrator, create
from trw_mcp.state import _factory_experiment as fx

SUBJECT = "a" * 40
BEFORE = datetime(2026, 12, 27, 23, 59, 59, tzinfo=timezone.utc)
AFTER = datetime(2026, 12, 28, 0, 0, 0, tzinfo=timezone.utc)
STATES = ("disabled", "enabled", "overdue", "config_error")


def snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def set_state(project: Path, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """Put the process into one of the four gate states (clock injected for overdue)."""
    monkeypatch.setenv("HOME", str(project.parent))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.delenv("TRW_FACTORY_ENABLED", raising=False)
    monkeypatch.setattr(fx, "_utc_now", lambda: BEFORE if state != "overdue" else AFTER)
    if state == "config_error":
        (project / ".trw").mkdir(parents=True, exist_ok=True)
        (project / ".trw" / "config.yaml").write_text("factory_enabled: [not, a, bool\n", encoding="utf-8")
    elif state != "disabled":
        monkeypatch.setenv("TRW_FACTORY_ENABLED", "1")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    # gc.auto=0 / maintenance.auto=false: auto-maintenance leaves a transient .git/objects/maintenance.lock that a
    # before/after snapshot of the project dir (.git included) would see on Linux CI.
    for cmd in (
        ["init", "-q"],
        ["config", "user.email", "t@e.com"],
        ["config", "user.name", "T"],
        ["config", "gc.auto", "0"],
        ["config", "maintenance.auto", "false"],
    ):
        subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True)
    (repo / ".gitignore").write_text(".trw/\n", encoding="utf-8")
    (repo / "one.py").write_text("one = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, capture_output=True)
    return repo


@pytest.fixture
def run(project: Path) -> Path:
    path = project / ".trw" / "runs" / "task" / "run1"
    (path / "meta").mkdir(parents=True)
    (path / "meta" / "run.yaml").write_text("run_id: run1\nstatus: active\n", encoding="utf-8")
    (path / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return path


def _exit(fn: object, *args: object) -> int:
    with pytest.raises(SystemExit) as excinfo:
        fn(*args)  # type: ignore[operator]
    return int(excinfo.value.code)  # type: ignore[arg-type]


def _parse(*argv: str) -> argparse.Namespace:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    return _build_arg_parser().parse_args(list(argv))


def test_default_off_and_banner(
    project: Path, run: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.models.config._main import TRWConfig
    from trw_mcp.server._cli_factory import run_factory

    assert TRWConfig().factory_enabled is False
    # Strict round trip: yaml enables it, and an env var wins over yaml.
    set_state(project, monkeypatch, "disabled")
    assert fx.check().state == "disabled"
    (project / ".trw").mkdir(exist_ok=True)
    (project / ".trw" / "config.yaml").write_text("factory_enabled: true\n", encoding="utf-8")
    assert fx.check().state == "enabled"
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "0")
    assert fx.check().state == "disabled"
    (project / ".trw" / "config.yaml").unlink()

    before = snapshot(project)
    set_state(project, monkeypatch, "disabled")
    code = _exit(run_factory, _parse("factory", "status", "--run", str(run)))
    out = capsys.readouterr()
    assert code == 2 and "factory_disabled" in out.out + out.err and fx.BANNER not in out.out
    assert snapshot(project) == before

    set_state(project, monkeypatch, "enabled")
    code = _exit(run_factory, _parse("factory", "status", "--run", str(run), "--now", "2026-01-01T00:00:00+00:00"))
    lines = capsys.readouterr().out.splitlines()
    assert code == 0 and lines[0] == fx.BANNER
    assert fx.BANNER == (
        "EXPERIMENTAL (Alpha): trw-software-factory may change or be removed without notice; "
        "stable surfaces must not depend on it. Expires 2026-12-27."
    )
    for limit in fx.LIMITATIONS:
        assert any(limit in line for line in lines[:12])
    assert snapshot(project) == before  # status writes nothing


def test_expiry_and_exit_record(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_state(project, monkeypatch, "enabled")
    rec = fx.EXPERIMENT
    assert (rec.owner, rec.tier, rec.start, rec.expiry) == ("operator", "Alpha", date(2026, 9, 28), date(2026, 12, 27))
    assert "receiver-evidenced USED" in rec.exit_criterion
    assert fx.check(BEFORE).enabled
    overdue = fx.check(AFTER)
    assert overdue.state == "overdue" and overdue.exit_code == 3 and "EXPERIMENT OVERDUE" in overdue.message
    mountain = timezone(timedelta(hours=-7))
    assert fx.check(datetime(2026, 12, 27, 16, 59, 59, tzinfo=mountain)).enabled  # 23:59:59Z
    assert fx.check(datetime(2026, 12, 27, 17, 0, 0, tzinfo=mountain)).state == "overdue"  # 00:00:00Z
    assert fx.check(datetime(2026, 12, 28, 0, 0, tzinfo=timezone.utc)).state == "overdue"
    # A naive clock and every malformed record fail closed.
    assert fx.check(datetime(2026, 10, 1)).state == "config_error"  # noqa: DTZ001  # naive on purpose
    good = fx.EXPERIMENT
    for bad in (
        fx.ExperimentRecord("", "Alpha", good.start, good.expiry, good.exit_criterion),
        fx.ExperimentRecord("operator", "Beta", good.start, good.expiry, good.exit_criterion),
        fx.ExperimentRecord("operator", "Alpha", good.expiry, good.start, good.exit_criterion),
        fx.ExperimentRecord("operator", "Alpha", good.start, good.expiry, "  "),
    ):
        gate = fx.check(BEFORE, record=bad)
        assert gate.state == "config_error" and gate.exit_code == 2 and not gate.enabled
    # Disabled stays disabled when overdue.
    set_state(project, monkeypatch, "disabled")
    assert fx.check(AFTER).state == "disabled"


@pytest.mark.parametrize("state", STATES)
def test_receipt_verify_cli_and_service(
    state: str, project: Path, run: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.tools._evidence_receipts import VerificationReceiptRefusedError, record_verification_receipt
    from trw_mcp.tools._receipt_cli import run_receipt

    set_state(project, monkeypatch, state)
    before = snapshot(project)
    argv = ["receipt", "verify", "--run", str(run), "--subject", SUBJECT, "--check", "c", "--exit-code", "0"]
    argv += ["--evidence", "one.py", "--json"]
    if state == "enabled":
        assert _exit(run_receipt, _parse(*argv)) == 0
        assert len(list((run / "meta" / "receipts" / "verification").glob("*.json"))) == 1
        return
    expected = {"disabled": (2, "factory_disabled"), "overdue": (3, "experiment_overdue")}.get(
        state, (2, "factory_config_error")
    )
    assert _exit(run_receipt, _parse(*argv)) == expected[0]
    assert json.loads(capsys.readouterr().out)["error"] == expected[1]
    with pytest.raises(VerificationReceiptRefusedError) as excinfo:  # a direct import cannot bypass the gate
        record_verification_receipt(
            run, subject=SUBJECT, check="c", exit_code=0, evidence_paths=["one.py"], project_root=project
        )
    assert excinfo.value.reason_code == expected[1]
    assert snapshot(project) == before


@pytest.mark.parametrize("state", STATES)
def test_factory_checkpoint_branch(state: str, project: Path, run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    set_state(project, monkeypatch, state)
    factory = json.dumps({"factory": 1, "kind": "START", "attempt": "a1"})
    before = snapshot(project)
    result = execute_checkpoint(str(run), factory, None)
    if state == "enabled":
        assert result["recorded"] is True and snapshot(project) != before
        return
    reason = {"disabled": "factory_disabled", "overdue": "experiment_overdue"}.get(state, "factory_config_error")
    assert result["recorded"] is False and result["reason"] == reason
    assert snapshot(project) == before
    # Ordinary checkpoints, including prose that merely mentions the word, are unchanged in every state.
    for message in ("finished the parser", '{"factory": 2, "note": "other discriminator"}', "factory 1 done"):
        ordinary = execute_checkpoint(str(run), message, None)
        assert ordinary["recorded"] is True and ordinary["status"] == "checkpoint_created"


def test_factory_checkpoint_with_slice_done_is_refused_before_writes(
    project: Path, run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    set_state(project, monkeypatch, "enabled")
    factory = json.dumps({"factory": 1, "kind": "START", "attempt": "a1"})
    before = snapshot(project)
    result = execute_checkpoint(str(run), factory, None, slice_done="S1")
    assert result["recorded"] is False
    assert result["error_type"] == "factory_slice_done_conflict"
    assert snapshot(project) == before
    # Factory without slice_done and plain with slice_done both still record.
    assert execute_checkpoint(str(run), factory, None)["recorded"] is True
    assert execute_checkpoint(str(run), "finished the parser", None, slice_done="S1")["recorded"] is True


@pytest.mark.parametrize("state", STATES)
def test_formation_orchestrator_member(
    state: str, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._orchestration_formation import create_formation

    env = formation_env
    set_state(env.project_root, monkeypatch, state)
    before = snapshot(env.project_root)
    # Default init (no explicit opt-in): the member only when enabled; otherwise exactly as before FR18.
    manifest = create_formation(env.orchestrator_run, env.payload(), None, pin_key="orch-pin")
    ids = [m.member_id for m in manifest.members]
    assert ("orchestrator" in ids) is (state == "enabled")
    assert {"impl-1", "impl-2"} <= set(ids)
    # Explicit request or the later addition: refused before any mutation when not enabled.
    other = env.member_runs["impl-1"]
    if state != "enabled":
        after_init = snapshot(env.project_root)
        with pytest.raises(FormationError, match=r"factory_|EXPERIMENT OVERDUE"):
            add_orchestrator(
                manifest.formation_id, env.orchestrator_run, "lead", pin_key="orch-pin", trw_dir=env.trw_dir
            )
        with pytest.raises(FormationError, match=r"factory_|EXPERIMENT OVERDUE"):
            create(other, env.payload(formation_id="x"), trw_dir=env.trw_dir, orchestrator_member_id="lead")
        assert snapshot(env.project_root) == after_init
    assert before != snapshot(env.project_root)  # the ordinary init itself did write


def test_factory_tests_pass_with_the_clock_after_expiry(tmp_path: Path) -> None:
    """The factory tests pin their own clock: a real clock past the expiry must not break them.

    A plugin forces ``_utc_now`` to 2027-01-01 at session start; the conftest autouse
    fixture re-pins it inside the window for every test, so the suite stays green.
    """
    import os
    import sys

    (tmp_path / "force_late_clock.py").write_text(
        "from datetime import datetime, timezone\n"
        "\n"
        "def pytest_configure(config):\n"
        "    from trw_mcp.state import _factory_experiment as fx\n"
        "    fx._utc_now = lambda: datetime(2027, 1, 1, tzinfo=timezone.utc)\n",
        encoding="utf-8",
    )
    tests_dir = Path(__file__).resolve().parent
    targets = [
        "test_verification_receipt.py",
        "test_orchestration_init_advanced.py",
        "comms/test_formation_orchestrator_member.py",
    ]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "force_late_clock", "-p", "no:cacheprovider"]
        + [str(tests_dir / t) for t in targets],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tests_dir.parent),
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]


# --- an unrelated bad key must not disable the factory (stable regression after promotion #4) -----------------------


def _config(project: Path, text: str) -> None:
    (project / ".trw").mkdir(parents=True, exist_ok=True)
    (project / ".trw" / "config.yaml").write_text(text, encoding="utf-8")


def _isolate(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(project.parent))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.delenv("TRW_FACTORY_ENABLED", raising=False)
    monkeypatch.setattr(fx, "_utc_now", lambda: BEFORE)


_DUPLICATE_UNRELATED = (
    "cc03_hook_enabled: false\nfactory_enabled: {value}\nother: 1\ncc03_hook_enabled: true\n"  # a CONFLICTING repeat
)


@pytest.mark.parametrize(("value", "state"), [("true", "enabled"), ("false", "disabled")])
def test_a_duplicate_unrelated_key_does_not_change_the_switch(
    project: Path, monkeypatch: pytest.MonkeyPatch, value: str, state: str
) -> None:
    from structlog.testing import capture_logs

    _isolate(project, monkeypatch)
    _config(project, _DUPLICATE_UNRELATED.format(value=value))
    with pytest.raises(Exception) as strict:  # non-vacuity: the general cascade really does reject this file
        from trw_mcp.models.config._loader import resolve_config_overrides

        resolve_config_overrides(project / ".trw" / "config.yaml", apply_env_exclusion=False)
    assert "duplicate" in str(strict.value).lower()

    with capture_logs() as logs:
        gate = fx.check()

    assert gate.state == state
    assert any(entry["event"] == "factory_switch_read_alone" and entry["cascade_failure"] for entry in logs), logs


def test_a_duplicate_unrelated_key_with_no_switch_is_off_not_an_error(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(project, monkeypatch)
    _config(project, "cc03_hook_enabled: false\ncc03_hook_enabled: true\n")
    assert fx.check().state == "disabled"


def test_the_environment_switch_still_wins_over_a_broken_config(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(project, monkeypatch)
    _config(project, _DUPLICATE_UNRELATED.format(value="false"))
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "1")
    assert fx.check().state == "enabled"
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "off")
    assert fx.check().state == "disabled"
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "maybe")
    gate = fx.check()
    assert gate.state == "config_error" and "TRW_FACTORY_ENABLED is not a boolean" in gate.detail


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("factory_enabled: true\nfactory_enabled: false\nx: 1\nx: 2\n", "both true and false"),
        ("factory_enabled: [not, a, bool\nx: 1\nx: 2\n", "not a plain true/false"),
        ("factory_enabled: yes-please\nx: 1\nx: 2\n", "not a plain true/false"),
        ("factory_enabled: true\n'factory_enabled': false\nx: 1\nx: 2\n", "not a plain true/false"),
        ('"factory_enabled": true\nfactory_enabled: false\nx: 1\nx: 2\n', "not a plain true/false"),
    ],
)
def test_an_ambiguous_or_malformed_switch_is_still_a_named_config_error(
    project: Path, monkeypatch: pytest.MonkeyPatch, text: str, fragment: str
) -> None:
    _isolate(project, monkeypatch)
    _config(project, text)
    gate = fx.check()
    assert gate.state == "config_error"
    assert fragment in gate.detail and "StateError" in gate.detail, gate.detail


def test_the_refusal_detail_never_echoes_config_content(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(project, monkeypatch)
    _config(project, "platform_api_key: sk-SECRET-VALUE\nplatform_api_key: sk-SECRET-VALUE\nfactory_enabled: maybe\n")
    gate = fx.check()
    assert gate.state == "config_error"
    assert "SECRET" not in gate.detail and "SECRET" not in gate.message


def test_a_factory_checkpoint_records_despite_an_unrelated_duplicate_key(
    project: Path, run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    _isolate(project, monkeypatch)
    _config(project, _DUPLICATE_UNRELATED.format(value="true"))
    result = execute_checkpoint(str(run), json.dumps({"factory": 1, "kind": "START", "attempt": "a1"}), None)
    assert result["recorded"] is True, result
