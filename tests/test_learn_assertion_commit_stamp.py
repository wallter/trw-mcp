"""PRD-CORE-362 FR01-FR03: ``trw_learn`` stamps the checkout commit on the assertions it writes.

The real ``execute_learn`` / ``execute_learn_update`` paths run against a real git repository in ``tmp_path``;
only the store write is a capturing double. ``subprocess.run`` is wrapped where a git failure is the subject.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig

_IDENT = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}
_FIXED = {"type": "grep_present", "pattern": "def my_func", "target": "src/**/*.py"}
_SECOND = {"type": "glob_exists", "pattern": "", "target": "src/main.py"}


def _git(root: Path, *args: str) -> str:
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, **_IDENT}
    done = subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, str]:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "a.py")
    _git(root, "commit", "-qm", "one")
    (root / ".trw").mkdir()
    return root, _git(root, "rev-parse", "HEAD")


class _Captured:
    def __init__(self) -> None:
        self.assertions: list[dict[str, str]] | None = None
        self.calls = 0

    def store(self, _trw_dir: Path, *, learning_id: str = "", **kwargs: Any) -> dict[str, object]:
        self.calls += 1
        self.assertions = kwargs.get("assertions")
        return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}


def _create(
    root: Path,
    captured: _Captured,
    assertions: list[dict[str, str]] | None,
    *,
    storage: Path | None = None,
    **extra: Any,
) -> dict[str, Any]:
    from trw_mcp.tools._learn_impl import execute_learn

    return dict(
        execute_learn(
            summary="a stamped learning",
            detail="Checking the checkout commit stamp on created assertions.",
            trw_dir=storage or root / ".trw",
            project_root=root,
            config=TRWConfig(),
            assertions=assertions,
            _adapter_store=captured.store,
            _generate_learning_id=lambda: "L-stamp-001",
            _save_learning_entry=lambda *a, **kw: root / "entry.yaml",
            _update_analytics=lambda *a, **kw: None,
            _list_active_learnings=lambda *a, **kw: [],
            _check_and_handle_dedup=lambda *a, **kw: None,
            **extra,
        )
    )


class _GitSpy:
    """Counts the git processes started by the claim tree module while delegating to the real ``run``."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        real = subprocess.run

        def run(argv: Any, *a: Any, **kw: Any) -> Any:
            if isinstance(argv, list) and "--no-lazy-fetch" in argv:
                self.calls.append(argv)
            return real(argv, *a, **kw)

        monkeypatch.setattr(subprocess, "run", run)


# FR01 -----------------------------------------------------------------------------------------------------


def test_a_created_learning_stamps_head_on_each_assertion(checkout: tuple[Path, str]) -> None:
    root, head = checkout
    captured = _Captured()

    result = _create(root, captured, [dict(_FIXED), dict(_SECOND)])

    assert result["status"] == "recorded"
    assert captured.assertions is not None
    assert [a["commit_hash"] for a in captured.assertions] == [head, head]
    assert [(a["type"], a["target"]) for a in captured.assertions] == [
        (_FIXED["type"], _FIXED["target"]),
        (_SECOND["type"], _SECOND["target"]),
    ]


def test_a_caller_supplied_commit_hash_is_kept(checkout: tuple[Path, str]) -> None:
    root, head = checkout
    given = "c" * 40
    captured = _Captured()

    _create(root, captured, [{**_FIXED, "commit_hash": given}, dict(_SECOND)])

    assert captured.assertions is not None
    assert [a["commit_hash"] for a in captured.assertions] == [given, head]


def test_a_learning_without_assertions_runs_no_git(checkout: tuple[Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = checkout
    spy = _GitSpy(monkeypatch)
    captured = _Captured()

    assert _create(root, captured, None)["status"] == "recorded"
    assert _create(root, captured, [])["status"] == "recorded"
    assert _create(root, captured, [{**_FIXED, "commit_hash": "d" * 40}])["status"] == "recorded"

    assert spy.calls == [], "no assertions, or none lacking a stamp, starts no git process"


def test_a_journal_replay_keeps_the_recorded_stamp(checkout: tuple[Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    root, head = checkout
    first = _Captured()
    _create(root, first, [dict(_FIXED)])
    assert first.assertions is not None and first.assertions[0]["commit_hash"] == head

    # the tree has moved on by the time the journal is replayed; the replay must not look again
    (root / "b.py").write_text("y = 1\n", encoding="utf-8")
    _git(root, "add", "b.py")
    _git(root, "commit", "-qm", "two")
    assert _git(root, "rev-parse", "HEAD") != head
    spy = _GitSpy(monkeypatch)
    replayed = _Captured()

    _create(root, replayed, first.assertions, _replay_learning_id="L-stamp-001", _from_journal=True)

    assert replayed.assertions is not None and replayed.assertions[0]["commit_hash"] == head
    assert spy.calls == []


# FR02 -----------------------------------------------------------------------------------------------------


def _update(root: Path, **upd: Any) -> dict[str, Any]:
    from trw_mcp.tools._learn_arg_bags import LearnUpdateFields
    from trw_mcp.tools._learn_update_impl import execute_learn_update

    seen: dict[str, Any] = {}

    def adapter_update(_trw_dir: Path, **fields: Any) -> dict[str, str]:
        seen.update(fields)
        return {"status": "updated", "learning_id": "L-1", "changes": "x"}

    result = execute_learn_update(
        trw_dir=root / ".trw",
        config=TRWConfig(),
        writer=_NoWriter(),  # type: ignore[arg-type]
        adapter_update=adapter_update,
        project_root=lambda: root,
        learning_id="L-1",
        status=None,
        summary=None,
        detail=None,
        impact=None,
        tags=None,
        type=None,
        confidence=None,
        upd=LearnUpdateFields(**upd),
    )
    assert result["status"] == "updated"
    return seen


class _NoWriter:
    def __getattr__(self, name: str) -> Any:
        return lambda *a, **kw: None


def test_an_update_that_replaces_assertions_stamps_them(
    checkout: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, head = checkout
    monkeypatch.setattr("trw_mcp.tools._learn_update_impl._sync_learning_yaml_backup", lambda *a, **kw: None)
    given = "e" * 40

    seen = _update(root, assertions=[dict(_FIXED), {**_SECOND, "commit_hash": given}])

    assert [a["commit_hash"] for a in seen["assertions"]] == [head, given]


def test_an_update_without_assertions_runs_no_git(checkout: tuple[Path, str], monkeypatch: pytest.MonkeyPatch) -> None:
    root, _ = checkout
    monkeypatch.setattr("trw_mcp.tools._learn_update_impl._sync_learning_yaml_backup", lambda *a, **kw: None)
    spy = _GitSpy(monkeypatch)

    seen = _update(root, nudge_line="a nudge")

    assert "assertions" not in seen
    assert spy.calls == []


# FR03 -----------------------------------------------------------------------------------------------------


def test_a_root_that_is_not_a_checkout_writes_the_learning_unstamped(tmp_path: Path) -> None:
    root = (tmp_path / "plain").resolve()
    (root / ".trw").mkdir(parents=True)
    captured = _Captured()

    result = _create(root, captured, [dict(_FIXED)])

    assert result["status"] == "recorded"
    assert captured.assertions == [dict(_FIXED)]


def _fault(kind: str, head: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = subprocess.run

    def run(argv: Any, *a: Any, **kw: Any) -> Any:
        if not (isinstance(argv, list) and "--no-lazy-fetch" in argv):
            return real(argv, *a, **kw)
        if kind == "missing":
            raise FileNotFoundError("git")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, 5.0)
        if kind == "nonzero":
            return subprocess.CompletedProcess(argv, 128, "", "fatal")
        return subprocess.CompletedProcess(argv, 0, f"{root}\n{'f' * 64}\n", "")

    monkeypatch.setattr(subprocess, "run", run)


@pytest.mark.parametrize("kind", ["missing", "timeout", "nonzero", "sha256_name"])
def test_a_git_that_cannot_answer_writes_the_learning_unstamped(
    kind: str, checkout: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, head = checkout
    _fault(kind, head, root, monkeypatch)
    captured = _Captured()

    result = _create(root, captured, [dict(_FIXED)])

    assert result["status"] == "recorded"
    assert captured.assertions == [dict(_FIXED)]


def test_a_stamped_assertion_passes_the_store_bound_text_scan(checkout: tuple[Path, str]) -> None:
    from trw_mcp.tools._learn_journal_wiring import store_bound_text

    _, head = checkout
    payload: dict[str, object] = {
        "summary": "s",
        "assertions": [{**_FIXED, "commit_hash": head}],
    }

    assert store_bound_text(payload) is None
    assert payload["assertions"] == [{**_FIXED, "commit_hash": head}], "the sha is not masked"


def test_the_stamp_is_the_projects_head_whatever_the_storage_directory_is_called(
    checkout: tuple[Path, str],
) -> None:
    root, head = checkout
    storage = root / ".knowledge"
    storage.mkdir()
    # the storage directory is itself a nested repository with a different HEAD
    _git(storage, "init", "-q")
    (storage / "n.txt").write_text("nested\n", encoding="utf-8")
    _git(storage, "add", "n.txt")
    _git(storage, "commit", "-qm", "nested")
    assert _git(storage, "rev-parse", "HEAD") != head
    captured = _Captured()

    _create(root, captured, [dict(_FIXED)], storage=storage)

    assert captured.assertions is not None and captured.assertions[0]["commit_hash"] == head


def test_a_create_without_a_project_root_is_written_unstamped(checkout: tuple[Path, str]) -> None:
    from trw_mcp.tools._learn_impl import execute_learn

    root, _ = checkout
    captured = _Captured()

    result = execute_learn(
        summary="no root given",
        detail="Callers that pass no project root get no stamp.",
        trw_dir=root / ".trw",
        config=TRWConfig(),
        assertions=[dict(_FIXED)],
        _adapter_store=captured.store,
        _generate_learning_id=lambda: "L-stamp-002",
        _save_learning_entry=lambda *a, **kw: root / "entry.yaml",
        _update_analytics=lambda *a, **kw: None,
        _list_active_learnings=lambda *a, **kw: [],
        _check_and_handle_dedup=lambda *a, **kw: None,
    )

    assert result["status"] == "recorded" and captured.assertions == [dict(_FIXED)]
