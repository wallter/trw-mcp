"""PRD-CORE-206 outcome-based lifecycle trust + shared-worktree safety.

PRD-CORE-206 supersedes the activity-count route in PRD-FIX-053-FR02: learnings,
checkpoints, edits, and commit counts are activity, not verification, and a raw
build *event* is replayable. The only positive route is a validated typed
PRD-CORE-205 receipt consumed atomically at most once.

Coverage:
- FR03: activity/raw-event counts never increment ``successful_sessions``.
- FR04: closed task-type/evidence matrix + atomic single consumption.
- NFR01: raw build/activity/review/acceptable-failure/unknown never increment.
- NFR02: concurrent workers yield one increment; injected write failure is inert.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import threading
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state._trust_outcome import (
    classify_trust_eligibility,
    compute_receipt_set_digest,
    compute_trust_outcome_id,
    consume_trust_outcome,
)
from trw_mcp.state.trust import read_trust_registry, write_trust_registry
from trw_mcp.tools._evidence_persistence import write_receipt
from trw_mcp.tools._evidence_writers import parse_build_command_results, record_build_receipt

from ._evidence_factories import (
    build_receipt,
    project_with_binding,
    validation_plan,
    verification_receipt,
)

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _write_events(run_dir: Path, events: list[dict[str, object]]) -> None:
    path = run_dir / "meta" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _write_run_yaml(run_dir: Path, task_type: str) -> None:
    path = run_dir / "meta" / "run.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"task_type: {task_type}\n", encoding="utf-8")


def _seed_registry(trw_dir: Path, *, session_count: int = 0, successful: int = 0) -> None:
    write_trust_registry(
        trw_dir,
        {
            "project": {
                "session_count": session_count,
                "successful_sessions": successful,
                "last_session_at": None,
                "tier": "crawl",
                "consumed_trust_outcome_ids": {},
            }
        },
    )


def _registry_bytes(trw_dir: Path) -> bytes:
    return (trw_dir / "context" / "trust-registry.yaml").read_bytes()


def _project_counts(trw_dir: Path) -> tuple[int, int]:
    project = read_trust_registry(trw_dir)["project"]
    assert isinstance(project, dict)
    return int(project["session_count"]), int(project["successful_sessions"])


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "runs" / "20260313T000000Z-abc123"
    (path / "meta").mkdir(parents=True)
    return path


def _patch_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trw_dir: Path,
    project_root: Path,
    config: TRWConfig,
) -> None:
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project_root)


def _enforce(config: TRWConfig) -> TRWConfig:
    return config.model_copy(update={"evidence_receipt_mode": "enforce"})


def _observe(config: TRWConfig) -> TRWConfig:
    return config.model_copy(update={"evidence_receipt_mode": "observe"})


def _write_real_build_receipt(project: Path, run: Path) -> None:
    source = project / "src" / "a.py"
    _write_events(run, [{"event": "file_modified", "file": str(source)}])
    commands = parse_build_command_results(
        [
            {"command_id": "tests", "label": "pytest", "command_class": "test", "exit_code": 0},
            {
                "command_id": "static_checks",
                "label": "ruff+mypy",
                "command_class": "static",
                "exit_code": 0,
            },
        ]
    )
    assert commands is not None
    outcome = record_build_receipt(
        run,
        project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="full",
        coverage_pct=None,
        policy_mode="enforce",
        command_results=commands,
    )
    assert outcome is not None and outcome.ok


# --------------------------------------------------------------------------
# FR03 — activity counts never increment
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("learn_count", "checkpoint_count"), [(3, 1), (5, 2), (20, 10), (0, 1), (3, 0)])
@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_activity_counts_never_increment_successful_sessions(
    run_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: TRWConfig,
    learn_count: int,
    checkpoint_count: int,
    mode: str,
) -> None:
    """Learning/checkpoint volume alone never advances review autonomy (FR03).

    Activity events with no eligible typed receipt leave the registry byte-identical
    in both interim (observe-frozen) and enforce modes.
    """
    from trw_mcp.tools._deferred_delivery import _step_trust_increment

    events: list[dict[str, object]] = [
        {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}} for _ in range(learn_count)
    ]
    events.extend(
        {"event": "tool_invocation", "data": {"tool_name": "trw_checkpoint"}} for _ in range(checkpoint_count)
    )
    _write_events(run_dir, events)
    _write_run_yaml(run_dir, "coding")

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    _seed_registry(trw_dir, session_count=7, successful=3)
    before = _registry_bytes(trw_dir)

    resolved_config = _enforce(config) if mode == "enforce" else _observe(config)
    _patch_env(monkeypatch, trw_dir=trw_dir, project_root=tmp_path, config=resolved_config)

    result = _step_trust_increment(run_dir)

    assert result is not None
    assert result.get("skipped") is True
    assert "reason" in result
    assert _registry_bytes(trw_dir) == before, "activity-only run must not touch the registry"


# --------------------------------------------------------------------------
# FR04 — closed eligibility matrix
# --------------------------------------------------------------------------

# (task_type, positive validated receipt kinds, expected eligible)
_MATRIX = [
    ("coding", {"build"}, True),
    ("coding", {"verification"}, True),
    ("coding", set(), False),  # review-only / acceptable-failure / counts
    ("rca", {"verification"}, True),
    ("rca", {"build"}, True),
    ("eval", {"verification"}, True),
    ("eval", {"build"}, True),
    ("docs", {"verification"}, True),
    ("docs", {"build"}, False),  # docs only accepts verification
    ("research", {"verification"}, True),
    ("research", {"build"}, False),
    ("planning", {"verification"}, True),
    ("planning", {"build"}, False),
    ("unknown", {"build"}, False),  # operator must classify first
    ("unknown", {"verification"}, False),
    ("unknown", set(), False),
]


@pytest.mark.parametrize(("task_type", "positive_kinds", "expected"), _MATRIX)
def test_closed_eligibility_matrix(task_type: str, positive_kinds: set[str], expected: bool) -> None:
    result = classify_trust_eligibility(task_type, positive_kinds)
    assert result.eligible is expected
    if not expected:
        assert result.reason in {"unknown_task_class", "no_eligible_positive_receipt"}


def test_review_only_and_acceptable_failure_are_never_eligible() -> None:
    """Review verdicts and acceptable-failure records contribute no positive kind."""
    # A review receipt would surface as no positive build/verification kind.
    for task_type in ("coding", "rca", "eval", "docs", "research", "planning"):
        assert classify_trust_eligibility(task_type, set()).eligible is False


def test_outcome_evidence_matrix_and_atomic_single_consumption(tmp_path: Path, config: TRWConfig) -> None:
    """FR04: matrix holds and one outcome consumes exactly once (dup + conflict + concurrent)."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir)

    outcome_id = compute_trust_outcome_id("proj", "run-1", None)
    digest = compute_receipt_set_digest([("build-1", "d1"), ("verify-1", "d2")])

    # First consumption increments both counters exactly once.
    first = consume_trust_outcome(trw_dir, outcome_id, digest, config=config)
    assert first.status == "incremented"
    assert first.incremented is True
    assert _project_counts(trw_dir) == (1, 1)

    # Duplicate (identical binding) is idempotent — no second increment.
    dup = consume_trust_outcome(trw_dir, outcome_id, digest, config=config)
    assert dup.status == "idempotent"
    assert dup.incremented is False
    assert _project_counts(trw_dir) == (1, 1)

    # A changed receipt set for a consumed outcome is a conflict — never increments.
    changed = consume_trust_outcome(trw_dir, outcome_id, compute_receipt_set_digest([("x", "y")]), config=config)
    assert changed.status == "conflict"
    assert changed.incremented is False
    assert _project_counts(trw_dir) == (1, 1)

    # Concurrent consumption of the SAME outcome+digest yields exactly one increment.
    trw_dir2 = tmp_path / ".trw2"
    trw_dir2.mkdir(parents=True)
    _seed_registry(trw_dir2)
    outcome2 = compute_trust_outcome_id("proj", "run-2", None)
    barrier = threading.Barrier(8)
    statuses: list[str] = []
    lock = threading.Lock()

    def _worker() -> None:
        barrier.wait()
        res = consume_trust_outcome(trw_dir2, outcome2, digest, config=config)
        with lock:
            statuses.append(res.status)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses.count("incremented") == 1
    assert statuses.count("idempotent") == 7
    assert _project_counts(trw_dir2) == (1, 1)


def _mp_worker(trw_dir_str: str, outcome_id: str, digest: str, config: TRWConfig) -> str:
    from trw_mcp.state._trust_outcome import consume_trust_outcome as _consume

    return _consume(Path(trw_dir_str), outcome_id, digest, config=config).status


def test_concurrent_processes_consume_outcome_once(
    tmp_path: Path, config: TRWConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR02: cross-process flock serializes to a single increment."""
    repo_root = str(Path(__file__).resolve().parents[1])
    monkeypatch.syspath_prepend(repo_root)
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (repo_root, existing_pythonpath))))

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir)
    outcome_id = compute_trust_outcome_id("proj", "run-mp", None)
    digest = compute_receipt_set_digest([("b", "d")])

    ctx = mp.get_context("spawn")
    args = [(str(trw_dir), outcome_id, digest, config) for _ in range(6)]
    with ctx.Pool(6) as pool:
        statuses = pool.starmap(_mp_worker, args)

    assert statuses.count("incremented") == 1
    assert _project_counts(trw_dir) == (1, 1)


# --------------------------------------------------------------------------
# NFR01 — no raw event / activity / review / acceptable-failure increment
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["observe", "enforce"])
@pytest.mark.parametrize(
    "event",
    [
        {"event": "build_check_complete", "data": {"result": "pass"}},
        {"event": "tool_invocation", "data": {"tool_name": "trw_build_check", "build_passed": True}},
        {"event": "review_complete", "data": {"verdict": "pass"}},
        {"event": "acceptable_failure", "data": {"authorized": True}},
    ],
)
def test_raw_build_event_cannot_increment_without_typed_receipt(
    run_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: TRWConfig,
    mode: str,
    event: dict[str, object],
) -> None:
    """A raw event (log line), not a typed receipt, never advances trust (NFR01)."""
    from trw_mcp.tools._deferred_delivery import _step_trust_increment

    _write_events(run_dir, [event])
    _write_run_yaml(run_dir, "coding")
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir, session_count=2, successful=1)
    before = _registry_bytes(trw_dir)

    resolved_config = _enforce(config) if mode == "enforce" else _observe(config)
    _patch_env(monkeypatch, trw_dir=trw_dir, project_root=tmp_path, config=resolved_config)

    result = _step_trust_increment(run_dir)

    assert result is not None
    assert result.get("skipped") is True
    assert _registry_bytes(trw_dir) == before


def test_unknown_task_class_never_increments(tmp_path: Path, config: TRWConfig) -> None:
    """An unknown task class with any evidence stays frozen (FR04 unknown row)."""
    project, binding, _scope = project_with_binding(tmp_path, {"src/a.py": "x = 1\n"})
    run = project / "run"
    plan = validation_plan(binding, required_command_ids=("pytest",))
    write_receipt(run, "build", "build-1", build_receipt(binding, plan))

    from trw_mcp.state.trust import evaluate_and_consume_trust_outcome

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir)
    eligibility, consume = evaluate_and_consume_trust_outcome(trw_dir, run, project, "unknown", config=config)
    assert eligibility.eligible is False
    assert eligibility.reason == "unknown_task_class"
    assert consume is None
    assert _project_counts(trw_dir) == (0, 0)


# --------------------------------------------------------------------------
# NFR02 — atomicity: injected write failure leaves the registry intact
# --------------------------------------------------------------------------


def test_registry_and_evidence_consumption_are_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: TRWConfig,
) -> None:
    """Injected write failure produces no partial increment or consumption marker."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir, session_count=4, successful=2)
    before = _registry_bytes(trw_dir)

    outcome_id = compute_trust_outcome_id("proj", "run-fail", None)
    digest = compute_receipt_set_digest([("b", "d")])

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected registry write failure")

    monkeypatch.setattr("trw_mcp.state.trust.write_trust_registry", _boom)
    failed = consume_trust_outcome(trw_dir, outcome_id, digest, config=config)
    assert failed.status == "write_failed"
    assert failed.incremented is False
    # On-disk registry is byte-identical: no counter change, no consumption marker.
    assert _registry_bytes(trw_dir) == before
    assert _project_counts(trw_dir) == (4, 2)

    # Retry after the failure clears succeeds — no commit occurred, so it is fresh.
    monkeypatch.undo()
    retry = consume_trust_outcome(trw_dir, outcome_id, digest, config=config)
    assert retry.status == "incremented"
    assert _project_counts(trw_dir) == (5, 3)


# --------------------------------------------------------------------------
# Enforce-path wiring — collection is not a stub
# --------------------------------------------------------------------------


def test_enforce_path_consumes_fresh_build_receipt(tmp_path: Path, config: TRWConfig) -> None:
    """A current, passing BuildReceipt for a coding run is eligible and consumed once."""
    project, binding, _scope = project_with_binding(tmp_path, {"src/a.py": "x = 1\n"})
    run = project / "run"
    _write_real_build_receipt(project, run)

    from trw_mcp.state.trust import evaluate_and_consume_trust_outcome

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir)
    eligibility, consume = evaluate_and_consume_trust_outcome(trw_dir, run, project, "coding", config=config)
    assert eligibility.eligible is True
    assert consume is not None and consume.status == "incremented"
    assert _project_counts(trw_dir) == (1, 1)

    # Re-running the same run is idempotent (same receipt set) — no second increment.
    _elig2, consume2 = evaluate_and_consume_trust_outcome(trw_dir, run, project, "coding", config=config)
    assert consume2 is not None and consume2.status == "idempotent"
    assert _project_counts(trw_dir) == (1, 1)


def test_stale_receipt_is_not_positive_evidence(tmp_path: Path, config: TRWConfig) -> None:
    """A bound file changed after the receipt -> stale -> not eligible."""
    project, binding, _scope = project_with_binding(tmp_path, {"src/a.py": "x = 1\n"})
    run = project / "run"
    _write_real_build_receipt(project, run)
    # Mutate a bound file after the receipt was written.
    (project / "src/a.py").write_text("x = 2\n", encoding="utf-8")

    from trw_mcp.state.trust import evaluate_and_consume_trust_outcome

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir)
    eligibility, consume = evaluate_and_consume_trust_outcome(trw_dir, run, project, "coding", config=config)
    assert eligibility.eligible is False
    assert consume is None
    assert _project_counts(trw_dir) == (0, 0)


def test_verification_receipt_only_route_for_docs(tmp_path: Path, config: TRWConfig) -> None:
    """docs task: a passing VerificationReceipt is eligible; a build receipt is not."""
    project, binding, _scope = project_with_binding(tmp_path, {"docs/x.md": "# hi\n"})
    run = project / "run"
    write_receipt(run, "verification", "verify-1", verification_receipt(binding))

    from trw_mcp.state.trust import evaluate_and_consume_trust_outcome

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _seed_registry(trw_dir)
    eligibility, consume = evaluate_and_consume_trust_outcome(trw_dir, run, project, "docs", config=config)
    assert eligibility.eligible is True
    assert consume is not None and consume.status == "incremented"


def test_no_run_dir_skips_gracefully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: TRWConfig) -> None:
    from trw_mcp.tools._deferred_delivery import _step_trust_increment

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True)
    _patch_env(monkeypatch, trw_dir=trw_dir, project_root=tmp_path, config=_enforce(config))

    result = _step_trust_increment(None)
    assert result is not None
    assert result.get("skipped") is True
