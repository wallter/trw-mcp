"""Runtime guard: the test suite must never write into the real ``.trw/``.

The incident this guards against
--------------------------------
``_isolate_trw_dir`` hand-enumerated the modules whose ``resolve_trw_dir`` /
``resolve_project_root`` aliases it patched. ``trw_mcp.telemetry.pipeline`` was
never on the list, and a ``TelemetryPipeline`` built by a test kept a daemon
flush thread and an ``atexit`` drain alive past teardown. Both resolved paths
after ``monkeypatch`` reverted, so ~8k fixture events (``duration_ms: 42``,
``framework_version: "v99.0_TEST"``, ``tool_0``..``tool_9``) were written into
the repository's own ``.trw/logs/pipeline-events.jsonl`` — a file
``trw-eval``'s RCA/run_facts extractor reads as real evidence. Under
CONSTITUTION HB-4 that is manufactured evidence, however accidental.

Why a guard and not a longer list
---------------------------------
The previous defence was "a list of modules, plus a grep to confirm nothing
else writes there". Both halves fail silently: the list is only correct until
the next module is added, and a negative grep result is indistinguishable from
a broken search. These tests instead *observe the behaviour* — where paths
actually resolve, and whether a marker this test invented can be found in the
live file. Neither depends on an enumeration or a search being right.
"""

from __future__ import annotations

import atexit
import importlib
import pkgutil
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from tests import _path_isolation

from ._telemetry_pipeline_support import (  # noqa: F401
    _make_event,
    make_configured_pipeline,
    pipeline_cls,
)

#: Monorepo root: ``trw-mcp/tests/<this file>`` -> ``trw-mcp`` -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every real pipeline-events log the suite could reach. ``resolve_project_root``
#: falls back to the CWD, and the suite is invoked from both the monorepo root
#: (``make test-fast``) and from ``trw-mcp/`` (single-file runs), so each CWD has
#: its own live sink. The repo-root one is what the incident polluted and what
#: ``trw-eval``'s RCA/run_facts extractor reads.
_LIVE_PIPELINE_EVENT_LOGS = (
    _REPO_ROOT / ".trw" / "logs" / "pipeline-events.jsonl",
    _REPO_ROOT / "trw-mcp" / ".trw" / "logs" / "pipeline-events.jsonl",
)

#: Cap the read of each live log so a grown file cannot slow the suite down.
_LIVE_READ_TAIL_BYTES = 4 * 1024 * 1024


def _import_every_trw_mcp_module() -> int:
    """Import every ``trw_mcp`` submodule, returning how many are now loaded.

    Modules that legitimately refuse to import (optional extras, ``__main__``)
    are skipped rather than failing the guard — the guard is about *where paths
    resolve*, not about import health, which other tests own.
    """
    import trw_mcp

    for info in pkgutil.walk_packages(trw_mcp.__path__, prefix="trw_mcp."):
        if info.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(info.name)
        except Exception:  # justified: import health is not what this guard asserts
            continue
    return len(_path_isolation._trw_mcp_modules())


class TestNoModuleEscapesIsolation:
    """Every module that binds a path resolver must resolve into the test tmp dir."""

    def test_no_trw_mcp_module_binds_the_real_path_resolvers(self) -> None:
        """Import the whole package tree; no alias may still point at a real resolver.

        This is the test that would have caught the incident directly: before the
        fix, ``trw_mcp.telemetry.pipeline`` (plus ``publisher``, ``sender``,
        ``client``, ``startup``, ``scoring._utils``, ``state.analytics.report``,
        and others) all held the genuine ``resolve_trw_dir``.

        Walking the tree also covers modules no test has imported yet, so a
        module added tomorrow is checked the first time this runs — the property
        the hand-enumerated list could not have.
        """
        loaded = _import_every_trw_mcp_module()

        # Non-vacuity control: an empty "escaped" list proves nothing unless the
        # sweep is demonstrably finding and rebinding aliases in the first place.
        isolated = _path_isolation.isolated_alias_count()
        assert loaded > 100, f"expected the trw_mcp tree to be loaded, got {loaded} modules"
        assert isolated > 0, "control failed: no isolated resolver aliases found at all"

        escaped = _path_isolation.unisolated_aliases()
        assert escaped == [], (
            f"these modules still resolve paths against the REAL repository and will write into it: {escaped}"
        )

    def test_telemetry_pipeline_resolves_its_jsonl_inside_tmp_path(self, tmp_path: Path) -> None:
        """The exact call that polluted the repo must land under this test's tmp_path.

        ``_resolve_jsonl_path`` is a staticmethod that re-resolves on every
        flush, so this asserts the property at the precise seam the leaked
        thread exploited.
        """
        from trw_mcp.telemetry.pipeline import TelemetryPipeline

        resolved = TelemetryPipeline._resolve_jsonl_path()

        assert resolved not in _LIVE_PIPELINE_EVENT_LOGS
        assert tmp_path in resolved.parents, f"pipeline JSONL resolved outside tmp_path: {resolved}"


class TestLivePipelineEventsFileIsNeverWritten:
    """A real enqueue+flush must be observable in tmp and absent from the live file."""

    def test_flushed_events_land_in_tmp_and_never_in_the_live_log(
        self,
        pipeline_cls: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Drive the real write path and prove the marker reaches only tmp_path.

        Asserting on a unique marker rather than on the live file's size/mtime
        keeps this immune to the production ``trw-mcp`` server writing that file
        concurrently: another writer can never emit this test's uuid.
        """
        marker = f"isolation-guard-{uuid.uuid4()}"
        pipeline, trw_dir = make_configured_pipeline(pipeline_cls, tmp_path, monkeypatch)

        pipeline.enqueue(_make_event(tool_name=marker))
        result = pipeline.flush_now()

        tmp_log = trw_dir / "logs" / "pipeline-events.jsonl"
        assert tmp_log.exists(), "flush wrote no local JSONL at all — the control failed"
        assert marker in tmp_log.read_text(encoding="utf-8"), (
            f"control failed: marker absent from the tmp log, so its absence "
            f"from the live log proves nothing (flush result: {result})"
        )

        assert marker not in _read_live_logs_tail(), (
            f"test event {marker!r} reached a live pipeline-events log — the suite "
            "is writing fixture data into what trw-eval reads as real evidence"
        )

    def test_flush_after_the_local_patch_is_dropped_never_reaches_the_live_log(
        self,
        pipeline_cls: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reproduce the window the leaked flush thread actually wrote in.

        The pollution did not happen inside a test body — every test that
        constructs a pipeline patches ``pipeline.resolve_trw_dir`` for its own
        synchronous assertions. It happened when the daemon timer ticked (or the
        atexit drain ran) *after* those function-scoped patches were reverted.
        ``monkeypatch.undo()`` reproduces that revert exactly, and the flush that
        follows is the write that reached the repository.
        """
        marker = f"isolation-guard-late-{uuid.uuid4()}"
        pipeline, _ = make_configured_pipeline(pipeline_cls, tmp_path, monkeypatch)
        pipeline.enqueue(_make_event(tool_name=marker))

        monkeypatch.undo()  # the teardown the leaked thread used to outlive
        pipeline.flush_now()

        # Control: the event really was written somewhere, so its absence from
        # the live log below is a fact about routing, not about a no-op flush.
        late_log = _path_isolation.resolve_trw_dir() / "logs" / "pipeline-events.jsonl"
        assert late_log.exists() and marker in late_log.read_text(encoding="utf-8"), (
            f"control failed: post-teardown flush wrote nothing to {late_log}"
        )

        assert marker not in _read_live_logs_tail(), (
            f"post-teardown flush wrote {marker!r} into a live pipeline-events log — "
            "this is the exact defect: path resolution outlived its patch"
        )


class TestPipelineInstancesAreStopped:
    """Both leak vectors of an unstopped pipeline must be closed at teardown."""

    def test_pipeline_cls_fixture_tracks_instances_built_by_production_code(self, pipeline_cls: Any) -> None:
        """Tracking must cover ``get_instance()``, not just direct construction.

        The leaked instances came from several construction sites; a factory that
        only tracked direct ``pipeline_cls(...)`` calls would have missed the
        singleton that production code builds mid-test.
        """
        tracked = pipeline_cls._test_tracked_instances
        before = len(tracked)

        instance = pipeline_cls.get_instance()

        assert len(tracked) == before + 1
        assert any(item is instance for item in tracked)

    def test_stop_clears_both_the_flush_thread_and_the_atexit_drain(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``stop(drain=False)`` must end the thread AND unregister the atexit hook.

        The atexit drain is the second, quieter leak vector: it survives every
        fixture teardown and fires at interpreter shutdown, long after any patch
        that made the pipeline's environment fake has gone.
        """
        pipeline, _ = make_configured_pipeline(
            pipeline_cls, tmp_path, monkeypatch, pipeline_kwargs={"flush_interval_secs": 30.0}
        )
        pipeline.enqueue(_make_event())

        assert pipeline._thread is not None and pipeline._thread.is_alive()
        assert pipeline._atexit_registered

        pipeline.stop(drain=False, timeout=5.0)

        assert not pipeline._thread.is_alive()
        assert not pipeline._atexit_registered
        assert pipeline._thread not in threading.enumerate()
        # Re-unregistering a hook that is already gone is a no-op, so this only
        # confirms stop() left nothing behind for interpreter shutdown to run.
        atexit.unregister(pipeline._atexit_drain)


def _read_live_logs_tail() -> str:
    """Return the concatenated tails of every live pipeline-events log."""
    chunks: list[str] = []
    for log in _LIVE_PIPELINE_EVENT_LOGS:
        if not log.exists():
            continue
        with log.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _LIVE_READ_TAIL_BYTES))
            chunks.append(handle.read().decode("utf-8", errors="replace"))
    return "\n".join(chunks)
