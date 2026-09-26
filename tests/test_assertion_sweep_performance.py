"""PRD-CORE-231-FR02 / NFR01: the maintain-verify batch sweep.

Covers the behavioral contract (stale transitions persisted, cleared
transitions counted, bulk fetch not N+1) and the NFR01 wall-clock budget
(1000 entries-with-assertions in under 30s).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import structlog
from trw_memory.lifecycle.verification_pass import run_maintain_verify
from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from tests._layout import requires_local_timing
from tests._memory_fixtures import DaemonCheckout
from tests._path_isolation import set_current_root
from tests._timing import assert_budget
from trw_mcp.models.config import TRWConfig

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
    # Every test using this fixture drives
    # ``trw_memory.lifecycle.verification_pass.run_maintain_verify`` directly
    # against a raw ``SQLiteBackend`` it owns -- that is trw-memory's own
    # function under trw-memory's own backend, never through trw-mcp's
    # ``selected_store``, so nothing here is affected by PRD-CORE-280 e3.
    # ``test_maintain_verify_cli_is_registered_and_dispatches`` (batch 23b) no
    # longer uses this fixture: it now drives the CLI subcommand through a real
    # ``daemon_checkout``, matching what ``run_maintain_verify_for_project``
    # actually calls (``selected_store``) in production.
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
        # PRD-CORE-244 FR03: a 'verified' verdict now has to clear a recomputed
        # anchor-validity floor, so the sweep takes the floor as a required,
        # config-derived argument rather than assuming one.
        anchor_validity_verified_floor=cfg.anchor_validity_verified_floor,
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
    entry = backend.get("L-never-recalled", namespace="default")
    assert entry is not None
    assert entry.verification_status == "stale"


def test_sweep_clears_a_recovered_entry(backend: SQLiteBackend, project: Path) -> None:
    """A previously-stale entry whose assertion re-passes is cleared and counted.

    PRD-CORE-244 FR03: clearing no longer means writing ``None`` back. ``None``
    is what an entry nobody ever examined reads, so reusing it for "examined and
    found clean" made the two indistinguishable. A cleared entry now carries the
    positive ``"verified"`` verdict and the ``verification_checked_at`` stamp
    that says when the exam happened.
    """
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-recovered", [_assertion("live_symbol", old)])
    backend.update("L-recovered", verification_status="stale", namespace="default")

    summary = _sweep(backend, project, config)

    assert summary.cleared_transitions == 1
    entry = backend.get("L-recovered", namespace="default")
    assert entry is not None
    assert entry.verification_status == "verified"
    assert entry.verification_checked_at != ""


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
    """A corpus smaller than one batch uses one bulk query, not per-entry fetches."""
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
            return backend.update(entry_id, **fields, namespace="default")

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
            return backend.update(entry_id, **fields, namespace="default")

    summary = _sweep(_MixedBackend(), project, config)

    assert summary.entries_processed == 1
    assert backend.get("L-good", namespace="default") is not None


def test_sweep_degrades_without_a_project_root(backend: SQLiteBackend) -> None:
    """PRD-CORE-086-FR09 contract: unresolvable root skips, never raises."""
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    _store(backend, "L-norr", [_assertion("symbol_that_was_deleted", old)])

    summary = _sweep(backend, None, config)

    assert summary.entries_processed == 1
    # passed is None (unverifiable), so no first_failed_at => no stale verdict.
    entry = backend.get("L-norr", namespace="default")
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
    daemon_checkout: DaemonCheckout,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The sweep is reachable from the real CLI surface, not just importable.

    PRD-CORE-280 slice e (batch 23b): ported off ``memory_adapter.get_backend``
    (dead patch since ``run_maintain_verify_for_project`` moved to
    ``selected_store``) onto ``daemon_checkout``, matching the pattern in
    ``test_learn_update_by_id.py``.
    """
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS
    from trw_mcp.state.memory_adapter import store_learning

    set_current_root(daemon_checkout.trw_dir.parent)
    config = TRWConfig()
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    store_learning(
        daemon_checkout.trw_dir,
        "L-cli",
        "swept claim",
        "",
        assertions=[{"type": "grep_present", "pattern": "symbol_that_was_deleted", "target": "**/*.py"}],
    )
    # Backdate the assertion's first-failure clock directly on the daemon store,
    # since store_learning/StoreRequest has no first_failed_at passthrough.
    asyncio.run(
        daemon_checkout.client.update(
            "L-cli",
            daemon_checkout.namespace,
            {"assertions": [_assertion("symbol_that_was_deleted", old).model_dump(mode="json")]},
        )
    )

    args = _build_arg_parser().parse_args(["maintain-verify", "--json"])
    assert args.command == "maintain-verify"

    SUBCOMMAND_HANDLERS["maintain-verify"](args)

    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    # The fake's ``verify`` only counts rows carrying assertions; it checks
    # nothing, so this proves the CLI handler reached ``store.verify`` for the
    # checkout's namespace and printed its summary, not any real sweep verdict.
    assert payload["entries_processed"] == 1
    assert payload["stale_transitions"] == 1
    entry = asyncio.run(daemon_checkout.client.get("L-cli", daemon_checkout.namespace))
    assert entry is not None
    assert entry["entry"]["verification_status"] == "stale"


def _seed_bulk_sweep(backend: SQLiteBackend, config: TRWConfig) -> None:
    old = datetime.now(timezone.utc) - timedelta(days=config.assertion_stale_threshold_days + 10)
    with backend.transaction():
        for index in range(_SWEEP_ENTRY_COUNT):
            _store(backend, f"L-perf-{index}", [_assertion("symbol_that_was_deleted", old)])


@pytest.mark.slow
def test_bulk_sweep_1000_entries(backend: SQLiteBackend, project: Path) -> None:
    """1000 entries-with-assertions all sweep to stale."""
    config = TRWConfig()
    _seed_bulk_sweep(backend, config)

    summary = _sweep(backend, project, config)

    assert summary.entries_processed == _SWEEP_ENTRY_COUNT
    assert summary.stale_transitions == _SWEEP_ENTRY_COUNT


@pytest.mark.slow
@requires_local_timing
def test_bulk_sweep_1000_entries_budget(backend: SQLiteBackend, project: Path) -> None:
    """NFR01: 1000 entries-with-assertions sweep in under 30s (measured)."""
    config = TRWConfig()
    _seed_bulk_sweep(backend, config)

    started = time.monotonic()
    _sweep(backend, project, config)
    elapsed = time.monotonic() - started

    assert_budget("bulk_sweep_1000_entries", elapsed, _SWEEP_BUDGET_SECONDS, "s")
