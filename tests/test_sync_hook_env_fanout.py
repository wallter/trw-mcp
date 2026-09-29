"""``instructions sync`` writes every synced client's hook-env file, as init and update do (PRD-CORE-305 FR07 row b).

It used to write one profile's ``hook-env.d/<key>.sh``: for ``--client all`` the serving config's profile, for
``auto`` the first detected instruction target. Every other installed client's hooks kept reading stale nudge and
session settings until the next init or update.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._hook_env import _hook_env_key
from trw_mcp.bootstrap._utils import SUPPORTED_IDES
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._profiles import resolve_client_profile


def _project(tmp_path: Path, target_platforms: list[str] | None = None) -> Path:
    (tmp_path / ".trw" / "runtime").mkdir(parents=True)
    if target_platforms is not None:
        lines = "".join(f"  - {client}\n" for client in target_platforms)
        (tmp_path / ".trw" / "config.yaml").write_text(f"target_platforms:\n{lines}", encoding="utf-8")
    return tmp_path


def _written(project: Path) -> set[str]:
    return {p.stem for p in (project / ".trw" / "runtime" / "hook-env.d").glob("*.sh")}


def _keys(*client_ids: str) -> set[str]:
    return {_hook_env_key(resolve_client_profile(client_id)) for client_id in client_ids}


def _sync(project: Path, client: str) -> list[str]:
    from trw_mcp.state.claude_md._hook_policy import refresh_hook_policy

    # The serving config names claude-code: the old code wrote that one profile for ``all``.
    return refresh_hook_policy(project / ".trw", project, TRWConfig(), client)


def test_all_writes_every_supported_clients_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _sync(project, "all")
    assert _written(project) == _keys(*SUPPORTED_IDES)


def test_auto_writes_every_recorded_clients_file(tmp_path: Path) -> None:
    project = _project(tmp_path, ["codex", "opencode"])
    _sync(project, "auto")
    assert _keys("codex", "opencode") <= _written(project)
    assert _keys("codex") != _keys("opencode"), "the two recorded clients must not share a file"


@pytest.mark.parametrize("client", ["opencode", "codex"])
def test_named_client_writes_only_its_own_file(tmp_path: Path, client: str) -> None:
    project = _project(tmp_path, ["claude-code", "codex", "opencode"])
    _sync(project, client)
    assert _written(project) == _keys(client)


def test_a_failing_client_does_not_stop_the_others_or_the_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import _hook_env

    project = _project(tmp_path, ["codex", "opencode"])
    real = _hook_env._write_hook_env_file

    def flaky(trw_dir: Path, profile: object, **kwargs: object) -> Path:
        if getattr(profile, "client_id", "") == "codex":
            raise OSError("disk full")
        return real(trw_dir, profile, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_hook_env, "_write_hook_env_file", flaky)
    _sync(project, "auto")
    assert _keys("opencode") <= _written(project)
    assert not (_keys("codex") & _written(project))
