"""PRD-CORE-267 FR03 — the shared-anchor-set migration.

Drives the REAL SQLite backend through the same adapter the server uses, so
"dry-run wrote nothing" and "apply cleared exactly these rows" are statements
about persisted state rather than about a mock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.tools._anchor_migration import clear_shared_anchor_sets

_SHARED = [
    {
        "file": "example/app/_logging.py",
        "symbol_name": "_redact_secrets",
        "symbol_type": "function",
        "signature": "def _redact_secrets(payload):",
        "line_range": (1, 1),
    },
    {
        "file": "example/app/middleware/tenant.py",
        "symbol_name": "_ancestor_ids_setting",
        "symbol_type": "function",
        "signature": "def _ancestor_ids_setting():",
        "line_range": (1, 1),
    },
]

_OTHER = [
    {
        "file": "trw-mcp/src/trw_mcp/state/anchor_generation.py",
        "symbol_name": "generate_anchors",
        "symbol_type": "function",
        "signature": "def generate_anchors(...):",
        "line_range": (1, 1),
    },
]


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> object:
    """A real SQLite-backed store in a scratch project — never the repo's own."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    from trw_mcp.models.config import _reset_config

    _reset_config()
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    from trw_mcp.state._memory_connection import get_backend

    return get_backend(trw_dir)


def _store(backend: object, entry_id: str, anchors: list[dict[str, object]]) -> None:
    """Seed one anchored row directly (fixture setup, not the write path)."""
    from trw_memory.models.memory import Anchor, MemoryEntry

    entry = MemoryEntry(
        id=entry_id,
        content=f"summary for {entry_id}",
        detail=f"detail for {entry_id}",
        namespace="default",
        anchors=[Anchor.model_validate(anchor) for anchor in anchors],
        anchor_validity=1.0,
    )
    backend.store(entry)  # type: ignore[attr-defined]


def _seed(backend: object, *, shared: int, other: int) -> None:
    for index in range(shared):
        _store(backend, f"L-shared{index:03d}", _SHARED)
    for index in range(other):
        _store(backend, f"L-other{index:03d}", _OTHER)


def _anchors_of(backend: object, entry_id: str) -> list[object]:
    entry = backend.get(entry_id, namespace="default")  # type: ignore[attr-defined]
    assert entry is not None, entry_id
    return list(entry.anchors)


class TestSharedAnchorSetMigration:
    def test_dry_run_reports_without_writing(self, backend: object) -> None:
        """FR03: the default reports the measurement and mutates nothing."""
        _seed(backend, shared=12, other=3)

        summary = clear_shared_anchor_sets(backend, threshold=10)

        assert summary.dry_run is True
        assert summary.sets_over_threshold == 1
        assert summary.entries_affected == 12
        assert summary.entries_cleared == 0
        assert summary.entries_scanned == 15
        # Nothing moved on disk — both groups keep their anchors.
        assert len(_anchors_of(backend, "L-shared000")) == 2
        assert len(_anchors_of(backend, "L-other000")) == 1

    def test_apply_clears_and_is_idempotent(self, backend: object) -> None:
        """FR03: apply clears exactly the over-threshold set; a second run is a no-op."""
        _seed(backend, shared=12, other=3)

        applied = clear_shared_anchor_sets(backend, threshold=10, apply=True)

        assert applied.dry_run is False
        assert applied.entries_cleared == 12
        assert applied.clear_failures == 0
        for index in range(12):
            entry = backend.get(f"L-shared{index:03d}", namespace="default")  # type: ignore[attr-defined]
            assert entry is not None
            assert entry.anchors == []
            assert entry.anchor_validity is None
        # The under-threshold group is untouched, anchors AND score.
        under = backend.get("L-other000", namespace="default")  # type: ignore[attr-defined]
        assert len(under.anchors) == 1
        assert under.anchor_validity == 1.0

        second = clear_shared_anchor_sets(backend, threshold=10, apply=True)
        assert second.sets_over_threshold == 0
        assert second.entries_affected == 0
        assert second.entries_cleared == 0

    def test_group_exactly_at_threshold_is_selected(self, backend: object) -> None:
        """The predicate is 'at least', so a group of exactly N qualifies."""
        _seed(backend, shared=10, other=0)

        summary = clear_shared_anchor_sets(backend, threshold=10)

        assert summary.sets_over_threshold == 1
        assert summary.entries_affected == 10

    def test_group_below_threshold_is_never_selected(self, backend: object) -> None:
        """A cluster of nine is left alone — topical convergence is not a defect."""
        _seed(backend, shared=9, other=0)

        summary = clear_shared_anchor_sets(backend, threshold=10)

        assert summary.sets_over_threshold == 0
        assert summary.entries_affected == 0
        assert summary.entries_scanned == 9

    def test_unanchored_store_reports_zero_rather_than_failing(self, backend: object) -> None:
        """A store with no anchors at all is an ordinary answer, not an error."""
        _store(backend, "L-bare000", [])

        summary = clear_shared_anchor_sets(backend, threshold=10)

        assert summary.entries_scanned == 1
        assert summary.sets_over_threshold == 0
        assert summary.entries_affected == 0

    def test_anchor_set_identity_ignores_line_drift(self, backend: object) -> None:
        """Two entries naming the same (file, symbol) pairs are ONE set.

        Recorded line numbers drift as the file is edited; the fabrication being
        cleaned up is about WHICH symbols were named, not where they sat.
        """
        drifted = [dict(anchor, line_range=(400, 400)) for anchor in _SHARED]
        for index in range(5):
            _store(backend, f"L-a{index:03d}", _SHARED)
        for index in range(5):
            _store(backend, f"L-b{index:03d}", drifted)

        summary = clear_shared_anchor_sets(backend, threshold=10)

        assert summary.sets_over_threshold == 1
        assert summary.entries_affected == 10


class TestMigrationCli:
    def test_cli_dry_run_reports_and_writes_nothing(self, backend: object, capsys: pytest.CaptureFixture[str]) -> None:
        """The maintain-verify subcommand routes to the migration and stays read-only."""
        import argparse
        import json

        from trw_mcp.server._subcommands_maintain import _run_maintain_verify

        _seed(backend, shared=12, other=0)
        args = argparse.Namespace(
            clear_shared_anchors=True,
            apply=False,
            namespace=None,
            as_json=True,
        )

        _run_maintain_verify(args)

        payload = json.loads(capsys.readouterr().out)
        assert payload["dry_run"] is True
        assert payload["entries_affected"] == 12
        assert payload["entries_cleared"] == 0
        assert len(_anchors_of(backend, "L-shared000")) == 2
