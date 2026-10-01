"""``trw-mcp doctor`` finds user-tier learnings that were copied into the project (L-5ist).

Before the fix every ``trw_learn`` wrote a YAML sidecar and an ``index.yaml`` row under the project's
``.trw/learnings/`` -- including user-tier rows, in a folder the installed gitignore tracks. The row warns
with the count and the fix, and never deletes anything (HB-2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import TRWConfig
from trw_mcp.server._doctor_user_yaml import user_yaml_row
from trw_mcp.state._store_selection import StoreUnavailableError
from trw_mcp.state._tier_routing import USER_NAMESPACE

PROJECT_NAMESPACE = "project:demo"

pytestmark = pytest.mark.usefixtures("stub_cli_version_probes")


def _pin(store: FakeMemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (store, PROJECT_NAMESPACE))


def _sidecar(target: Path, learning_id: str, slug: str) -> Path:
    """A project YAML sidecar as ``save_learning_entry`` writes it: named by summary slug, id inside."""
    entries = target / ".trw" / "learnings" / "entries"
    entries.mkdir(parents=True, exist_ok=True)
    path = entries / f"2026-09-30-{slug}.yaml"
    path.write_text(f"id: {learning_id}\nsummary: {slug}\nstatus: active\n", encoding="utf-8")
    return path


def _index(target: Path, *ids: str) -> Path:
    path = target / ".trw" / "learnings" / "index.yaml"
    rows = "".join(f"  - id: {lid}\n    summary: s\n" for lid in ids)
    path.write_text(f"entries:\n{rows}total_count: {len(ids)}\n", encoding="utf-8")
    return path


def test_warns_with_count_and_fix_when_a_user_tier_row_has_a_project_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore()
    store.put("personal one", USER_NAMESPACE, {"entry_id": "L-u1"})
    store.put("personal two", USER_NAMESPACE, {"entry_id": "L-u2"})
    store.put("a project row", PROJECT_NAMESPACE, {"entry_id": "L-p1"})
    _pin(store, monkeypatch)
    leaked = [_sidecar(tmp_path, "L-u1", "personal-one"), _sidecar(tmp_path, "L-u2", "personal-two")]
    kept = _sidecar(tmp_path, "L-p1", "a-project-row")
    index = _index(tmp_path, "L-u1", "L-u2", "L-p1")

    status, message = user_yaml_row(tmp_path, TRWConfig())

    assert status == "WARN"
    assert message.startswith("2 user-tier learning(s)")
    assert "L-u1" in message and "L-u2" in message and "L-p1" not in message
    assert "2 row(s)" in message and str(index) in message
    assert "delete" in message and "canonical row" in message
    assert "not done for you" in message
    # HB-2: the row only reports. Every file is still there, byte for byte.
    assert all(path.is_file() for path in [*leaked, kept, index])


def test_warns_for_an_index_row_left_behind_after_the_sidecar_was_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The index row carries the summary too: deleting only the files must not turn the row green."""
    store = FakeMemoryStore()
    store.put("personal one", USER_NAMESPACE, {"entry_id": "L-u1"})
    _pin(store, monkeypatch)
    _sidecar(tmp_path, "L-p1", "a-project-row")  # the entries dir exists; no user sidecar remains
    _index(tmp_path, "L-u1", "L-p1")

    status, message = user_yaml_row(tmp_path, TRWConfig())

    assert status == "WARN"
    assert message.startswith("0 user-tier learning(s)")
    assert "1 row(s)" in message


def test_passes_when_only_project_rows_have_copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeMemoryStore()
    store.put("a project row", PROJECT_NAMESPACE, {"entry_id": "L-p1"})
    store.put("personal one", USER_NAMESPACE, {"entry_id": "L-u1"})
    _pin(store, monkeypatch)
    _sidecar(tmp_path, "L-p1", "a-project-row")

    status, message = user_yaml_row(tmp_path, TRWConfig())

    assert status == "PASS"
    assert "no user-tier learning has a copy" in message


def test_passes_when_there_is_no_entries_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin(FakeMemoryStore(), monkeypatch)

    status, _message = user_yaml_row(tmp_path, TRWConfig())

    assert status == "PASS"


def test_skips_when_the_store_cannot_be_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _sidecar(tmp_path, "L-u1", "personal-one")

    def _unavailable(_trw_dir: Path) -> object:
        raise StoreUnavailableError("daemon not running; run trw-mcp doctor")

    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", _unavailable)

    status, message = user_yaml_row(tmp_path, TRWConfig())

    assert status == "SKIP"
    assert "daemon not running" in message


def test_the_row_is_in_the_catalogue_and_runs_through_the_facade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server._doctor_checks_registry import CHECKS
    from trw_mcp.server._subcommands_doctor import _check_user_yaml

    assert ("user_tier_yaml", "_check_user_yaml") in CHECKS
    store = FakeMemoryStore()
    store.put("personal one", USER_NAMESPACE, {"entry_id": "L-u1"})
    _pin(store, monkeypatch)
    _sidecar(tmp_path, "L-u1", "personal-one")

    result = _check_user_yaml(tmp_path, TRWConfig())

    assert (result.name, result.status) == ("user_tier_yaml", "WARN")
