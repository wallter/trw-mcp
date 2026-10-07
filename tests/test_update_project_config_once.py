"""P2a: one install resolves ``TRWConfig`` a bounded number of times, and never from a stale config.yaml.

``update_project(ide="all")`` rebuilt the config cascade twice per client inside every hook-env pass (30
builds per update, 15 per init). The hook-env pass now resolves it once, at the moment it runs. The count
tests pin that ceiling with a counting wrapper, so a change that brings the per-client rebuilds back fails;
the refresh tests prove each pass still reads the config.yaml the earlier phases left, not one resolved
before them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

#: Measured after the change (2026-10-06): report cap 1 + two hook-env passes 2 + three install-binding
#: ``get_config()`` builds 3 = 6 for update(all); report cap 1 + one pass 1 + skills 1 = 3 for init(all).
#: Before it: 30 and 15.
_UPDATE_ALL_MAX_BUILDS = 6
_INIT_ALL_MAX_BUILDS = 3


@pytest.fixture
def config_builds(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every ``TRWConfig`` construction while the test runs (a wrapper on the settings constructor)."""
    import pydantic_settings.main as settings_main

    from trw_mcp.models.config import TRWConfig

    built: list[str] = []
    real_init: Callable[..., None] = settings_main.BaseSettings.__init__

    def counting_init(self: Any, *args: Any, **kwargs: Any) -> None:
        if isinstance(self, TRWConfig):
            built.append(type(self).__name__)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(settings_main.BaseSettings, "__init__", counting_init)
    yield built


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def test_init_all_builds_the_config_a_bounded_number_of_times(tmp_path: Path, config_builds: list[str]) -> None:
    from trw_mcp.bootstrap import init_project

    root = _repo(tmp_path)
    assert not init_project(root, ide="all")["errors"]
    assert config_builds, "control: the counting wrapper saw no build at all"
    assert len(config_builds) <= _INIT_ALL_MAX_BUILDS, config_builds


def test_update_all_builds_the_config_a_bounded_number_of_times(tmp_path: Path, config_builds: list[str]) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    root = _repo(tmp_path)
    assert not init_project(root, ide="all")["errors"]
    config_builds.clear()
    assert not update_project(root, ide="all")["errors"]
    assert config_builds, "control: the counting wrapper saw no build at all"
    assert len(config_builds) <= _UPDATE_ALL_MAX_BUILDS, config_builds


def test_one_hook_env_pass_resolves_the_config_once_for_every_client(tmp_path: Path, config_builds: list[str]) -> None:
    """The pass that used to build it twice per client: eight clients, one build."""
    from trw_mcp.bootstrap._hook_env import write_hook_env_for_clients
    from trw_mcp.models.config._profiles import builtin_client_ids

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    clients = list(builtin_client_ids())
    assert len(clients) > 1
    written = write_hook_env_for_clients(trw_dir, clients)
    assert len(written) == len(clients)
    assert len(config_builds) == 1, config_builds


def _disable_hooks(root: Path) -> None:
    config = root / ".trw" / "config.yaml"
    text = config.read_text(encoding="utf-8")
    assert "hooks_enabled" not in text, "control: the setting must be new, or the append is a duplicate key"
    config.write_text(text.rstrip("\n") + "\nhooks_enabled: false\n", encoding="utf-8")


def _hook_flags(root: Path) -> str:
    return (root / ".trw" / "runtime" / "hook-flags").read_text(encoding="utf-8")


def test_the_first_hook_env_pass_reads_the_config_a_mid_install_phase_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A phase before the instruction sync (where ``target_platforms`` is recorded) changes config.yaml:
    the sync's hook-env pass, and the published flags, must see the new value."""
    from trw_mcp.bootstrap import _update_project, init_project, update_project

    root = _repo(tmp_path)
    assert not init_project(root, ide="claude-code")["errors"]
    assert "hooks_enabled=true" in _hook_flags(root)
    real_record = _update_project._update_config_target_platforms

    def record_then_migrate(target_dir: Path, *args: Any, **kwargs: Any) -> None:
        real_record(target_dir, *args, **kwargs)
        _disable_hooks(target_dir)

    monkeypatch.setattr(_update_project, "_update_config_target_platforms", record_then_migrate)
    result = update_project(root, ide="claude-code")

    assert not result["errors"]
    assert "hooks_enabled=false" in _hook_flags(root)
    assert any("hooks_enabled resolves to false" in warning for warning in result["warnings"])


def test_the_last_hook_env_pass_does_not_reuse_the_first_passes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.yaml changes BETWEEN the two hook-env passes of one update: the final flags are the new value."""
    from trw_mcp.bootstrap import _update_project, init_project, update_project

    root = _repo(tmp_path)
    assert not init_project(root, ide="claude-code")["errors"]
    real_integrations = _update_project.run_update_integrations

    def integrations_then_migrate(target_dir: Path, *args: Any, **kwargs: Any) -> Any:
        outcome = real_integrations(target_dir, *args, **kwargs)
        _disable_hooks(target_dir)
        return outcome

    monkeypatch.setattr(_update_project, "run_update_integrations", integrations_then_migrate)
    result = update_project(root, ide="claude-code")

    assert not result["errors"]
    assert "hooks_enabled=false" in _hook_flags(root)


def test_a_config_that_does_not_load_still_fails_open_per_client(tmp_path: Path) -> None:
    """The once-per-pass build must not turn a broken config.yaml into a skipped pass: every client's file is
    still written, exactly as when each client resolved the config itself."""
    from trw_mcp.bootstrap._hook_env import write_hook_env_for_clients

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "config.yaml").write_text("hooks_enabled: [unclosed\n", encoding="utf-8")
    write_hook_env_for_clients(trw_dir, ["claude-code", "codex"])
    assert sorted(p.name for p in (trw_dir / "runtime" / "hook-env.d").iterdir()) == ["claude.sh", "codex.sh"]
