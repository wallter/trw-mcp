"""PRD-CORE-337 FR08 -- the migrated writers outside FR05's census trees, proven through their entry points.

``test_state_writers_use_safe_fs.py`` covers the state/ writers and extends the census to these
qualnames; this file proves the BEHAVIOUR of each one through the function production calls: the
meta-tune ``promote_candidate``, the ``audit``/``export`` CLI handlers, ``BatchSender.send``,
``run_post_commit`` (the git hook's entry), ``AnomalyDetector.observe`` (the security middleware's) and
``stale_advisory_first_time`` (what ``trw_status`` asks). For each: a symlink planted at the target
never has its target changed, and the refusal is handled the way the caller promises -- raised where
the caller surfaces it, logged and skipped where it must not raise; a plain target still gets the
expected content.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from trw_memory.exceptions import UnsafeWriteError

from tests._planted_symlink import OUTSIDE_BYTES, assert_untouched, plant_symlink
from trw_mcp.meta_tune import promote
from trw_mcp.meta_tune.sandbox import SandboxResult
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig
from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig, AnomalyObservation
from trw_mcp.server import _subcommands
from trw_mcp.state.analytics._stale_runs import stale_advisory_first_time
from trw_mcp.telemetry import sender as sender_module
from trw_mcp.telemetry.sender import BatchSender, stamp_consent
from trw_mcp.tools import _post_commit as pc

_NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def _refusal(call: Callable[[], object]) -> UnsafeWriteError | None:
    """Run *call*; the refusal it raised, or ``None``. Callers check the victim FIRST, so a write-through fails on bytes."""
    try:
        call()
    except UnsafeWriteError as exc:
        return exc
    return None


# --- meta_tune.promote.promote_candidate (R12 rows 5, 6) --------------------------------------------


def _promote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: Path) -> promote.PromotionResult:
    sandbox = SandboxResult(
        exit_code=0,
        stdout=json.dumps({"declared_metric_delta": 0.25, "outcome_trace": [{"task": "t1", "score": 0.5}]}),
        stderr="",
        wall_ms=1.0,
        rss_peak_mb=1.0,
        network_attempted=False,
        writes_outside_tmp=[],
        timed_out=False,
    )
    monkeypatch.setattr(promote, "run_sandboxed", lambda *_a, **_k: sandbox)
    config = TRWConfig(
        meta_tune=MetaTuneConfig(enabled=True, audit_log_path=str(tmp_path / "audit" / "meta_tune_audit.jsonl"))
    )
    return promote.promote_candidate(
        target_path=target,
        candidate_content="after\n",
        proposer_id="agent",
        reviewer_id="alice",
        approval_ts=datetime.now(timezone.utc),
        sandbox_command=["python", "-c", "pass"],
        edit_id="edit-1",
        state_dir=tmp_path / "state",
        _config=config,
    )


@pytest.mark.parametrize(
    ("link_rel", "target_exists"),
    [
        ("project/CLAUDE.md", False),  # the live promoted target itself
        ("state/staging/edit-1/CLAUDE.md", True),  # the sandbox's staged copy
        ("state/backups/edit-1.bak", False),  # the empty backup written when the target is new
        ("state/backups/edit-1.bak", True),  # the backup copy of an existing target (was shutil.copy2)
    ],
)
def test_promote_refuses_a_symlink_at_each_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_rel: str, target_exists: bool
) -> None:
    target = tmp_path / "project" / "CLAUDE.md"
    target.parent.mkdir(parents=True)
    if target_exists:
        target.write_text("before\n", encoding="utf-8")
    victim = plant_symlink(tmp_path / link_rel, tmp_path / "outside")

    refused = _refusal(lambda: _promote(tmp_path, monkeypatch, target))

    assert victim.read_bytes() == OUTSIDE_BYTES
    assert refused is not None  # a CLI path: the refusal surfaces
    if target_exists:
        assert target.read_bytes() == b"before\n"


def test_promote_writes_a_plain_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "project" / "CLAUDE.md"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    target.chmod(0o600)

    result = _promote(tmp_path, monkeypatch, target)

    assert result.promoted is True
    assert target.read_bytes() == b"after\n"
    backup = tmp_path / "state" / "backups" / "edit-1.bak"
    assert backup.read_bytes() == b"before\n"
    if os.name != "nt":  # rollback copy2-s the backup back, so it must carry the target's mode, as copy2 did
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600


# --- server._subcommands: audit / export --output (R12 row 7 and its twin) ---------------------------

_REPORT = {"status": "ok", "entries": [1, 2]}


def _cli(verb: str, output: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if verb == "export":
        monkeypatch.setattr("trw_mcp.export.export_data", lambda *_a, **_k: dict(_REPORT))
        args = argparse.Namespace(target_dir=".", scope="all", format="json", output=str(output))
        handler = _subcommands._run_export
    else:
        monkeypatch.setattr("trw_mcp.audit.run_audit", lambda *_a, **_k: dict(_REPORT))
        args = argparse.Namespace(target_dir=".", fix=False, format="json", output=str(output))
        handler = _subcommands._run_audit
    with pytest.raises(SystemExit) as done:
        handler(args)
    assert done.value.code == 0


@pytest.mark.parametrize("verb", ["export", "audit"])
def test_cli_output_refuses_a_symlinked_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str) -> None:
    link = tmp_path / "reports" / "out.json"
    victim = plant_symlink(link, tmp_path / "outside")

    refused = _refusal(lambda: _cli(verb, link, monkeypatch))

    assert_untouched(link, victim)
    assert refused is not None  # the CLI surfaces the refusal (non-zero exit via traceback)


@pytest.mark.parametrize("verb", ["export", "audit"])
def test_cli_output_writes_a_plain_file_and_keeps_its_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    out = tmp_path / "reports" / "out.json"
    out.parent.mkdir()
    out.write_text("stale\n", encoding="utf-8")
    out.chmod(0o600)

    _cli(verb, out, monkeypatch)

    assert out.read_bytes() == json.dumps(_REPORT, indent=2, default=str).encode("utf-8")
    if os.name != "nt":
        assert stat.S_IMODE(out.stat().st_mode) == 0o600  # a private report is never widened by the rewrite


# --- telemetry.sender.BatchSender.send -> _rewrite_queue (R12 row 11) ---------------------------------

_CONSENTED = json.dumps(stamp_consent({"event": "kept"}, consented=True)) + "\n"
_PRE_CONSENT = json.dumps(stamp_consent({"event": "dropped"}, consented=False)) + "\n"


def _send(queue: Path, monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(sender_module, "platform_contact_enabled", lambda _root: True)
    trw_dir = queue.parents[1]  # the queue's project, whose own config grants the send
    (trw_dir / "config.yaml").write_text("platform_telemetry_enabled: true\n", encoding="utf-8")
    batch_sender = BatchSender(
        platform_urls=["https://api.example.com"],
        input_path=queue,
        max_retries=1,
        backoff_base=0.0,
        platform_telemetry_enabled=True,
        source_trw_dir=trw_dir,
    )
    monkeypatch.setattr(batch_sender, "_http_post", lambda *_a: False)  # the batch fails, so it is re-queued
    return batch_sender.send()


def test_telemetry_queue_rewrite_refuses_a_symlinked_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue = tmp_path / ".trw" / "logs" / "tool-telemetry.jsonl"
    original = (_PRE_CONSENT + _CONSENTED).encode("utf-8")
    victim = plant_symlink(queue, tmp_path / "outside", original)

    refused = _refusal(lambda: _send(queue, monkeypatch))

    assert_untouched(queue, victim, original)
    assert refused is not None  # surfaces; the deferred delivery step logs it (_deferred_delivery)


def test_telemetry_queue_rewrite_keeps_only_unsent_consented_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / ".trw" / "logs" / "tool-telemetry.jsonl"
    queue.parent.mkdir(parents=True)
    queue.write_text(_PRE_CONSENT + _CONSENTED, encoding="utf-8")

    result = _send(queue, monkeypatch)

    assert isinstance(result, dict) and result["remaining"] == 1
    assert queue.read_bytes() == _CONSENTED.encode("utf-8")


# --- tools._post_commit.run_post_commit: pending marker and receipt (R12 row 22) ---------------------


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "repo"
    (root / ".trw" / "runtime").mkdir(parents=True)
    monkeypatch.delenv(pc.HEAD_ENV_VAR, raising=False)
    monkeypatch.delenv(pc.BUDGET_ENV_VAR, raising=False)
    monkeypatch.setattr(pc, "_sweep_trw_dir", lambda _root: root / ".trw")
    monkeypatch.setattr(pc, "_head_sha", lambda _root: "cafebabe")
    monkeypatch.setattr(pc, "_run_pass", lambda *_a: None)
    yield root
    while _HELD:
        os.close(_HELD.pop())


_HELD: list[int] = []


def _hold_lock(repo: Path) -> None:
    """A live owner: the lock file exists and is flock-held on a separate descriptor (closed by the fixture)."""
    import fcntl

    lock = repo / ".trw" / pc.LOCK_REL_PATH
    lock.write_text(json.dumps({"pid": os.getpid(), "started_at": "now", "head_sha": "owner"}), encoding="utf-8")
    fd = os.open(lock, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    _HELD.append(fd)


def test_post_commit_pending_marker_refuses_a_symlink_and_reports_it(repo: Path, tmp_path: Path) -> None:
    _hold_lock(repo)
    link = repo / ".trw" / pc.PENDING_REL_PATH
    victim = plant_symlink(link, tmp_path / "outside")

    receipt = pc.run_post_commit(repo)  # "Never raises": the git hook must not fail

    assert_untouched(link, victim)
    assert receipt.lock_state == "deferred"
    assert receipt.pending_marked is False  # the refusal is visible in the receipt, not assumed away


def test_post_commit_pending_marker_on_a_plain_file(repo: Path) -> None:
    _hold_lock(repo)

    receipt = pc.run_post_commit(repo)

    assert receipt.pending_marked is True
    marker = json.loads((repo / ".trw" / pc.PENDING_REL_PATH).read_text(encoding="utf-8"))
    assert marker["head_sha"] == "cafebabe" and set(marker) == {"head_sha", "marked_at"}


def test_post_commit_receipt_refuses_a_symlink_without_raising(repo: Path, tmp_path: Path) -> None:
    link = repo / pc.RECEIPT_REL_PATH
    victim = plant_symlink(link, tmp_path / "outside")

    receipt = pc.run_post_commit(repo)

    assert receipt.lock_state == "acquired"
    assert_untouched(link, victim)


def test_post_commit_receipt_on_a_plain_file(repo: Path) -> None:
    receipt = pc.run_post_commit(repo)

    written = (repo / pc.RECEIPT_REL_PATH).read_bytes()
    assert written == json.dumps(receipt.as_dict(), indent=2).encode("utf-8")


# --- security.anomaly_detector: the shadow clock, via AnomalyDetector.observe (R12 row 3) -------------


def _observe(clock: Path) -> list[str]:
    detector = AnomalyDetector(config=AnomalyDetectorConfig(shadow_clock_path=clock), now_fn=lambda: _NOW)
    return detector.observe(AnomalyObservation(ts=_NOW, server="trw", tool="trw_status"))


def test_shadow_clock_refuses_a_symlink_and_the_tool_call_still_passes(tmp_path: Path) -> None:
    clock = tmp_path / ".trw" / "security" / "mcp_shadow_start.yaml"
    victim = plant_symlink(clock, tmp_path / "outside")  # not a clock document, so the bootstrap writes

    assert isinstance(_observe(clock), list)  # the middleware keeps working; the clock stays in memory

    assert_untouched(clock, victim)


def test_shadow_clock_on_a_plain_path(tmp_path: Path) -> None:
    clock = tmp_path / ".trw" / "security" / "mcp_shadow_start.yaml"

    _observe(clock)

    expected = {
        "phase": "shadow",
        "started_at": _NOW.isoformat(),
        "threshold_review_at": datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc).isoformat(),
    }
    assert clock.read_bytes() == yaml.safe_dump(expected, sort_keys=True).encode("utf-8")


# --- state.analytics._stale_runs.stale_advisory_first_time (R12 row 16), what trw_status asks ----------


def test_stale_advisory_sentinel_refuses_a_dangling_symlink(tmp_path: Path) -> None:
    run_dir = tmp_path / ".trw" / "runs" / "r1"
    link = run_dir / "meta" / ".stale_advisory_shown"
    link.parent.mkdir(parents=True)
    victim = tmp_path / "outside" / "created-through-the-link"
    victim.parent.mkdir()
    link.symlink_to(victim)  # dangling: exists() is False, so the writer tries to create it

    first = stale_advisory_first_time(run_dir)

    assert not victim.exists(), "nothing may be created through the planted link"
    assert link.is_symlink()
    assert first is True  # fail-open: the hint shows again
    assert stale_advisory_first_time(run_dir) is True


def test_stale_advisory_sentinel_refuses_a_symlinked_parent_dir(tmp_path: Path) -> None:
    """``meta/`` itself links to another tree: the walk below run_dir refuses it and nothing lands there."""
    run_dir = tmp_path / ".trw" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    elsewhere = tmp_path / "outside" / "meta"
    elsewhere.mkdir(parents=True)
    (run_dir / "meta").symlink_to(elsewhere, target_is_directory=True)

    first = stale_advisory_first_time(run_dir)

    assert list(elsewhere.iterdir()) == [], "nothing may be created through the symlinked parent"
    assert first is True  # fail-open: the refusal is logged by safe_fs and the hint shows


def test_stale_advisory_sentinel_on_a_plain_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / ".trw" / "runs" / "r1"
    run_dir.mkdir(parents=True)

    assert stale_advisory_first_time(run_dir) is True
    assert stale_advisory_first_time(run_dir) is False
    assert (run_dir / "meta" / ".stale_advisory_shown").read_bytes() == b""
