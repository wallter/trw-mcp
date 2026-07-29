"""PRD-CORE-231-FR02 / NFR01: the maintain-verify batch sweep.

Covers the behavioral contract (stale transitions persisted, cleared
transitions counted, bulk fetch not N+1) and the NFR01 wall-clock budget
(1000 entries-with-assertions in under 30s).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import structlog
from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._maintain_verify import run_maintain_verify

#: NFR01 budget: 1000 entries-with-assertions must sweep in under 30 seconds.
_SWEEP_ENTRY_COUNT = 1000
_SWEEP_BUDGET_SECONDS = 30.0


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def live_symbol() -> None:\n    return None\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def backend(tmp_path: Path) -> SQLiteBackend:
    return SQLiteBackend(tmp_path / "store" / "memory.db")


def _assertion(pattern: str, first_failed_at: datetime | None) -> Assertion:
    return Assertion(
        type=AssertionType.GREP_PRESENT,
        pattern=pattern,
        target="**/*.py",
        first_failed_at=first_failed_at,
    )


def _store(backend: SQLiteBackend, entry_id: str, assertions: list[Assertion]) -> None:
    now = datetime.now(timezone.utc)
    backend.store(
        MemoryEntry(id=entry_id, content="swept claim", created_at=now, updated_at=now, assertions=assertions)
    )


def _sweep(backend: Any, project_root: Path | None, config: TRWConfig | None = None) -> Any:
    cfg = config or TRWConfig()
    return run_maintain_verify(
        backend,
        assertion_failure_penalty=cfg.assertion_failure_penalty,
        assertion_stale_threshold_days=cfg.assertion_stale_threshold_days,
        batch_limit=cfg.maintain_verify_batch_limit,
        project_root=project_root,
    )


def test_sweep_persists_stale_for_never_recalled_entry(backend: SQLiteBackend, project: Path) -> None:
    """The whole point of the sweep: an entry nobody recalled still goes stale."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-never-recalled", [_assertion("symbol_that_was_deleted", old)])

    summary = _sweep(backend, project, config)

    assert summary.entries_processed == 1
    assert summary.stale_transitions == 1
    entry = backend.get("L-never-recalled")
    assert entry is not None
    assert entry.verification_status == "stale"


def test_sweep_clears_a_recovered_entry(backend: SQLiteBackend, project: Path) -> None:
    """A previously-stale entry whose assertion re-passes is cleared and counted."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-recovered", [_assertion("live_symbol", old)])
    backend.update("L-recovered", verification_status="stale")

    summary = _sweep(backend, project, config)

    assert summary.cleared_transitions == 1
    entry = backend.get("L-recovered")
    assert entry is not None
    assert entry.verification_status is None


def test_sweep_logs_summary_event(backend: SQLiteBackend, project: Path) -> None:
    """NFR04: one durable audit line per run with the transition counts."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-audit", [_assertion("symbol_that_was_deleted", old)])

    with structlog.testing.capture_logs() as logs:
        _sweep(backend, project, config)

    events = [e for e in logs if e["event"] == "maintain_verify_sweep_complete"]
    assert len(events) == 1
    assert events[0]["entries_processed"] == 1
    assert events[0]["stale_transitions"] == 1
    assert "duration_ms" in events[0]


def test_sweep_uses_one_bulk_fetch_not_n_plus_one(backend: SQLiteBackend, project: Path) -> None:
    """NFR01: entries are pulled in a single query regardless of entry count."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    for index in range(25):
        _store(backend, f"L-bulk-{index}", [_assertion("symbol_that_was_deleted", old)])

    calls: list[dict[str, object]] = []
    real_fetch = backend.entries_with_assertions

    class _CountingBackend:
        def entries_with_assertions(self, **kwargs: object) -> list[MemoryEntry]:
            calls.append(kwargs)
            return real_fetch(**kwargs)  # type: ignore[arg-type]

        def update(self, entry_id: str, **fields: object) -> MemoryEntry | None:
            return backend.update(entry_id, **fields)

    summary = _sweep(_CountingBackend(), project, config)

    assert summary.entries_processed == 25
    assert len(calls) == 1
    assert calls[0]["limit"] == config.maintain_verify_batch_limit


def test_sweep_survives_a_broken_entry(backend: SQLiteBackend, project: Path) -> None:
    """One unverifiable row must not abort the remaining sweep."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-good", [_assertion("symbol_that_was_deleted", old)])
    good_entries = backend.entries_with_assertions(limit=10)

    class _ExplodingEntry:
        id = "L-broken"
        verification_status = None
        anchors: list[object] = []

        @property
        def assertions(self) -> list[object]:
            raise RuntimeError("row is corrupt")

    class _MixedBackend:
        def entries_with_assertions(self, **_kwargs: object) -> list[Any]:
            return [_ExplodingEntry(), *good_entries]

        def update(self, entry_id: str, **fields: object) -> MemoryEntry | None:
            return backend.update(entry_id, **fields)

    summary = _sweep(_MixedBackend(), project, config)

    assert summary.entries_processed == 1
    assert backend.get("L-good") is not None


def test_sweep_degrades_without_a_project_root(backend: SQLiteBackend) -> None:
    """PRD-CORE-086-FR09 contract: unresolvable root skips, never raises."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-norr", [_assertion("symbol_that_was_deleted", old)])

    summary = _sweep(backend, None, config)

    assert summary.entries_processed == 1
    # passed is None (unverifiable), so no first_failed_at => no stale verdict.
    entry = backend.get("L-norr")
    assert entry is not None
    assert entry.verification_status is None


def test_batch_limit_rejects_out_of_bounds_values() -> None:
    """NFR03: the sweep-size knob is typed with explicit bounds, not a magic number."""
    import pydantic

    assert TRWConfig(maintain_verify_batch_limit=1).maintain_verify_batch_limit == 1
    with pytest.raises(pydantic.ValidationError):
        TRWConfig(maintain_verify_batch_limit=0)
    with pytest.raises(pydantic.ValidationError):
        TRWConfig(maintain_verify_batch_limit=100_001)


def test_maintain_verify_cli_is_registered_and_dispatches(
    monkeypatch: pytest.MonkeyPatch,
    backend: SQLiteBackend,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The sweep is reachable from the real CLI surface, not just importable."""
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-cli", [_assertion("symbol_that_was_deleted", old)])

    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: project / ".trw")
    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda _trw_dir: backend)

    args = _build_arg_parser().parse_args(["maintain-verify", "--json"])
    assert args.command == "maintain-verify"

    SUBCOMMAND_HANDLERS["maintain-verify"](args)

    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert payload["entries_processed"] == 1
    assert payload["stale_transitions"] == 1
    entry = backend.get("L-cli")
    assert entry is not None
    assert entry.verification_status == "stale"


@pytest.mark.slow
def test_bulk_sweep_1000_entries(backend: SQLiteBackend, project: Path) -> None:
    """NFR01: 1000 entries-with-assertions sweep in under 30s (measured)."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    with backend.transaction():
        for index in range(_SWEEP_ENTRY_COUNT):
            _store(backend, f"L-perf-{index}", [_assertion("symbol_that_was_deleted", old)])

    started = time.monotonic()
    summary = _sweep(backend, project, config)
    elapsed = time.monotonic() - started

    assert summary.entries_processed == _SWEEP_ENTRY_COUNT
    assert summary.stale_transitions == _SWEEP_ENTRY_COUNT
    assert elapsed < _SWEEP_BUDGET_SECONDS, f"sweep took {elapsed:.1f}s (budget {_SWEEP_BUDGET_SECONDS}s)"
