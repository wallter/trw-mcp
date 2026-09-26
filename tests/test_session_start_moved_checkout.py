"""PRD-CORE-253 FR01 — the moved-checkout observation at session start.

Detection only. The step must report the evidence and the repair command, must
never re-label a row: a silent auto-merge on a path change cannot be told
apart from two different projects that occupied the same path over time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("trw_memory.daemon")

from trw_memory.namespaces.identity import resolve_project_namespace

from trw_mcp.tools._moved_checkout_readback import step_moved_checkout


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real git checkout, resolved as the caller's project root."""
    root = tmp_path / "renamed-project"
    root.mkdir()
    _git("init", "-q", cwd=root)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    return root


@pytest.fixture
def user_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user memory DIRECTORY the step reads, left empty of stores."""
    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path / "userhome" / "memory"


def _seed(user_memory_dir: Path, namespace: str, count: int) -> None:
    """Write rows through the REAL backend factory, not a hand-placed file.

    Seeding by constructing ``SQLiteBackend(<user_dir>/memory.db)`` directly is
    what let an earlier version of this suite pass while the production step was
    dead: the test created the exact path the step stat-ed, and nothing else
    ever did. Going through ``create_backend_from_config`` means the rows land
    wherever production would actually put them.
    """
    from datetime import datetime, timezone

    from trw_memory.integrations._backend import create_backend_from_config
    from trw_memory.models.config import MemoryConfig
    from trw_memory.models.memory import MemoryEntry

    user_memory_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    config = MemoryConfig(storage_path=str(user_memory_dir))
    with create_backend_from_config(config, namespace) as backend:
        for index in range(count):
            backend.store(
                MemoryEntry(
                    id=f"M-{namespace[-4:]}-{index}",
                    content=f"row {index} in {namespace}",
                    namespace=namespace,
                    created_at=now,
                    updated_at=now,
                )
            )


def test_no_user_store_reports_nothing(checkout: Path, user_store: Path) -> None:
    """A first-ever session has no store to read; that is not a signal."""
    assert not user_store.exists()

    # "absent", not "not_measured": with no store there are no sibling
    # namespaces to have moved away from, so nothing was missed.
    assert step_moved_checkout() == {"status": "absent"}


def test_the_step_reads_the_same_census_the_diagnose_tool_does(checkout: Path, user_store: Path) -> None:
    """One definition of "what namespaces exist", or the two surfaces diverge.

    This is the non-vacuity guard for the seeding fix: the rows below are written
    by the production factory, and BOTH the tool and the session-start step must
    see them. An earlier version of this step stat-ed a hardcoded
    ``<user_dir>/memory.db`` that no production write path ever created.
    """
    from trw_memory.models.config import MemoryConfig
    from trw_memory.namespaces.curate import store_census

    stale = f"project:renamed-project-{'0' * 8}"
    _seed(user_store, stale, 2)

    census = store_census(MemoryConfig(storage_path=str(user_store)))

    assert census.get(stale) == 2, f"the production seed is invisible to the shared census: {census}"
    assert step_moved_checkout()["status"] == "measured"


def test_a_same_slug_sibling_with_rows_is_reported_with_its_repair(checkout: Path, user_store: Path) -> None:
    """The exact shape a rename leaves: empty current identity, populated sibling."""
    current = resolve_project_namespace()
    stale = f"project:renamed-project-{'0' * 8}"
    assert stale != current
    _seed(user_store, stale, 3)

    observed = step_moved_checkout()

    assert observed["status"] == "measured"
    assert observed["current_namespace"] == current
    assert observed["current_rows"] == 0
    assert observed["candidates"] == [{"namespace": stale, "rows": 3}]
    assert observed["repair_command"] == f"trw-memory namespace rename {stale} {current}"


def test_the_step_never_writes(checkout: Path, user_store: Path) -> None:
    """Detection, not correction: the rows stay exactly where they were."""
    current = resolve_project_namespace()
    stale = f"project:renamed-project-{'0' * 8}"
    _seed(user_store, stale, 2)

    step_moved_checkout()

    from trw_memory.models.config import MemoryConfig
    from trw_memory.namespaces.curate import store_census

    census = store_census(MemoryConfig(storage_path=str(user_store)))
    assert census.get(stale) == 2
    assert census.get(current, 0) == 0


def test_a_populated_current_identity_reports_nothing(checkout: Path, user_store: Path) -> None:
    """Otherwise every project with a same-slug neighbour would trip the advisory."""
    current = resolve_project_namespace()
    _seed(user_store, f"project:renamed-project-{'0' * 8}", 2)
    _seed(user_store, current, 1)

    assert step_moved_checkout() == {"status": "absent"}


def test_a_different_slug_reports_nothing(checkout: Path, user_store: Path) -> None:
    """A fresh clone of an unrelated project produces none of the signal."""
    _seed(user_store, f"project:something-else-{'0' * 8}", 5)

    assert step_moved_checkout() == {"status": "absent"}


def test_the_step_is_registered_in_the_session_start_table() -> None:
    """An unwired step reports nothing, however correct it is."""
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_step_table import SESSION_START_STEPS

    keys = [step.key for step in SESSION_START_STEPS]
    assert "moved_checkout" in keys, "the step exists but session start never runs it"
    step = next(candidate for candidate in SESSION_START_STEPS if candidate.key == "moved_checkout")
    assert not step.critical, "a rename advisory must never take down the mandated first action"
    assert hasattr(_ceremony, step.attr), "the driver resolves the adapter by name off the ceremony facade"
