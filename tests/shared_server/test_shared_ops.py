"""Operator verbs: env create/seed, swap, doctor row, and the disabled-proxy rollback lever."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from trw_memory.user_paths import resolve_user_memory_dir

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _cli, _doctor, _ops
from trw_mcp.shared_server._records import SharedPaths, SharedServerError, read_env_map

pytestmark = pytest.mark.integration


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    return SharedPaths.resolve(tmp_path / ".trw", SharedMcpConfig(envs_dir=str(tmp_path / "envs")))


def _stable_store(rows: int) -> Path:
    memory = resolve_user_memory_dir()  # conftest points TRW_USER_DIR at a tmp dir
    store = memory / "memory.db"
    with sqlite3.connect(store) as db:
        db.execute("CREATE TABLE memories (id TEXT)")
        db.executemany("INSERT INTO memories VALUES (?)", [(str(i),) for i in range(rows)])
    (memory / "daemon-grants.json").write_text('{"digest": {"namespaces": ["p"]}}')
    return store


def test_seeding_copies_a_snapshot_and_leaves_the_source_untouched(paths: SharedPaths) -> None:
    source = _stable_store(3)
    before = source.read_bytes()
    target = _ops.ensure_env(paths, "dev", seed_from="stable")
    with sqlite3.connect(target / "memory.db") as db:
        assert db.execute("SELECT count(*) FROM memories").fetchone()[0] == 3
    assert (target / "daemon-grants.json").read_text() == (source.parent / "daemon-grants.json").read_text()
    assert source.read_bytes() == before
    assert target == paths.envs_dir / "dev" / "memory"


def test_seeding_never_overwrites_an_existing_store(paths: SharedPaths) -> None:
    _stable_store(1)
    _ops.ensure_env(paths, "dev", seed_from="stable")
    with pytest.raises(SharedServerError, match="never overwrites"):
        _ops.ensure_env(paths, "dev", seed_from="stable")


def test_an_unseeded_env_gets_grants_but_no_data(paths: SharedPaths) -> None:
    _stable_store(2)
    target = _ops.ensure_env(paths, "test", seed_from=None)
    assert (target / "daemon-grants.json").is_file() and not (target / "memory.db").exists()


def test_stable_is_never_created_or_seeded(paths: SharedPaths) -> None:
    with pytest.raises(SharedServerError, match="stable"):
        _ops.ensure_env(paths, "stable", seed_from=None)


@pytest.mark.parametrize("layout", ["interpreter", "venv", "worktree"])
def test_python_resolves_from_an_interpreter_a_venv_or_a_worktree(tmp_path: Path, layout: str) -> None:
    python = {
        "interpreter": tmp_path / "py",
        "venv": tmp_path / "bin" / "python",
        "worktree": tmp_path / ".venv" / "bin" / "python",
    }[layout]
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("")
    assert _ops.resolve_python(tmp_path if layout != "interpreter" else python) == python


def test_python_resolution_refuses_a_path_without_one(tmp_path: Path) -> None:
    with pytest.raises(SharedServerError, match="no python"):
        _ops.resolve_python(tmp_path)


def test_swap_refuses_a_version_mismatch_and_changes_nothing(paths: SharedPaths, tmp_path: Path) -> None:
    with pytest.raises(SharedServerError, match="nothing changed"):
        _ops.swap(paths, "dev", Path(sys.executable), project_root=tmp_path, expect="0.0.0-not-this")
    assert read_env_map(paths) == {}


def test_swap_of_a_stopped_env_repoints_it_for_the_next_proxy_call(paths: SharedPaths, tmp_path: Path) -> None:
    report = _ops.swap(paths, "dev", Path(sys.executable), project_root=tmp_path, expect=None)
    assert "starts on the next proxy call" in report
    assert read_env_map(paths) == {"dev": sys.executable}
    assert (paths.envs_dir / "dev" / "memory").is_dir(), "a non-stable env gets its own memory dir"


def test_a_version_swap_needs_the_local_wheelhouse(paths: SharedPaths, tmp_path: Path) -> None:
    config = SharedMcpConfig(wheelhouse=str(tmp_path / "missing"))
    with pytest.raises(SharedServerError, match="no local wheelhouse"):
        _ops.build_version_venv(paths, "test", "8.0.0.dev3", config)


def test_doctor_row_moves_from_skip_to_warn_to_pass(paths: SharedPaths) -> None:
    assert _doctor.doctor_row(paths, SharedMcpConfig())[0] == "SKIP"
    on = SharedMcpConfig(enabled=True)
    assert _doctor.doctor_row(paths, on)[0] == "PASS"
    _ops.set_env_python(paths, "dev", Path(sys.executable))
    status, message = _doctor.doctor_row(paths, on)
    assert status == "WARN" and "trw-mcp env create dev" in message


def test_disabled_proxy_runs_stdio_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[list[str]] = []
    monkeypatch.setattr("trw_mcp.server._cli.main", lambda: served.append(list(sys.argv)))
    monkeypatch.setattr(sys, "argv", ["trw-mcp-proxy", "--env", "dev"])
    _cli.main_proxy()
    assert served == [["trw-mcp", "serve"]], "shared_mcp off: the proxy is exactly `trw-mcp serve`"


@pytest.mark.parametrize(
    ("variable", "value"), [("TRW_SURFACE_ROLE", "reviewer"), ("TRW_DISPATCH_CHILD", "1")], ids=["reviewer", "child"]
)  # TRW_DISPATCH_CHILD is trw_mcp.dispatch._child_marker.CHILD_MARKER_ENV
def test_a_bounded_lane_never_attaches_to_the_shared_server(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)
    attached: list[str] = []
    monkeypatch.setattr("trw_mcp.shared_server._proxy.run_proxy", attached.append)
    served: list[list[str]] = []
    monkeypatch.setattr("trw_mcp.server._cli.main", lambda: served.append(list(sys.argv)))
    # Patched last: resolving "trw_mcp.server._cli" imports trw_mcp.server, whose app build reads the
    # real config; the stub stands in only for the proxy's own shared_mcp read (order-independent).
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _Enabled())
    monkeypatch.setattr(sys, "argv", ["trw-mcp-proxy"])
    _cli.main_proxy()
    assert (served, attached) == ([["trw-mcp", "serve"]], [])


class _Enabled:
    shared_mcp = SharedMcpConfig(enabled=True)


def _stable_config() -> Any:
    from trw_memory.models.config import MemoryConfig

    memory = resolve_user_memory_dir()
    return MemoryConfig(storage_path=str(memory), memory_single_store_path=str(memory / "memory.db"))


def _dev_config(target: Path) -> Any:
    from trw_memory.models.config import MemoryConfig

    return MemoryConfig(storage_path=str(target), memory_single_store_path=str(target / "memory.db"))


def _stable_with_a_quarantined_active_row() -> str:
    """A real stable store holding one active row whose identity the stable ledger has quarantined."""
    from trw_memory.integrations._backend import create_backend_from_config
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.security.quarantine_ledger import LedgerIdentity, ledger_for_config

    config = _stable_config()
    with create_backend_from_config(config, "default") as backend:
        backend.store(MemoryEntry(id="L-held", content="poisoned guidance kept active", namespace="default"))
        backend.store(MemoryEntry(id="L-fine", content="ordinary guidance", namespace="default"))
    ledger_for_config(config).append(LedgerIdentity(namespace="default", entry_id="L-held"), "quarantined", actor="t")
    (resolve_user_memory_dir() / "daemon-grants.json").write_text('{"digest": {"namespaces": ["default"]}}')
    return "L-held"


def test_a_seeded_env_still_blocks_what_the_source_quarantined(paths: SharedPaths) -> None:
    """ENV-SEED-LEDGER: `env create dev --seed-from stable` carries the ledger, so dev never serves a held identity."""
    from trw_memory.integrations._backend import create_backend_from_config

    held = _stable_with_a_quarantined_active_row()

    target = _ops.ensure_env(paths, "dev", seed_from="stable")

    with sqlite3.connect(target / "memory.db") as raw:  # the row itself was copied: only the ledger hides it
        assert raw.execute("SELECT count(*) FROM memories WHERE id = ?", (held,)).fetchone()[0] == 1
    with create_backend_from_config(_dev_config(target), "default") as dev:
        assert dev.get(held, namespace="default") is None, "the seeded env served a quarantined identity"
        assert dev.get("L-fine", namespace="default") is not None


def test_a_source_ledger_that_cannot_be_read_refuses_the_whole_seed(paths: SharedPaths) -> None:
    """Never a store without its ledger: an unreadable source ledger leaves dev with neither."""
    from trw_memory.security.quarantine_ledger import ledger_for_config

    _stable_with_a_quarantined_active_row()
    ledger = ledger_for_config(_stable_config()).path
    ledger.chmod(0)
    try:
        with pytest.raises(SharedServerError, match=f"quarantine ledger {ledger}.*could not be copied"):
            _ops.ensure_env(paths, "dev", seed_from="stable")
    finally:
        ledger.chmod(0o600)
    target = paths.envs_dir / "dev" / "memory"
    assert not (target / "memory.db").exists() and not (target / "memory.db.seeding").exists()
    assert not ledger_for_config(_dev_config(target)).path.exists()


def test_a_source_without_a_ledger_seeds_the_store_alone(paths: SharedPaths) -> None:
    from trw_memory.security.quarantine_ledger import ledger_for_config

    _stable_store(2)
    target = _ops.ensure_env(paths, "dev", seed_from="stable")
    assert (target / "memory.db").is_file()
    assert not ledger_for_config(_dev_config(target)).path.exists()


def test_a_failed_store_publish_leaves_the_seeded_ledger_intact_and_names_both_paths(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review r2: never delete or replace a ledger -- a daemon may already have appended to the one we published."""
    from trw_memory.security.quarantine_ledger import LedgerIdentity, QuarantineLedger, ledger_for_config
    from trw_memory.storage import _snapshot

    _stable_with_a_quarantined_active_row()
    target = paths.envs_dir / "dev" / "memory"
    dev_ledger = ledger_for_config(_dev_config(target)).path
    real_publish = _snapshot.publish_no_clobber

    def failing_store_publish(tmp: Path, dest: Path) -> tuple[int, int]:
        if dest.name != "memory.db":
            return real_publish(tmp, dest)
        # A dev daemon appends to the ledger this seed just published, then the store's publish fails.
        QuarantineLedger(dev_ledger).append(LedgerIdentity("default", "L-own"), "rejected", actor="dev-daemon")
        raise OSError("disk full")

    monkeypatch.setattr(_snapshot, "publish_no_clobber", failing_store_publish)
    with pytest.raises(SharedServerError) as refused:
        _ops.ensure_env(paths, "dev", seed_from="stable")

    message = str(refused.value)
    assert str(target / "memory.db") in message and str(dev_ledger) in message
    assert "nothing was deleted" in message and f"rm -r {paths.envs_dir / 'dev'}" in message
    assert not (target / "memory.db").exists()
    assert not [p.name for p in target.iterdir() if ".seeding" in p.name]
    rows = QuarantineLedger(dev_ledger).rows()
    assert [(r.identity.entry_id, r.decision) for r in rows] == [("L-held", "quarantined"), ("L-own", "rejected")]
    monkeypatch.setattr(_snapshot, "publish_no_clobber", real_publish)
    with pytest.raises(SharedServerError, match="already exists"):  # a retry over the seeded ledger stays refused
        _ops.ensure_env(paths, "dev", seed_from="stable")
    assert [r.actor for r in QuarantineLedger(dev_ledger).rows()] == ["t", "dev-daemon"]


def _security_dir(memory_dir: Path) -> Path:
    return memory_dir.parent / "security"


def test_a_seed_carries_the_review_queue_but_never_the_signing_key_or_history(paths: SharedPaths) -> None:
    """D9: dev can review what stable holds; its signing key and audit/anomaly/rate-limit history start fresh."""
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.security._runtime_quarantine import open_quarantine_backend

    _stable_with_a_quarantined_active_row()
    with open_quarantine_backend(_stable_config()) as queue:
        queue.store(MemoryEntry(id="Q-held", content="held for review", namespace="default"))
    stable_security = _security_dir(resolve_user_memory_dir())
    fresh = ("ed25519_signing_key.bin", "audit.jsonl", "rate_limits.yaml", "anomaly_stats.yaml")
    for name in fresh:
        (stable_security / name).write_text("stable-only")

    target = _ops.ensure_env(paths, "dev", seed_from="stable")

    dev_security = _security_dir(target)
    with open_quarantine_backend(_dev_config(target)) as queue:
        held = queue.get("Q-held", namespace="default")
    assert held is not None and held.content == "held for review"
    assert (dev_security / "quarantine.db").stat().st_mode & 0o777 == 0o600
    assert not [name for name in fresh if (dev_security / name).exists()], "per-env security state was copied"


@pytest.mark.skipif(os.geteuid() == 0, reason="root traverses a 0600 directory")
def test_a_source_ledger_behind_an_untraversable_directory_refuses_the_seed(paths: SharedPaths) -> None:
    """env-seed-ledger r3 P0: EACCES on stable's security dir is not "no ledger"; dev gets no store."""
    from trw_memory.security.quarantine_ledger import ledger_for_config

    _stable_with_a_quarantined_active_row()
    ledger = ledger_for_config(_stable_config()).path
    target = paths.envs_dir / "dev" / "memory"
    dev_ledger = ledger_for_config(_dev_config(target)).path
    ledger.parent.chmod(0o600)  # memory.db stays readable; only the ledger's directory cannot be searched
    try:
        with pytest.raises(SharedServerError) as refused:
            _ops.ensure_env(paths, "dev", seed_from="stable")
    finally:
        ledger.parent.chmod(0o700)
    message = str(refused.value)
    assert str(ledger) in message and str(dev_ledger) in message and "PermissionError" in message
    assert "no store was seeded" in message and "permissions" in message
    assert not (target / "memory.db").exists() and not dev_ledger.exists()


def test_a_review_queue_that_cannot_be_copied_keeps_the_seeded_ledger_and_names_the_retry(
    paths: SharedPaths,
) -> None:
    """A failure after the ledger landed deletes nothing and says how to retry; dev never gets the store."""
    from trw_memory.models.memory import MemoryEntry
    from trw_memory.security._runtime_quarantine import open_quarantine_backend
    from trw_memory.security.quarantine_ledger import QuarantineLedger, ledger_for_config

    _stable_with_a_quarantined_active_row()
    with open_quarantine_backend(_stable_config()) as queue:
        queue.store(MemoryEntry(id="Q-held", content="held for review", namespace="default"))
    queue_db = _security_dir(resolve_user_memory_dir()) / "quarantine.db"
    target = paths.envs_dir / "dev" / "memory"
    dev_ledger = ledger_for_config(_dev_config(target)).path
    queue_db.chmod(0)
    try:
        with pytest.raises(SharedServerError) as refused:
            _ops.ensure_env(paths, "dev", seed_from="stable")
    finally:
        queue_db.chmod(0o600)
    message = str(refused.value)
    assert f"the review queue {queue_db} could not be copied" in message
    assert f"Already seeded: {dev_ledger}" in message and "nothing was deleted" in message
    assert f"rm -r {paths.envs_dir / 'dev'}" in message
    assert not (target / "memory.db").exists()
    assert [r.identity.entry_id for r in QuarantineLedger(dev_ledger).rows()] == ["L-held"]


# --- swap --src: a linked worktree's source, served by this interpreter (HOTRELOAD-SWAP-SRC) ---


@pytest.fixture(autouse=True)
def _no_ambient_git_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):  # e.g. when run from a git hook
        monkeypatch.delenv(name, raising=False)


def _git(*argv: str | Path) -> None:
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *map(str, argv)], check=True, capture_output=True
    )


def _repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    repo, worktree = tmp_path / "repo", tmp_path / "wt"
    repo.mkdir()
    _git("init", "-q", repo)
    _git("-C", repo, "commit", "-q", "--allow-empty", "-m", "root")
    _git("-C", repo, "worktree", "add", "-q", "--detach", worktree)
    for pkg in ("trw-mcp", "trw-memory"):
        (worktree / pkg / "src").mkdir(parents=True)
    return repo, worktree


def test_src_resolves_a_linked_worktree_of_the_same_repo(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    wt = worktree.resolve()
    expected = os.pathsep.join([str(wt / "trw-mcp" / "src"), str(wt / "trw-memory" / "src")])
    assert _ops.worktree_pythonpath(worktree, repo) == expected


def test_src_refuses_a_directory_that_is_not_a_worktree_of_this_repo(tmp_path: Path) -> None:
    repo, _ = _repo_with_worktree(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(SharedServerError, match="not a git worktree of this repo"):
        _ops.worktree_pythonpath(other, repo)
    _git("init", "-q", other)  # a git repo, but a different one
    with pytest.raises(SharedServerError, match="not a git worktree of this repo"):
        _ops.worktree_pythonpath(other, repo)


def test_src_refuses_a_worktree_without_the_package_sources(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (worktree / "trw-memory" / "src").rmdir()
    with pytest.raises(SharedServerError, match="trw-memory"):
        _ops.worktree_pythonpath(worktree, repo)


def _this_source() -> str:
    import trw_mcp

    return str(Path(trw_mcp.__file__).resolve().parents[1])  # the src dir this test imports trw_mcp from


def test_a_src_swap_records_the_pythonpath_and_a_python_swap_clears_it(paths: SharedPaths, tmp_path: Path) -> None:
    from trw_mcp.shared_server._records import env_pythonpath

    report = _ops.swap(
        paths, "dev", Path(sys.executable), project_root=tmp_path, expect=None, pythonpath=_this_source()
    )
    assert "starts on the next proxy call" in report and f"PYTHONPATH={_this_source()}" in report
    assert read_env_map(paths) == {"dev": sys.executable}
    assert env_pythonpath(paths, "dev") == _this_source()
    assert paths.envs() == ["dev"], "the pythonpath map is never read as an env record"
    _ops.swap(paths, "dev", Path(sys.executable), project_root=tmp_path, expect=None)
    assert env_pythonpath(paths, "dev") is None


def test_a_src_swap_refuses_when_the_interpreter_imports_trw_mcp_from_elsewhere(
    paths: SharedPaths, tmp_path: Path
) -> None:
    empty = tmp_path / "empty-src"
    empty.mkdir()
    with pytest.raises(SharedServerError, match="imports trw_mcp from"):
        _ops.swap(paths, "dev", Path(sys.executable), project_root=tmp_path, expect=None, pythonpath=str(empty))
    assert read_env_map(paths) == {}


def test_the_server_spawns_with_the_recorded_pythonpath(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.shared_server import _proxy
    from trw_mcp.shared_server._records import set_env_python

    seen: dict[str, Any] = {}

    class _Proc:
        def wait(self) -> int:
            return 0

    def fake_popen(argv: list[str], **kwargs: Any) -> _Proc:
        seen.update(argv=argv, env=kwargs["env"])
        return _Proc()

    monkeypatch.setattr(_proxy.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("PYTHONPATH", "/shell/path")
    paths.root.mkdir(parents=True, exist_ok=True)
    set_env_python(paths, "stable", Path("/py"), pythonpath="/wt/trw-mcp/src:/wt/trw-memory/src")
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path), successor=True)
    assert seen["argv"] == ["/py", "-m", "trw_mcp.server", "serve", "--shared", "--env", "stable", "--successor"]
    assert seen["env"]["PYTHONPATH"] == "/wt/trw-mcp/src:/wt/trw-memory/src"
    set_env_python(paths, "stable", Path("/py"))
    _proxy.spawn_server(paths, "stable", project_root=str(tmp_path))
    assert "PYTHONPATH" not in seen["env"], "a --python swap never inherits the shell's PYTHONPATH"


def test_swap_cli_src_uses_this_interpreter_and_the_worktree_source(
    paths: SharedPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    from trw_mcp.state import _paths as state_paths

    repo, worktree = _repo_with_worktree(tmp_path)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(_cli, "_paths", lambda: (paths, None))
    monkeypatch.setattr(state_paths, "resolve_project_root", lambda: repo)
    monkeypatch.setattr(_ops, "swap", lambda *a, **k: calls.append({"args": a, **k}) or "ok")
    parser = argparse.ArgumentParser()
    _cli.add_shared_subcommands(parser.add_subparsers(dest="cmd"))
    _cli.run_swap(parser.parse_args(["swap", "--env", "dev", "--src", str(worktree)]))
    assert calls[0]["args"] == (paths, "dev", Path(sys.executable))
    assert calls[0]["pythonpath"] == _ops.worktree_pythonpath(worktree, repo)
    assert calls[0]["project_root"] == repo
