"""Core orchestration tool tests — module size, init, status, checkpoint.

PRD-CORE-280 slice e1: none of these tests care how memory behaves, but
``trw_status`` reaches the memory store internally as part of its ceremony
status computation (e.g. embedding/WAL health), so the checkout routes
through ``fake_memory_store`` rather than the in-process SQLite store the
unpinned ``TRW_PROJECT_ROOT`` (via ``set_project_root``) would otherwise open.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from tests._tools_orchestration_support import FRAMEWORK_VERSION, orch_tools, set_project_root  # noqa: F401
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._orchestration_phase import (
    _check_framework_version_staleness,
    _compute_reversion_metrics,
)

pytestmark = pytest.mark.usefixtures("fake_memory_store")


def test_orchestration_module_stays_within_500_lines() -> None:
    """CORE-089 keeps orchestration.py at or under the documented size gate."""
    module_path = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools" / "orchestration.py"
    with module_path.open("r", encoding="utf-8") as module_file:
        assert sum(1 for _ in module_file) <= 500


@pytest.mark.parametrize(
    "helper",
    [
        _compute_reversion_metrics,
        _check_framework_version_staleness,
    ],
)
def test_phase_helpers_live_in_orchestration_phase(helper: object) -> None:
    """CORE-089 phase helpers should be defined in _orchestration_phase.py."""
    source_file = inspect.getsourcefile(helper)
    assert source_file is not None
    assert source_file.endswith("_orchestration_phase.py")


class TestTrwInit:
    """Tests for trw_init tool."""

    def test_creates_trw_dir(self, tmp_path: Path, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="test-task", objective="Test objective")

        assert "run_id" in result
        assert "run_path" in result
        assert result["status"] == "initialized"
        assert len(result["task_profile_hash"]) == 16
        assert result["capability_tier"] == "balanced"
        # model_tier was a byte-for-byte duplicate of capability_tier and was
        # removed from responses 2026-07-27; assert it stays gone.
        assert "model_tier" not in result
        assert result["recommended_effort"] == "medium"
        assert result["effort_source"] == "task_complexity"
        assert result["effort_adapter_status"] == "advisory"

        trw_dir = tmp_path / ".trw"
        assert trw_dir.exists()
        assert (trw_dir / "config.yaml").exists()
        assert (trw_dir / "learnings" / "entries").exists()
        assert (trw_dir / "reflections").exists()
        assert (trw_dir / "scripts").exists()
        assert (trw_dir / "patterns").exists()
        assert (trw_dir / "context").exists()
        assert (trw_dir / ".gitignore").exists()

    def test_creates_run_dirs(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="my-task")

        run_path = Path(result["run_path"])
        assert (run_path / "meta" / "run.yaml").exists()
        assert (run_path / "meta" / "events.jsonl").exists()
        assert (run_path / "reports").exists()
        assert (run_path / "scratch" / "_orchestrator").exists()
        assert (run_path / "shards").exists()

    def test_run_yaml_content(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="check-task")

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["task"] == "check-task"
        assert run_yaml["framework"] == FRAMEWORK_VERSION
        assert run_yaml["status"] == "active"
        assert run_yaml["phase"] == "research"
        assert run_yaml["task_profile"]["complexity_class"] == "STANDARD"
        assert run_yaml["task_profile"]["capability_tier"] == "balanced"
        assert run_yaml["task_profile"]["recommended_effort"] == "medium"
        assert run_yaml["task_profile"]["effort_source"] == "task_complexity"
        assert run_yaml["task_profile"]["effort_adapter_status"] == "advisory"
        assert "model_tier" not in run_yaml["task_profile"]
        assert "reasoning_effort" not in run_yaml["task_profile"]
        assert len(run_yaml["task_profile"]["profile_hash"]) == 16

    def test_init_easy_hint_persists_minimal_task_profile(self, orch_tools: dict[str, Any]) -> None:
        result = orch_tools["trw_init"].fn(task_name="easy-task", complexity_hint="EASY")

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_class"] == "MINIMAL"
        assert run_yaml["task_profile"]["complexity_class"] == "MINIMAL"
        assert run_yaml["task_profile"]["ceremony_depth"] == "light"
        assert "VALIDATE" in run_yaml["task_profile"]["mandatory_phases"]


class TestTrwStatus:
    """Tests for trw_status tool."""

    def test_reads_run_state(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="status-task")
        run_path = init_result["run_path"]

        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert status["task"] == "status-task"
        assert status["phase"] == "research"
        assert status["status"] == "active"
        assert status["event_count"] >= 1
        assert status["phase_durations"]["active_phase"] == "research"
        assert status["phase_durations"]["phase_seconds"]["research"] >= 0.0
        assert status["capability_tier"] == "balanced"
        assert "model_tier" not in status
        assert status["recommended_effort"] == "medium"
        assert status["effort_source"] == "task_complexity"
        assert status["effort_adapter_status"] == "advisory"

    def test_torn_events_line_does_not_abort_status(self, orch_tools: dict[str, Any]) -> None:
        """A torn concurrent append in events.jsonl must not brick trw_status.

        events.jsonl is an append-only log that trw_status reads only for
        advisory analytics (event_count, reflection, phase_durations,
        reversions); the authoritative run state lives in run.yaml. A single
        torn append (two writers interleaving a partial record) must degrade to
        "drop that one line", not raise StateError and abort the whole status
        read — trw_status is invoked on every resume/compaction, so aborting it
        blinds the agent to its own run. Mirrors the resilient-read fixes
        already applied to the live _do_reflect seam over this same log
        (regression guard).
        """
        init_result = orch_tools["trw_init"].fn(task_name="torn-status-task")
        run_path = init_result["run_path"]

        events_path = Path(run_path) / "meta" / "events.jsonl"
        existing = events_path.read_text(encoding="utf-8")
        # Append a torn line (valid JSON prefix, truncated mid-object) followed
        # by an intact event. Before the fix the torn line raised StateError.
        torn = '{"ts": "2026-02-11T12:01:00Z", "type": "phase_chan\n'
        intact = '{"ts": "2026-02-11T12:02:00Z", "type": "checkpoint", "phase": "research"}\n'
        events_path.write_text(existing + torn + intact, encoding="utf-8")

        status = orch_tools["trw_status"].fn(run_path=run_path)

        # Status still resolves; authoritative fields come from run.yaml.
        assert status["task"] == "torn-status-task"
        assert status["status"] == "active"
        # The torn line is dropped, not fatal; intact events still counted.
        assert status["event_count"] >= 1


class TestTrwCheckpoint:
    """Tests for trw_checkpoint tool."""

    def test_creates_checkpoint(self, orch_tools: dict[str, Any]) -> None:
        init_result = orch_tools["trw_init"].fn(task_name="cp-task")

        result = orch_tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="Test checkpoint",
        )
        assert result["status"] == "checkpoint_created"
        assert result["message"] == "Test checkpoint"

        cp_path = Path(init_result["run_path"]) / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()


# --- PRD-CORE-338: time block on trw_status and trw_checkpoint --------------

_FROZEN_NOW = "2026-09-26T12:00:00+00:00"


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Inject one ``now`` into every clock the status/checkpoint path reads (NFR04).

    Patches the ``datetime`` name each module resolves, so the same fixture
    freezes the pre-change modules too (the failing-first run against int).
    """
    import importlib.util
    from datetime import datetime

    now = datetime.fromisoformat(_FROZEN_NOW)

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return now

    for name in ("_orchestration_lifecycle", "_orchestration_checkpoint", "_orchestration_time"):
        # The pre-change tree has no _orchestration_time; freeze whichever modules exist.
        if importlib.util.find_spec(f"trw_mcp.tools.{name}") is not None:
            monkeypatch.setattr(importlib.import_module(f"trw_mcp.tools.{name}"), "datetime", _Frozen)
    return now


def _seed_tracked_events(run_path: Path) -> None:
    """Rewrite the run's events to the FR01 worked example (start 10:00Z, 3 slices done)."""
    import json

    lines = [
        {"ts": "2026-09-26T10:00:00Z", "event": "run_init"},
        {"ts": "2026-09-26T10:00:00Z", "event": "phase_enter", "phase": "research"},
        *(
            {"ts": f"2026-09-26T{hhmm}:00Z", "event": "checkpoint", "slice_done": sid}
            for sid, hhmm in (("S1", "11:00"), ("S2", "11:30"), ("S3", "12:00"))
        ),
    ]
    (run_path / "meta" / "events.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))


def _status_json(run_path: str) -> str:
    """The run-scoped trw_status payload, assembled exactly as the served tool does.

    Assembled directly so the comparison excludes ``nudge_content``/
    ``ceremony_status``, which the facade appends from project-wide session
    state that legitimately changes between two calls.
    """
    from trw_mcp.state._helpers import read_jsonl_resilient
    from trw_mcp.tools._orchestration_status_assembly import assemble_status_result

    reader = FileStateReader()
    meta = Path(run_path) / "meta"
    state = reader.read_yaml(meta / "run.yaml")
    events = read_jsonl_resilient(meta / "events.jsonl")
    return json.dumps(assemble_status_result(state, events, Path(run_path), reader, meta), sort_keys=True)


def _status_with_time_removed(run_path: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """The pre-change output proxy: the same assembly with the time path removed."""
    from trw_mcp.tools import _orchestration_status_assembly as assembly

    with monkeypatch.context() as patch:
        patch.setattr(assembly, "status_time_block", lambda *_a, **_k: None)
        return _status_json(run_path)


class TestStatusTimeBlock:
    """PRD-CORE-338 FR05/NFR02/NFR03."""

    def test_status_time_block_only_for_tracked_runs(self, orch_tools: dict[str, Any], frozen_clock: Any) -> None:
        tracked = orch_tools["trw_init"].fn(task_name="tracked", complexity_hint="HARD")
        _seed_tracked_events(Path(tracked["run_path"]))
        block = orch_tools["trw_status"].fn(run_path=tracked["run_path"])["time"]
        assert block["started_at"] == "2026-09-26T10:00:00Z"
        assert block["elapsed_seconds"] == 7200
        assert block["last_event_at"] == "2026-09-26T12:00:00Z"
        assert block["forecast"]["n_done"] == 3
        assert block["forecast"]["status"] == "no_total"  # no declared slices: FR04 supplies the total
        assert block["drift"] == "no_target"

        untracked = orch_tools["trw_init"].fn(task_name="untracked", complexity_hint="EASY")
        assert "time" not in orch_tools["trw_status"].fn(run_path=untracked["run_path"])

    @pytest.mark.parametrize("hint", ["EASY", "STANDARD"])
    def test_untracked_status_is_byte_identical(
        self, orch_tools: dict[str, Any], frozen_clock: Any, monkeypatch: pytest.MonkeyPatch, hint: str
    ) -> None:
        run_path = orch_tools["trw_init"].fn(task_name=f"u-{hint}", complexity_hint=hint)["run_path"]
        _seed_tracked_events(Path(run_path))
        baseline = _status_with_time_removed(run_path, monkeypatch)
        current = _status_json(run_path)
        assert json.loads(current) == json.loads(baseline)
        assert current == baseline

    def test_kill_switch_status_is_byte_identical(
        self, orch_tools: dict[str, Any], frozen_clock: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.models.config import get_config

        run_path = orch_tools["trw_init"].fn(task_name="killed", complexity_hint="HARD")["run_path"]
        _seed_tracked_events(Path(run_path))
        monkeypatch.setattr(get_config(), "time_tracking_enabled", False)
        baseline = _status_with_time_removed(run_path, monkeypatch)
        current = _status_json(run_path)
        assert json.loads(current) == json.loads(baseline)
        assert current == baseline

    def test_status_reads_events_once(
        self, orch_tools: dict[str, Any], frozen_clock: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.tools import orchestration

        run_path = orch_tools["trw_init"].fn(task_name="reads", complexity_hint="HARD")["run_path"]
        _seed_tracked_events(Path(run_path))
        real = orchestration.read_jsonl_resilient
        reads: list[str] = []

        def _counting(path: Path) -> list[dict[str, object]]:
            reads.append(Path(path).name)
            return real(path)

        monkeypatch.setattr(orchestration, "read_jsonl_resilient", _counting)
        status = orch_tools["trw_status"].fn(run_path=run_path)
        assert "time" in status
        assert reads.count("events.jsonl") == 1

    def test_phase_reentry_sums_intervals(self, frozen_clock: Any) -> None:
        from trw_mcp.tools._orchestration_lifecycle import _phase_duration_summary

        events: list[dict[str, object]] = [
            {"event": "phase_enter", "phase": "implement", "ts": "2026-09-26T10:00:00Z"},
            {"event": "phase_enter", "phase": "validate", "ts": "2026-09-26T10:30:00Z"},
            {"event": "phase_enter", "phase": "implement", "ts": "2026-09-26T11:00:00Z"},
            {"event": "phase_enter", "phase": "validate", "ts": "2026-09-26T11:15:00Z"},
        ]
        seconds = _phase_duration_summary(events, "validate")["phase_seconds"]
        assert seconds == {"implement": 1800.0 + 900.0, "validate": 1800.0 + 2700.0}


class TestCheckpointTime:
    """PRD-CORE-338 FR06 on the served trw_checkpoint path."""

    def test_leading_stamp_off_the_clock_warns(self, orch_tools: dict[str, Any], frozen_clock: Any) -> None:
        run_path = orch_tools["trw_init"].fn(task_name="cp-skew", complexity_hint="EASY")["run_path"]
        result = orch_tools["trw_checkpoint"].fn(run_path=run_path, message="12:06Z S1 done")
        assert result["clock_mismatch"] == {
            "message_stamp": "12:06Z",
            "machine_ts": "2026-09-26T12:00:00Z",
            "delta_seconds": 360,
        }
        assert "elapsed_seconds" not in result

    def test_boundary_stamp_checkpoint_is_recorded_without_mismatch(
        self, orch_tools: dict[str, Any], frozen_clock: Any
    ) -> None:
        """r1 P1 on the served path: an unrepresentable leading stamp never fails a recorded checkpoint."""
        run_path = orch_tools["trw_init"].fn(task_name="cp-boundary", complexity_hint="EASY")["run_path"]
        result = orch_tools["trw_checkpoint"].fn(run_path=run_path, message="0001-01-01T00:00:00+01:00 progress")
        assert result["recorded"] is True
        assert "clock_mismatch" not in result

    def test_time_fields_failure_keeps_the_checkpoint_recorded(
        self, orch_tools: dict[str, Any], frozen_clock: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The adapter's fail-open arm: any time-field error leaves the persisted checkpoint's response intact."""
        from trw_mcp.state import timekeeping

        def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("time fields broke")

        monkeypatch.setattr(timekeeping, "clock_mismatch", _boom)
        run_path = orch_tools["trw_init"].fn(task_name="cp-failopen", complexity_hint="HARD")["run_path"]
        result = orch_tools["trw_checkpoint"].fn(run_path=run_path, message="12:30Z progress")
        assert result["recorded"] is True
        assert "clock_mismatch" not in result and "elapsed_seconds" not in result

    def test_tracked_checkpoint_carries_elapsed(self, orch_tools: dict[str, Any], frozen_clock: Any) -> None:
        run_path = orch_tools["trw_init"].fn(task_name="cp-tracked", complexity_hint="HARD")["run_path"]
        _seed_tracked_events(Path(run_path))
        result = orch_tools["trw_checkpoint"].fn(run_path=run_path, message="12:01Z progress")
        assert result["elapsed_seconds"] == 7200
        assert "clock_mismatch" not in result

    def test_untracked_checkpoint_is_byte_identical(
        self, orch_tools: dict[str, Any], frozen_clock: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NFR02: an untracked run with no leading stamp gets the pre-change payload, and never reads events."""
        import json

        from trw_mcp.tools import _orchestration_checkpoint as cp

        run_path = orch_tools["trw_init"].fn(task_name="cp-untracked", complexity_hint="STANDARD")["run_path"]
        with monkeypatch.context() as patch:
            patch.setattr(cp, "checkpoint_time_fields", lambda *_a, **_k: {})
            baseline = json.dumps(cp.execute_checkpoint(run_path, "plain progress", None), sort_keys=True)
        reads: list[str] = []
        real = cp.read_jsonl_resilient
        monkeypatch.setattr(cp, "read_jsonl_resilient", lambda path: reads.append(str(path)) or real(path))
        current = json.dumps(cp.execute_checkpoint(run_path, "plain progress", None), sort_keys=True)
        assert current == baseline
        assert reads == []


class TestTimeInputs:
    """PRD-CORE-338 FR02/FR03 on the served tools."""

    def test_slice_done_counts_once(self, orch_tools: dict[str, Any], frozen_clock: Any) -> None:
        from trw_mcp.state._helpers import read_jsonl_resilient
        from trw_mcp.state.timekeeping import load_timeline

        run_path = orch_tools["trw_init"].fn(task_name="slices", complexity_hint="EASY")["run_path"]
        for _ in range(2):
            assert orch_tools["trw_checkpoint"].fn(run_path=run_path, message="S2 done", slice_done="S2")["recorded"]
        orch_tools["trw_checkpoint"].fn(run_path=run_path, message="no slice", slice_done="  ")
        events = read_jsonl_resilient(Path(run_path) / "meta" / "events.jsonl")
        recorded = [event["slice_done"] for event in events if "slice_done" in event]
        assert recorded == ["S2", "S2"]
        assert [slice_id for slice_id, _ in load_timeline(events).done] == ["S2"]

    def test_formation_id_on_run_yaml_tracks_the_run(self, orch_tools: dict[str, Any], frozen_clock: Any) -> None:
        """FR02: join's stamp survives a typed load and makes a MINIMAL run tracked."""
        from trw_mcp.models.run import RunState
        from trw_mcp.state._run_yaml_update import update_run_yaml

        run_path = orch_tools["trw_init"].fn(task_name="member", complexity_hint="EASY")["run_path"]
        assert "time" not in orch_tools["trw_status"].fn(run_path=run_path)
        update_run_yaml(Path(run_path), lambda data: data.update(formation_id="f-1", member_id="impl-1"))
        state = FileStateReader().read_yaml(Path(run_path) / "meta" / "run.yaml")
        assert RunState.model_validate(state).formation_id == "f-1"
        assert "time" in orch_tools["trw_status"].fn(run_path=run_path)


class TestPlannedVsActual:
    """PRD-CORE-338 FR04 wiring and FR08 on real run directories."""

    @staticmethod
    def _write_prd(project: Path, prd_id: str, slices: list[tuple[str, float, float]]) -> None:
        from trw_mcp.models.config import get_config

        prds = project / get_config().prds_relative_path
        prds.mkdir(parents=True, exist_ok=True)
        lines = "".join(
            f"    - {{id: {sid}, estimate_hours_min: {lo}, estimate_hours_max: {hi}}}\n" for sid, lo, hi in slices
        )
        (prds / f"{prd_id}.md").write_text(
            f"---\nprd:\n  id: {prd_id}\n  title: t\n  time:\n    slices:\n{lines}---\n# body\n", encoding="utf-8"
        )

    def test_scoped_prd_estimates_track_and_forecast(
        self, orch_tools: dict[str, Any], frozen_clock: Any, set_project_root: Path
    ) -> None:
        self._write_prd(set_project_root, "PRD-CORE-990", [("S1", 1, 2), ("S2", 2, 3), ("S3", 0.5, 1)])
        run_path = orch_tools["trw_init"].fn(task_name="prd-est", complexity_hint="EASY", prd_scope=["PRD-CORE-990"])[
            "run_path"
        ]
        forecast = orch_tools["trw_status"].fn(run_path=run_path)["time"]["forecast"]
        assert (forecast["basis"], forecast["eta_earliest"], forecast["eta_latest"]) == (
            "declared_estimates",
            "2026-09-26T15:30:00Z",
            "2026-09-26T18:00:00Z",
        )

    def test_single_slice_prd_does_not_track(
        self, orch_tools: dict[str, Any], frozen_clock: Any, set_project_root: Path
    ) -> None:
        self._write_prd(set_project_root, "PRD-CORE-991", [("S1", 1, 2)])
        run_path = orch_tools["trw_init"].fn(task_name="one", complexity_hint="EASY", prd_scope=["PRD-CORE-991"])[
            "run_path"
        ]
        assert "time" not in orch_tools["trw_status"].fn(run_path=run_path)

    def test_suffixed_prd_filename_is_found(
        self, orch_tools: dict[str, Any], frozen_clock: Any, set_project_root: Path
    ) -> None:
        """s3 r1 P2: ``{id}-slug.md`` is resolved like ``{id}.md``."""
        self._write_prd(set_project_root, "PRD-CORE-993", [("S1", 1, 2), ("S2", 1, 2)])
        from trw_mcp.models.config import get_config

        prds = set_project_root / get_config().prds_relative_path
        (prds / "PRD-CORE-993.md").rename(prds / "PRD-CORE-993-time-slug.md")
        run_path = orch_tools["trw_init"].fn(task_name="suffix", complexity_hint="EASY", prd_scope=["PRD-CORE-993"])[
            "run_path"
        ]
        assert orch_tools["trw_status"].fn(run_path=run_path)["time"]["forecast"]["remaining"] == 2

    def test_two_single_slice_prds_do_not_track(
        self, orch_tools: dict[str, Any], frozen_clock: Any, set_project_root: Path
    ) -> None:
        """s3 r1 P2: FR02's two-slice trigger is per PRD, not summed across PRDs."""
        self._write_prd(set_project_root, "PRD-CORE-994", [("S1", 1, 2)])
        self._write_prd(set_project_root, "PRD-CORE-995", [("S1", 1, 2)])
        run_path = orch_tools["trw_init"].fn(
            task_name="two-single", complexity_hint="EASY", prd_scope=["PRD-CORE-994", "PRD-CORE-995"]
        )["run_path"]
        assert "time" not in orch_tools["trw_status"].fn(run_path=run_path)

    def test_single_slice_estimate_kept_for_an_otherwise_tracked_run(
        self, orch_tools: dict[str, Any], frozen_clock: Any, set_project_root: Path
    ) -> None:
        """s3 r2 P2: a one-slice PRD cannot trigger tracking, but its estimate still feeds a tracked run."""
        self._write_prd(set_project_root, "PRD-CORE-996", [("S1", 1, 2)])
        run_path = orch_tools["trw_init"].fn(task_name="one-hard", complexity_hint="HARD", prd_scope=["PRD-CORE-996"])[
            "run_path"
        ]
        forecast = orch_tools["trw_status"].fn(run_path=run_path)["time"]["forecast"]
        assert (forecast["basis"], forecast["remaining"]) == ("declared_estimates", 1)

    def test_first_forecast_is_recorded_once(
        self, orch_tools: dict[str, Any], frozen_clock: Any, set_project_root: Path
    ) -> None:
        from trw_mcp.state._helpers import read_jsonl_resilient

        self._write_prd(set_project_root, "PRD-CORE-992", [("S1", 1, 2), ("S2", 2, 3)])
        run_path = orch_tools["trw_init"].fn(task_name="ff", complexity_hint="EASY", prd_scope=["PRD-CORE-992"])[
            "run_path"
        ]
        for _ in range(2):
            orch_tools["trw_status"].fn(run_path=run_path)
        events = read_jsonl_resilient(Path(run_path) / "meta" / "events.jsonl")
        assert [e["forecast"]["eta_latest"] for e in events if e.get("event") == "time_forecast"] == [
            "2026-09-26T17:00:00Z"
        ]

    def test_deliver_complete_records_planned_vs_actual(
        self, orch_tools: dict[str, Any], frozen_clock: Any, set_project_root: Path
    ) -> None:
        """FR08 at the writer trw_deliver's S20 step calls: tracked gains ``time``, untracked does not."""
        from trw_mcp.state._helpers import read_jsonl_resilient
        from trw_mcp.tools._ceremony_deliver_tool import _log_deliver_event

        def _deliver(run_path: str) -> dict[str, object]:
            results: dict[str, Any] = {"critical_steps_completed": 1}
            _log_deliver_event(set_project_root / ".trw", Path(run_path), results, [], "none", 0.0, "sess")  # type: ignore[arg-type]
            events = read_jsonl_resilient(Path(run_path) / "meta" / "events.jsonl")
            return [e for e in events if e.get("event") == "trw_deliver_complete"][-1]

        tracked = orch_tools["trw_init"].fn(
            task_name="deliv", complexity_hint="HARD", advanced={"target_utc": "2026-09-26T14:30:00Z"}
        )["run_path"]
        _seed_tracked_events(Path(tracked))
        time_record = _deliver(tracked)["time"]
        assert time_record["elapsed_seconds"] == 7200
        assert time_record["target"] == "2026-09-26T14:30:00Z"
        assert [s["actual_hours"] for s in time_record["slices"]] == [1.0, 0.5, 0.5]

        untracked = orch_tools["trw_init"].fn(task_name="deliv-u", complexity_hint="EASY")["run_path"]
        assert "time" not in _deliver(untracked)
