"""PRD-CORE-125-FR03: ``learning_recall_enabled: false`` in ``.trw/config.yaml`` turns recall off.

The gate was lost in a merge on 2026-04-11 (70bb84843) and the key stayed
admitted, so eval arms that set it ran with recall on under a no-recall label.
The key is set the way trw-eval's overlay sets it: a line in the project config.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout
from tests._tools_learning_shared import _get_tools
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state._hook_flags import write_hook_flags


@pytest.fixture(autouse=True)
def _project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")


@pytest.mark.parametrize(("setting", "expected"), [("true", 1), ("false", 0)])
def test_the_config_key_decides_whether_trw_recall_returns_learnings(
    setting: str, expected: int, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A daemon-backed checkout so the search is real search (not the fake
    # store's namespace-agnostic stand-in).
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: daemon_checkout.trw_dir)
    tools = _get_tools()
    tools["trw_learn"].fn(summary="Database pool exhausts under load", detail="Raise the pool size.")
    existing = (daemon_checkout.trw_dir / "config.yaml").read_text(encoding="utf-8")
    (daemon_checkout.trw_dir / "config.yaml").write_text(
        f"{existing}learning_recall_enabled: {setting}\n", encoding="utf-8"
    )
    _reset_config()

    result = tools["trw_recall"].fn(query="database")

    assert len(result["learnings"]) == expected, result


# Lead ruling 2026-09-23: learning_recall_enabled is the MASTER switch. An arm
# labelled no-recall means no learning reaches the agent by any path, so the
# session-start and phase auto-recall paths obey it even when their own finer
# switches are on.


def _config(trw_dir: Path, **fields: object):  # type: ignore[no-untyped-def]
    from trw_mcp.models.config import get_config

    lines = "".join(f"{key}: {str(value).lower()}\n" for key, value in fields.items())
    existing = (trw_dir / "config.yaml").read_text(encoding="utf-8") if (trw_dir / "config.yaml").exists() else ""
    (trw_dir / "config.yaml").write_text(f"{existing}{lines}", encoding="utf-8")
    _reset_config()
    return get_config()


@pytest.mark.parametrize(("master", "expected"), [(True, 1), (False, 0)])
def test_the_master_switch_governs_session_start_recall(
    master: bool, expected: int, daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls

    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: daemon_checkout.trw_dir)
    _get_tools()["trw_learn"].fn(summary="Database pool exhausts under load", detail="Raise the pool size.")
    config = _config(daemon_checkout.trw_dir, learning_recall_enabled=master, session_start_recall_enabled=True)

    learnings, _extra = perform_session_recalls(daemon_checkout.trw_dir, "database", config, FileStateReader())

    assert len(learnings) == expected


def _prompt_hook(root: Path, setting: str) -> str:
    """The bundled UserPromptSubmit hook, with the real lib-trw.sh beside it."""
    import os
    import subprocess

    import trw_mcp

    entries = root / ".trw" / "learnings" / "entries"
    entries.mkdir(parents=True)
    (entries / "L-1.yaml").write_text(
        'id: "L-1"\nstatus: active\nsummary: "Database pool exhausts under load"\nimpact: 0.9\n', encoding="utf-8"
    )
    (root / ".trw" / "config.yaml").write_text(f"learning_recall_enabled: {setting}\n", encoding="utf-8")
    write_hook_flags(root / ".trw", TRWConfig(learning_recall_enabled=setting == "true"))
    hook = Path(trw_mcp.__file__).parent / "data" / "hooks" / "user-prompt-submit.sh"
    env = {key: value for key, value in os.environ.items() if not key.startswith("TRW_AUTO_RECALL")}
    env |= {"CLAUDE_PROJECT_DIR": str(root), "HOME": str(root / ".home")}
    return subprocess.run(
        ["/bin/sh", str(hook)],
        input='{"prompt":"database pool exhausts under load","session_id":"s"}',
        text=True,
        env=env,
        cwd=root,
        capture_output=True,
        check=False,
    ).stdout


@pytest.mark.parametrize(("setting", "injected"), [("true", True), ("false", False)])
def test_the_master_switch_governs_the_prompt_hook_recall(tmp_path: Path, setting: str, injected: bool) -> None:
    """The fourth path: UserPromptSubmit reads the entries mirror itself, outside the Python predicate."""
    assert ("Database pool exhausts under load" in _prompt_hook(tmp_path, setting)) is injected
