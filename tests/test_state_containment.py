"""E2E-INC-034/035: TRW state writes never leave the project through a planted symlink or an outside run_path.

Each case plants what a hostile checkout can ship (``.trw`` is partly tracked) and asserts the outside "victim"
directory stays empty and the write raises the named ``ContainmentError``. Controls prove legitimate in-root
writes, the user-level ``~/.trw`` and non-TRW paths still work.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".trw" / "learnings").mkdir(parents=True)
    (root / ".trw" / "runs").mkdir(parents=True)
    return root


@pytest.fixture
def victim(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    return outside


def _writer() -> object:
    from trw_mcp.state.persistence import FileStateWriter

    return FileStateWriter()


def test_a_learning_under_a_symlinked_entries_dir_is_refused(project: Path, victim: Path) -> None:
    from trw_mcp.state._containment import ContainmentError

    os.symlink(victim, project / ".trw" / "learnings" / "entries")
    with pytest.raises(ContainmentError, match="symlink"):
        _writer().write_yaml(project / ".trw" / "learnings" / "entries" / "probe.yaml", {"summary": "x"})  # type: ignore[attr-defined]
    assert list(victim.iterdir()) == []


def test_an_append_through_a_symlinked_run_dir_or_meta_file_is_refused(project: Path, victim: Path) -> None:
    from trw_mcp.state._containment import ContainmentError

    os.symlink(victim, project / ".trw" / "runs" / "task")
    with pytest.raises(ContainmentError):
        _writer().append_jsonl(project / ".trw" / "runs" / "task" / "r1" / "meta" / "events.jsonl", {"e": 1})  # type: ignore[attr-defined]
    real_meta = project / ".trw" / "runs" / "t2" / "r1" / "meta"
    real_meta.mkdir(parents=True)
    target = victim / "events.jsonl"
    target.write_text("", encoding="utf-8")
    os.symlink(target, real_meta / "events.jsonl")
    with pytest.raises(ContainmentError):
        _writer().append_jsonl(real_meta / "events.jsonl", {"e": 1})  # type: ignore[attr-defined]
    assert target.read_text(encoding="utf-8") == "" and [p.name for p in victim.iterdir()] == ["events.jsonl"]


def test_creating_a_run_dir_under_a_symlinked_task_dir_is_refused(project: Path, victim: Path) -> None:
    from trw_mcp.state._containment import ContainmentError

    os.symlink(victim, project / ".trw" / "runs" / "linkdir")
    with pytest.raises(ContainmentError):
        _writer().ensure_dir(project / ".trw" / "runs" / "linkdir" / "20260930T000000Z-abcd" / "meta")  # type: ignore[attr-defined]
    with pytest.raises(ContainmentError):
        _writer().write_text(project / ".trw" / "runs" / "linkdir" / "x" / "run.yaml", "a: 1\n")  # type: ignore[attr-defined]
    assert list(victim.iterdir()) == []


def test_a_symlinked_project_trw_dir_is_refused(tmp_path: Path, victim: Path) -> None:
    from trw_mcp.state._containment import ContainmentError

    repo = tmp_path / "repo2"
    repo.mkdir()
    os.symlink(victim, repo / ".trw")
    with pytest.raises(ContainmentError):
        _writer().write_yaml(repo / ".trw" / "config.yaml", {"a": 1})  # type: ignore[attr-defined]
    assert list(victim.iterdir()) == []


def test_parent_dir_components_below_trw_are_refused(project: Path) -> None:
    from trw_mcp.state._containment import ContainmentError, assert_trw_write_contained

    with pytest.raises(ContainmentError, match=r"\.\."):
        assert_trw_write_contained(project / ".trw" / "runs" / ".." / ".." / ".." / "x.yaml")


def test_legitimate_in_root_writes_still_work(project: Path) -> None:
    writer = _writer()
    writer.write_yaml(project / ".trw" / "learnings" / "entries" / "ok.yaml", {"summary": "x"})  # type: ignore[attr-defined]
    writer.append_jsonl(project / ".trw" / "runs" / "t" / "r" / "meta" / "events.jsonl", {"e": 1})  # type: ignore[attr-defined]
    writer.ensure_dir(project / ".trw" / "runs" / "t" / "r2" / "meta")  # type: ignore[attr-defined]
    assert (project / ".trw" / "learnings" / "entries" / "ok.yaml").is_file()
    assert (project / ".trw" / "runs" / "t" / "r" / "meta" / "events.jsonl").read_text().strip() == '{"e": 1}'


def _scope(monkeypatch: pytest.MonkeyPatch, home: Path, project_trw: Path) -> None:
    """HOME, no TRW_USER_DIR/XDG override (so the user scope is ~/.trw), and the project's trw_dir."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TRW_USER_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: project_trw)


def test_the_real_user_scope_dir_may_be_a_symlink(
    tmp_path: Path, project: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured user-scope store (~/.trw, not this project's trw_dir) is the user's own choice."""
    home = tmp_path / "home"
    home.mkdir()
    _scope(monkeypatch, home, project / ".trw")
    os.symlink(victim, home / ".trw")
    _writer().write_yaml(home / ".trw" / "memory" / "x.yaml", {"a": 1})  # type: ignore[attr-defined]
    assert (victim / "memory" / "x.yaml").is_file()


def test_a_project_rooted_at_home_contains_its_trw_like_any_other(
    tmp_path: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex r1 P0: project root == $HOME (a dotfiles repo, a tmp-HOME sandbox). ~/.trw IS the project's trw_dir,
    so a planted ~/.trw symlink is project state and is refused; codex's repro via the nudge emitter."""
    from trw_mcp.state._ceremony_progress_state import _emit_nudge_shown_event
    from trw_mcp.state._containment import ContainmentError

    home = tmp_path / "repo-is-home"
    home.mkdir()
    _scope(monkeypatch, home, home / ".trw")
    os.symlink(victim, home / ".trw")
    with pytest.raises(ContainmentError):
        _writer().write_yaml(home / ".trw" / "learnings" / "entries" / "x.yaml", {"a": 1})  # type: ignore[attr-defined]
    _emit_nudge_shown_event(home / ".trw", learning_id="L", phase="p", turn=1, surface_type="s")
    assert list(victim.iterdir()) == []


def test_best_effort_telemetry_falls_back_instead_of_raising(project: Path, victim: Path) -> None:
    """Codex r1 P1: a refused run dir must not escape telemetry's fail-open boundary."""
    from trw_mcp.telemetry.unified_events import resolve_unified_events_path

    os.symlink(victim, project / ".trw" / "runs" / "planted")
    fallback = project / ".trw" / "context"
    path = resolve_unified_events_path(run_dir=project / ".trw" / "runs" / "planted", fallback_dir=fallback)
    assert path is not None and path.parent == fallback
    assert list(victim.iterdir()) == []


def test_the_learn_journal_and_the_trust_lock_refuse_planted_links(project: Path, victim: Path) -> None:
    """Codex r1 P2 x2: both did an unguarded mkdir + create through a shipped .trw symlink."""
    from trw_mcp.state._containment import ContainmentError
    from trw_mcp.state._learn_journal_io import write_record_atomic
    from trw_mcp.state._trust_outcome import _with_registry_lock

    os.symlink(victim, project / ".trw" / "learnings" / "pending")
    with pytest.raises(ContainmentError):
        write_record_atomic(project / ".trw" / "learnings" / "pending" / "r.json", {"a": 1})
    os.symlink(victim, project / ".trw" / "context")
    with pytest.raises(ContainmentError):
        _with_registry_lock(project / ".trw", lambda: None)
    assert list(victim.iterdir()) == []


def test_a_path_under_no_trw_dir_is_not_this_rules_business(tmp_path: Path) -> None:
    from trw_mcp.state._containment import assert_trw_write_contained

    link = tmp_path / "linked"
    os.symlink(tmp_path, link)
    assert assert_trw_write_contained(link / "anything.txt") is None  # outside any .trw: not its business
    # Contrast: the identical symlink shape under a .trw directory IS refused.
    from trw_mcp.state._containment import ContainmentError

    project = tmp_path / "proj"
    project.mkdir()
    os.symlink(tmp_path, project / ".trw")
    with pytest.raises(ContainmentError):
        assert_trw_write_contained(project / ".trw" / "anything.txt")


def test_the_tool_call_emitter_never_attributes_to_an_outside_run_path(
    project: Path, victim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INC-035: the timing emitter wrote <outside>/meta/tool_call_events.jsonl before the tool validated run_path."""
    from trw_mcp.telemetry import tool_call_timing

    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)

    def trw_build_check(options: dict[str, object] | None = None) -> None: ...

    outside = victim / "run"
    assert tool_call_timing._resolve_run_dir(trw_build_check, options={"run_path": str(outside)}) != outside.resolve()
    assert not outside.exists()


def test_the_boolean_form_reports_instead_of_raising(project: Path, victim: Path) -> None:
    from trw_mcp.state._containment import trw_write_contained

    os.symlink(victim, project / ".trw" / "runs" / "planted")
    assert trw_write_contained(project / ".trw" / "runs" / "planted" / "x.jsonl") is False
    assert trw_write_contained(project / ".trw" / "runs" / "real" / "x.jsonl") is True


def test_a_best_effort_gc_append_skips_a_planted_run_dir(project: Path, victim: Path) -> None:
    """A direct open("a") appender outside FileStateWriter takes the same rule and carries on without writing."""
    from trw_mcp.state._run_gc_io import _append_event_best_effort

    os.symlink(victim, project / ".trw" / "runs" / "planted")
    _append_event_best_effort(project / ".trw" / "runs" / "planted" / "meta" / "events.jsonl", "run_abandoned", {})
    assert list(victim.iterdir()) == []


def test_the_refusal_names_the_symlinked_component_and_the_remedy(project: Path, victim: Path) -> None:
    from trw_mcp.state._containment import ContainmentError, assert_trw_write_contained

    os.symlink(victim, project / ".trw" / "learnings" / "entries")
    with pytest.raises(ContainmentError) as exc:
        assert_trw_write_contained(project / ".trw" / "learnings" / "entries" / "x.yaml")
    message = str(exc.value)
    assert str(project / ".trw" / "learnings" / "entries") in message
    assert "real directory" in message and "remove the link" in message


def test_a_refused_best_effort_path_warns_once_per_process(project: Path, victim: Path) -> None:
    import structlog.testing

    from trw_mcp.state import _containment

    os.symlink(victim, project / ".trw" / "runs" / "planted")
    target = project / ".trw" / "runs" / "planted" / "e.jsonl"
    _containment._WARNED.discard(str(target))
    with structlog.testing.capture_logs() as logs:
        assert _containment.trw_write_contained(target) is False
        assert _containment.trw_write_contained(target) is False
    refused = [e for e in logs if e["event"] == "trw_state_write_refused"]
    assert len(refused) == 1 and refused[0]["log_level"] == "warning"


def test_a_nested_trw_below_a_planted_link_does_not_reset_the_walk(project: Path, victim: Path) -> None:
    """Self-review r2: anchoring on the innermost .trw would skip a link above it."""
    from trw_mcp.state._containment import ContainmentError

    os.symlink(victim, project / ".trw" / "runs" / "planted")
    with pytest.raises(ContainmentError):
        _writer().write_yaml(project / ".trw" / "runs" / "planted" / ".trw" / "x.yaml", {"a": 1})  # type: ignore[attr-defined]
    assert list(victim.iterdir()) == []


def test_a_worktree_nested_under_the_main_trw_still_writes(project: Path) -> None:
    worktree_state = project / ".trw" / "worktrees" / "w1" / ".trw" / "runs" / "t" / "r"
    _writer().ensure_dir(worktree_state)  # type: ignore[attr-defined]
    assert worktree_state.is_dir()


def test_a_symlink_loop_at_trw_is_a_refusal_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex r2 known issue: resolving a looping .trw raised out of best-effort appenders. It is a refusal."""
    from trw_mcp.state._containment import ContainmentError, trw_write_contained

    repo = tmp_path / "loop-repo"
    repo.mkdir()
    os.symlink(repo / ".trw", repo / ".trw")  # .trw -> itself
    _scope(monkeypatch, repo, repo / ".trw")
    real_resolve = Path.resolve

    def resolve_like_py311(self: Path, strict: bool = False) -> Path:  # 3.11/3.12 raise on a loop; 3.13+ do not
        if self == repo / ".trw":
            raise RuntimeError(f"Symlink loop from {self!r}")
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_like_py311)
    user_store = tmp_path / "user-store"
    user_store.mkdir()
    monkeypatch.setenv("TRW_USER_DIR", str(user_store))  # a resolvable user scope, so the .trw resolve is reached
    with pytest.raises(ContainmentError):
        _writer().write_yaml(repo / ".trw" / "x.yaml", {"a": 1})  # type: ignore[attr-defined]
    assert trw_write_contained(repo / ".trw" / "context" / "e.jsonl") is False


def test_a_containment_refusal_is_caught_by_an_unsafe_write_handler(tmp_path: Path) -> None:
    """Handlers written for a refused safe_fs write (deliver outcome record, handoff seal) catch it too (HB-1)."""
    from trw_memory.exceptions import UnsafeWriteError

    from trw_mcp.state._containment import assert_trw_write_contained

    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "runs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeWriteError) as caught:
        assert_trw_write_contained(tmp_path / ".trw" / "runs" / "t" / "meta" / "x.json")
    assert caught.value.reason == "trw_containment"
    assert caught.value.path.endswith("x.json")


def test_a_refused_write_leaves_the_user_store_permissions_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The user-scope exemption probe only compares paths: it never hardens (chmods) the user store (W3 KI)."""
    from trw_mcp.state._containment import ContainmentError, assert_trw_write_contained

    user_store = tmp_path / "user-store"
    (user_store / "memory").mkdir(parents=True)
    for d in (user_store, user_store / "memory"):
        d.chmod(0o775)  # group-writable, self-owned: the resolver's verify step would harden it to 0700
    monkeypatch.setenv("TRW_USER_DIR", str(user_store))
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".trw").symlink_to(outside, target_is_directory=True)  # a planted .trw, not the user store
    with pytest.raises(ContainmentError):
        assert_trw_write_contained(repo / ".trw" / "x.yaml")
    assert (user_store.stat().st_mode & 0o777, (user_store / "memory").stat().st_mode & 0o777) == (0o775, 0o775)
