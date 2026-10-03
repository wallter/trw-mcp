"""``no_memory_daemon``: a test that opts in cannot spawn a memory daemon, and a spawn attempt fails it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.daemon import DaemonPaths
from trw_memory.daemon import client as daemon_client
from trw_memory.daemon.client import DaemonClient
from trw_memory.exceptions import DaemonUnreachableError

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._update_external import _run_auto_maintenance
from trw_mcp.models.config import reload_config

pytestmark = pytest.mark.integration


def test_init_then_update_spawns_nothing(no_memory_daemon: list[DaemonPaths], tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    assert not init_project(tmp_path, ide="claude-code")["errors"]
    assert not update_project(tmp_path, ide="claude-code")["errors"]
    assert no_memory_daemon == []


def test_forced_spawn_fails_with_the_fixture_message(no_memory_daemon: list[DaemonPaths], tmp_path: Path) -> None:
    paths = DaemonPaths(user_memory_dir=tmp_path)
    with pytest.raises(AssertionError, match="no_memory_daemon: test spawned a daemon"):
        daemon_client.start_daemon_detached(paths)
    assert no_memory_daemon == [paths]
    no_memory_daemon.clear()  # the deliberate attempt above; teardown would otherwise (correctly) fail the test


def test_embeddings_env_reaches_config_and_skips_auto_maintenance_store(
    no_memory_daemon: list[DaemonPaths], tmp_path: Path
) -> None:
    """TRW_EMBEDDINGS_ENABLED=false gates ``selected_store`` in auto-maintenance (the cached config was reloaded)."""
    result: dict[str, list[str]] = {"warnings": []}
    with patch("trw_mcp.state._store_selection.selected_store") as store:
        _run_auto_maintenance(tmp_path, result)
    store.assert_not_called()
    assert result == {"warnings": []}, "the skipped store check adds no warning"


def test_embeddings_env_true_control_does_reach_the_store(
    no_memory_daemon: list[DaemonPaths], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: the same call with the var flipped back does reach ``selected_store``, so the gate above is the var."""
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "true")
    reload_config()
    result: dict[str, list[str]] = {"warnings": []}
    embedder_down = MagicMock()
    embedder_down.embedder_status.return_value = {"available": False, "reason": "model missing"}
    with patch("trw_mcp.state._store_selection.selected_store", return_value=(embedder_down, "ns")) as store:
        _run_auto_maintenance(tmp_path, result)
    store.assert_called_once()
    assert result["warnings"] == ["Memory daemon cannot encode: model missing"]


def test_autostart_env_alone_makes_attach_refuse_without_spawning(
    no_memory_daemon: list[DaemonPaths], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEMORY_DAEMON_AUTOSTART=false is read at attach time: a free slot raises instead of calling the spawner."""
    spawned: list[DaemonPaths] = []
    monkeypatch.setattr(
        "trw_memory.daemon.client.start_daemon_detached", spawned.append
    )  # the var alone, not the guard
    client = DaemonClient("token", paths=DaemonPaths(user_memory_dir=tmp_path))
    with pytest.raises(DaemonUnreachableError, match=r"auto-start is off \(MEMORY_DAEMON_AUTOSTART=false\)"):
        client._attach()
    assert spawned == []


def test_a_swallowed_spawn_attempt_still_fails_the_test_at_teardown(pytester: pytest.Pytester) -> None:
    """``_run_auto_maintenance`` catches ``Exception``; the recorded attempt must still fail the test."""
    pytester.makeconftest("from tests._memory_fixtures import no_memory_daemon  # noqa: F401\n")
    pytester.makepyfile(
        """
        from pathlib import Path
        from trw_memory.daemon import DaemonPaths
        from trw_memory.daemon import client

        def test_swallows(no_memory_daemon, tmp_path: Path):
            try:
                client.start_daemon_detached(DaemonPaths(user_memory_dir=tmp_path))
            except Exception:
                pass
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*no_memory_daemon: test spawned a daemon*"])
