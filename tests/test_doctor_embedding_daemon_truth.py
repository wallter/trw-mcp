"""The doctor reports the memory daemon's embedding state, not only this project's flag (UF-MEM-04).

``embeddings_enabled: false`` in ``.trw/config.yaml`` is trw-mcp's own switch. The memory daemon is one process per
user, serving every project, and reads its own ``MEMORY_EMBEDDINGS_ENABLED``; it kept loading its model while the
doctor said "no embedding model is loaded". The row now asks the daemon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.server._doctor_embedding_egress import embedding_egress_report


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    path.mkdir()
    return path


def test_flag_off_but_the_daemon_has_a_model_is_a_warning_that_names_the_real_switch(
    trw_dir: Path, fake_memory_store: FakeMemoryStore
) -> None:
    fake_memory_store.embedder = {"available": True, "model": "org/m", "loaded": True, "reason": None}

    status, message = embedding_egress_report("org/m", embeddings_enabled=False, trw_dir=trw_dir)

    assert status == "WARN"
    assert "no embedding model is loaded" not in message
    assert "daemon" in message and "MEMORY_EMBEDDINGS_ENABLED=false" in message
    assert "every project" in message  # the daemon is shared: this project's flag cannot switch it off


def test_flag_off_and_the_daemon_has_no_model_keeps_the_pass(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    fake_memory_store.embedder = {"available": False, "model": "", "loaded": False, "reason": "embeddings_disabled"}

    status, message = embedding_egress_report("org/m", embeddings_enabled=False, trw_dir=trw_dir)

    assert status == "PASS" and "no embedding model is loaded" in message


def test_flag_off_with_the_daemon_unreachable_says_it_was_not_asked(
    trw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state import _store_selection

    def down(_trw_dir: Path) -> object:
        raise _store_selection.StoreUnavailableError("daemon unreachable")

    monkeypatch.setattr(_store_selection, "selected_store", down)

    status, message = embedding_egress_report("org/m", embeddings_enabled=False, trw_dir=trw_dir)

    assert status == "PASS"
    assert "could not be asked" in message and "no embedding model is loaded" not in message


def test_flag_on_is_unchanged_by_the_daemon_question(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    fake_memory_store.embedder = {"available": True, "model": "org/m", "loaded": True, "reason": None}

    status, message = embedding_egress_report("org/m", embeddings_enabled=True, trw_dir=trw_dir)

    assert "cache" in message and "daemon" not in message
    assert status in ("PASS", "WARN")


def test_the_field_description_says_what_the_project_flag_governs() -> None:
    """UF-MEM-04-CONFIG-SCOPE: the flag is trw-mcp-process-only; it cannot switch off the shared per-user daemon."""
    from trw_mcp.models.config._field_descriptions import FIELD_DESCRIPTIONS

    description = FIELD_DESCRIPTIONS["embeddings_enabled"]

    assert "trw-mcp" in description
    assert "MEMORY_EMBEDDINGS_ENABLED" in description, "the daemon-wide switch must be named"
    assert "does not" in description and "daemon" in description
    assert "falls back to keyword search only" not in description, "the old claim outlived the behaviour"
