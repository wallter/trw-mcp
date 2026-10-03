"""PRD-CORE-333: the learning publisher never uploads a learning the quarantine ledger blocks (CORE-333-PUBLISHER-BYPASS).

The publisher reads the YAML mirror directly, not through a filtered backend read, so a
quarantined learning was POSTed whenever learning sharing was on. A row is blocked by its
content (the ledger's hash key, whatever its id) or by its id in this project's namespace;
a ledger that cannot be read publishes nothing.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning

pytestmark = pytest.mark.usefixtures("governing_project")


def _publish(trw_dir: Path) -> tuple[dict[str, object], list[str]]:
    from trw_mcp.telemetry.publisher import publish_learnings

    cfg = _make_config(platform_telemetry_enabled=True, learning_sharing_enabled=True)
    with (
        patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
        patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.telemetry.publisher._post_learning", return_value=True) as post,
    ):
        result = publish_learnings()
    sent = [call.args[1]["source_learning_id"] for call in post.call_args_list]
    return dict(result), sent


def test_a_quarantined_learning_is_never_published(tmp_path: Path) -> None:
    from trw_memory.models.config import MemoryConfig
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.security.quarantine_ledger import LedgerIdentity, ledger_for_config

    trw_dir = tmp_path / ".trw"
    entries = trw_dir / "learnings" / "entries"
    _write_learning(entries, "L-visible.yaml", _make_learning(summary="safe", detail="fine"))
    _write_learning(entries, "L-byhash.yaml", _make_learning(summary="zebra poisoned", detail="payload"))
    _write_learning(entries, "L-byid.yaml", _make_learning(summary="flagged by id", detail="x"))
    ledger = ledger_for_config(MemoryConfig())
    # Quarantined under another id in another namespace: only the content identifies it.
    poisoned = MemoryEntry(id="Q-elsewhere", namespace="other", content="zebra poisoned", detail="payload")
    ledger.append(LedgerIdentity.of(poisoned), "quarantined", actor="system")

    _pin(trw_dir)  # the id key is namespace-qualified
    ledger.append(LedgerIdentity(namespace="proj-a", entry_id="L-byid"), "rejected", actor="reviewer")

    result, sent = _publish(trw_dir)

    assert sent == ["L-visible"], sent
    assert result["published"] == 1


def test_an_unreadable_ledger_publishes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from trw_memory.security import quarantine_ledger

    trw_dir = tmp_path / ".trw"
    _write_learning(trw_dir / "learnings" / "entries", "L-visible.yaml", _make_learning())

    def _broken(self: object) -> object:
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(quarantine_ledger.QuarantineLedger, "view", _broken)

    _pin(trw_dir)
    result, sent = _publish(trw_dir)

    assert sent == [] and result["published"] == 0


def test_a_learning_quarantined_by_its_source_id_is_never_published(tmp_path: Path) -> None:
    from trw_memory.models.config import MemoryConfig
    from trw_memory.security.quarantine_ledger import LedgerIdentity, ledger_for_config

    trw_dir = tmp_path / ".trw"
    entries = trw_dir / "learnings" / "entries"
    _write_learning(entries, "L-pulled.yaml", {**_make_learning(summary="pulled"), "source_learning_id": "S-9"})
    _write_learning(entries, "L-visible.yaml", _make_learning(summary="safe"))
    _pin(trw_dir)
    ledger_for_config(MemoryConfig()).append(
        LedgerIdentity(namespace="x", entry_id="x", source_learning_id="S-9"), "quarantined", actor="system"
    )

    _result, sent = _publish(trw_dir)

    assert sent == ["L-visible"]


def test_an_unreadable_pin_publishes_nothing(tmp_path: Path) -> None:
    trw_dir = tmp_path / ".trw"
    _write_learning(trw_dir / "learnings" / "entries", "L-visible.yaml", _make_learning())
    (trw_dir / "config.yaml").write_text("project_namespace: 7\n", encoding="utf-8")  # ill-typed: unreadable

    result, sent = _publish(trw_dir)

    assert sent == [] and result["published"] == 0


def test_an_unpinned_checkout_is_still_checked_by_content(tmp_path: Path) -> None:
    from trw_memory.models.config import MemoryConfig
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.security.quarantine_ledger import LedgerIdentity, ledger_for_config

    trw_dir = tmp_path / ".trw"
    entries = trw_dir / "learnings" / "entries"
    _write_learning(entries, "L-poison.yaml", _make_learning(summary="zebra poisoned", detail="payload"))
    _write_learning(entries, "L-visible.yaml", _make_learning(summary="safe"))
    poisoned = MemoryEntry(id="Q", namespace="other", content="zebra poisoned", detail="payload")
    ledger_for_config(MemoryConfig()).append(LedgerIdentity.of(poisoned), "quarantined", actor="system")

    _result, sent = _publish(trw_dir)

    assert sent == ["L-visible"]


def _pin(trw_dir: Path) -> None:
    with (trw_dir / "config.yaml").open("a", encoding="utf-8") as config:
        config.write("project_namespace: proj-a\n")
