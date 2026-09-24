"""PRD-CORE-280 FR06 -- one store: no user-tier consent, no ``--no-user-tier``, no federation.

The consent prompt existed because ``~/.trw`` used to be a second, portable
store (PRD-SEC-006 FR06). There is now one store, served by the memory daemon,
and ``user:local`` rows are never pushed, so the installer asks nothing and the
flags that answered the question are unknown arguments.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from tests._memory_fixtures import MemoryDaemon

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / "scripts" / "install-trw.template.py"


@pytest.mark.parametrize("flag", ["--no-user-tier", "--user-tier"])
def test_the_installer_rejects_the_retired_user_tier_flags_as_unknown(flag: str, tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(_TEMPLATE), flag], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 2
    assert f"unrecognized arguments: {flag}" in done.stderr


def test_a_noninteractive_install_mints_the_grant_and_portable_learnings_reach_user_local(
    tmp_path: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No question, no flag: the install pins and grants the checkout, and ``user:local`` just works."""
    from trw_memory.daemon import DaemonClient, read_checkout_grant
    from trw_memory.daemon._grants import granted_namespaces
    from trw_memory.namespaces.identity import resolve_project_namespace

    from tests._install_trw_main_support import drive_main
    from tests._install_trw_pip_target_contract_support import _load_installer_module
    from trw_mcp.models.config import reload_config
    from trw_mcp.state._memory_recall import recall_learnings
    from trw_mcp.state._tier_routing import USER_NAMESPACE
    from trw_mcp.state.memory_adapter import store_learning

    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)

    drive_main(
        _load_installer_module(_TEMPLATE), monkeypatch, target, extra_argv=("--ide", "claude-code"), project_setup=True
    )

    token = read_checkout_grant(target)
    pin = resolve_project_namespace(target)
    assert f"project_namespace: {pin}" in (target / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert set(granted_namespaces(memory_daemon.paths, token) or ()) == {pin, USER_NAMESPACE}

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(target))
    reload_config()
    client = DaemonClient(token, paths=memory_daemon.paths)
    try:
        stored = store_learning(
            target / ".trw", "L-fr06-portable", "prefer larger ollama models", "portable", source_type="human"
        )
        hits = recall_learnings(target / ".trw", "larger ollama models")
        row = asyncio.run(client.get("L-fr06-portable", USER_NAMESPACE))
    finally:
        asyncio.run(client.forget("L-fr06-portable", USER_NAMESPACE))
        reload_config()

    assert stored["status"] == "recorded"
    assert row["entry"]["namespace"] == USER_NAMESPACE
    assert "L-fr06-portable" in [hit["id"] for hit in hits]
