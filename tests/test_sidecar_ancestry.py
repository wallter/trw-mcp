"""The ancestor-sidecar read path of the pre-edit hint (``hint_sidecar_ancestor_enabled``).

Driven through ``compute_before_edit_hint`` with a fake ``GitReader`` at the git
port: HEAD comes from a real one-commit repo, and every ancestry question about
the (arbitrary) candidate shas is answered, and counted, by the fake. The real
``SubprocessGitReader`` and the real claude_code hook are exercised at the end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401  (fixture, imported by name)
from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint
from trw_mcp.channels.claude_code._hook_helpers import format_t2_hint
from trw_mcp.models.config import reload_config
from trw_mcp.state._entitlements import sign_entitlement_for_dev
from trw_mcp.tools import _before_edit_hint_core
from trw_mcp.tools._before_edit_hint_core import BeforeEditHintResult, compute_before_edit_hint
from trw_mcp.tools._sidecar_ancestry import (
    CommitUnavailableError,
    GitReadError,
    SubprocessGitReader,
    parse_name_status_z,
    shared_cache_dir,
    valid_sha,
)

_SCHEMA = "risk-report-sidecar/v0"
_FLAG = "hint_sidecar_ancestor_enabled"
_NEAR = "a" * 40
_MID = "b" * 40
_FAR = "c" * 40


@dataclass
class FakeGit:
    """``GitReader`` answering from tables; records every call by method name."""

    behind: dict[str, int | None] = field(default_factory=dict)  # sha -> distance, None = not an ancestor
    changed: dict[str, dict[str, str]] = field(default_factory=dict)
    fail: frozenset[str] = frozenset()
    dirty: frozenset[str] = frozenset()  # paths `git status` reports staged, unstaged or untracked
    calls: list[str] = field(default_factory=list)

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise GitReadError(f"`git {name}` exited 128: simulated")

    def ancestor_distance(self, sha: str, head: str) -> int | None:
        self._call("ancestor_distance")
        return self.behind.get(sha)

    def changed_paths(self, sha: str, head: str) -> dict[str, str]:
        self._call("changed_paths")
        return dict(self.changed.get(sha, {}))

    def worktree_changed(self, path: str) -> bool:
        self._call("worktree_changed")
        return path in self.dirty


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".trw/\n")
    head = _commit(repo, "foo.py", "x = 1\n")
    trw = repo / ".trw"
    trw.mkdir()
    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier="pro", issued_to="t@t", expires_at=future)
    (trw / "entitlements.yaml").write_text(f"tier: pro\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n")
    return repo, head


def _entry(target: str = "foo.py") -> dict[str, Any]:
    return {
        "target_path": target,
        "target_exists_in_map": True,
        "importers": ["imp.py"],
        "inferred_tests": ["tests/test_foo.py"],
        "doc_references": ["docs/foo.md"],
        "co_change_neighbors": ["bar.py", "baz.py"],
        "hotspot_warnings": ["non-trivial fan-in"],
        "risk_score": 0.42,
    }


def _batch(
    repo: Path,
    sha: str,
    *,
    mtime: float | None = None,
    entry: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    cache = repo / ".trw" / "distill" / "map-cache"
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"before-edit-batch-{sha}.json"
    envelope = {
        "schema_version": _SCHEMA,
        "sha": sha,
        "generated_at_unix": 1.0,
        "payload": {"hints": [entry or _entry()]},
        **(extra or {}),
    }
    path.write_text(json.dumps(envelope))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Learnings port faked empty; ``hint_delivered`` telemetry captured."""
    monkeypatch.setattr(_before_edit_hint_core, "_collect_learnings", lambda _fp, _root: ([], "ok"))
    records: list[dict[str, Any]] = []

    def _capture(**kwargs: Any) -> None:
        records.append(kwargs)

    monkeypatch.setattr("trw_mcp.channels._distill_telemetry.emit_hint_delivered", _capture)
    return records


def _hint(repo: Path, git: FakeGit, file_path: str = "foo.py") -> BeforeEditHintResult:
    return compute_before_edit_hint(file_path=file_path, repo_root=str(repo), git_reader=git)


def _render(result: BeforeEditHintResult) -> str:
    hint = result.distill_hint
    assert hint is not None
    return format_t2_hint(
        file_path=result.file_path,
        risk_score=hint.risk_score,
        hotspot_warnings=hint.hotspot_warnings,
        co_change_neighbors=hint.co_change_neighbors,
        inferred_tests=hint.inferred_tests,
        lessons=hint.lessons,
        lessons_status=hint.lessons_status,
        as_of=result.distill_as_of,
    )


def test_exact_head_batch_checks_only_worktree_state(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, head = _repo(tmp_path)
    _batch(repo, head)
    git = FakeGit()

    result = _hint(repo, git)

    assert (result.distill_status, result.distill_as_of, git.calls) == ("hint_available", None, ["worktree_changed"])
    assert "distill_as_of" not in result.model_dump()
    assert result.distill_hint is not None and result.distill_hint.risk_score == 0.42
    assert "AS-OF" not in _render(result)


def test_ancestor_within_the_bound_is_served_stale_with_an_as_of_line(
    tmp_path: Path, emitted: list[dict[str, Any]]
) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: 12}))

    assert result.distill_status == "hint_available_stale"
    assert result.distill_sidecar_sha == _NEAR
    assert result.distill_as_of is not None and result.distill_as_of.commits_behind == 12
    text = _render(result).splitlines()
    assert text[0] == "[TRW Distill Hint — T2]"  # the header the hook matches stays byte-identical
    assert text[1] == f"  AS-OF: {_NEAR[:9]}, 12 commits behind HEAD; historical, not current ({_FLAG})"
    assert emitted[-1]["tier"] == "T2"
    assert emitted[-1]["distill_status"] == "hint_available_stale"
    assert emitted[-1]["sidecar_commits_behind"] == 12


_CONTENT = ("risk_score", "hotspot_warnings", "importers", "inferred_tests", "doc_references")


@pytest.mark.parametrize(
    ("changed", "dirty"),
    [({"foo.py": "M"}, frozenset()), ({}, frozenset({"foo.py"}))],
    ids=["committed-since-the-sidecar", "staged-unstaged-or-untracked"],
)
def test_a_changed_target_keeps_only_co_change_history(
    tmp_path: Path, emitted: list[dict[str, Any]], changed: dict[str, str], dirty: frozenset[str]
) -> None:
    """P0: a claim the target's own later change could falsify is dropped; co-change stays "as of"."""
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: 3}, changed={_NEAR: changed}, dirty=dirty))

    hint = result.distill_hint
    assert hint is not None
    assert [getattr(hint, name) for name in _CONTENT] == [None, [], [], [], []]
    assert hint.co_change_neighbors == ["bar.py", "baz.py"]
    assert result.distill_as_of is not None and result.distill_as_of.target_changed is True
    assert "RISK" not in _render(result) and "WARN" not in _render(result)
    assert emitted[-1]["target_changed_since_sidecar"] is True


def test_an_unchanged_target_keeps_every_field_as_of_the_sidecar(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: 3}, changed={_NEAR: {"other.py": "M"}}))

    hint = result.distill_hint
    assert hint is not None
    assert [getattr(hint, name) for name in _CONTENT] == [
        0.42,
        ["non-trivial fan-in"],
        ["imp.py"],
        ["tests/test_foo.py"],
        ["docs/foo.md"],
    ]
    assert result.distill_as_of is not None and result.distill_as_of.target_changed is False
    lines = _render(result).splitlines()
    assert lines[1].startswith("  AS-OF: ") and lines[2] == "  RISK: 0.42"


@pytest.mark.parametrize(("dirty_paths", "changed"), [(["foo.py"], True), (["other.py"], False)])
def test_a_target_dirty_when_the_sidecar_was_built_counts_as_changed(
    tmp_path: Path, emitted: list[dict[str, Any]], dirty_paths: list[str], changed: bool
) -> None:
    """Envelope ``dirty_paths`` (the builder's dirty-at-build record) is provenance the diff cannot see."""
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR, extra={"dirty_paths": dirty_paths})

    result = _hint(repo, FakeGit(behind={_NEAR: 3}))

    assert result.distill_as_of is not None and result.distill_as_of.target_changed is changed
    assert result.distill_hint is not None and (result.distill_hint.risk_score is None) is changed


@pytest.mark.parametrize("bad", ["foo.py", [1], [""], ["a\nb"], {"foo.py": True}])
def test_malformed_dirty_paths_make_the_sidecar_malformed(
    tmp_path: Path, emitted: list[dict[str, Any]], bad: object
) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR, extra={"dirty_paths": bad})

    result = _hint(repo, FakeGit(behind={_NEAR: 3}))

    assert (result.distill_status, result.distill_hint) == ("sidecar_malformed", None)


def test_malformed_partner_entries_are_dropped_and_counted(
    tmp_path: Path, emitted: list[dict[str, Any]], captured_structlog: list[dict[str, Any]]
) -> None:
    repo, _head = _repo(tmp_path)
    entry = _entry()
    entry["co_change_neighbors"] = [5, "", "a\nb", {"x": 1}, "  ", "baz.py"]
    entry["importers"] = [None, "imp.py"]
    _batch(repo, _NEAR, entry=entry)

    result = _hint(repo, FakeGit(behind={_NEAR: 3}))

    assert result.distill_status == "hint_available_stale"
    assert result.distill_hint is not None
    assert (result.distill_hint.co_change_neighbors, result.distill_hint.importers) == (["baz.py"], ["imp.py"])
    assert "a\nb" not in _render(result)
    dropped = [e for e in captured_structlog if e.get("event") == "sidecar_partner_entries_dropped"]
    assert [e["count"] for e in dropped] == [6]


def test_the_reviewer_role_writes_no_ancestry_cache_and_gets_the_same_hint(
    tmp_path: Path, emitted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer lane writes nothing (PRD-CORE-300-FR12): ancestry is computed in memory only."""
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)
    cache = repo / ".trw" / "distill" / "map-cache"
    before = sorted(p.name for p in cache.iterdir())
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")

    reviewer_git = FakeGit(behind={_NEAR: 3})
    reviewer = _hint(repo, reviewer_git)

    assert sorted(p.name for p in cache.iterdir()) == before
    assert reviewer_git.calls == ["ancestor_distance", "changed_paths", "worktree_changed"]
    monkeypatch.delenv("TRW_SURFACE_ROLE")
    reload_config()  # the role is also read from config, which was built while the env marked a reviewer
    author = _hint(repo, FakeGit(behind={_NEAR: 3}))
    assert reviewer.model_dump() == author.model_dump()
    assert sorted(p.name for p in cache.iterdir()) != before  # non-vacuity: the author run does write it


def test_the_working_tree_check_is_never_cached(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)
    _hint(repo, FakeGit(behind={_NEAR: 3}))

    later = FakeGit(behind={_NEAR: 3}, dirty=frozenset({"foo.py"}))
    result = _hint(repo, later)

    assert later.calls == ["worktree_changed"]
    assert result.distill_as_of is not None and result.distill_as_of.target_changed is True


@pytest.mark.parametrize("gone", ["D", "R"])
def test_deleted_or_renamed_partners_are_dropped(tmp_path: Path, emitted: list[dict[str, Any]], gone: str) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: 3}, changed={_NEAR: {"bar.py": gone, "baz.py": "M"}}))

    assert result.distill_hint is not None
    assert result.distill_hint.co_change_neighbors == ["baz.py"]  # a merely modified partner stays


def test_a_non_ancestor_is_refused(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: None}))

    assert result.distill_status == "sidecar_too_far_behind"
    assert result.distill_hint is None
    assert "no cached sidecar is an ancestor of HEAD" in (result.distill_action or "")
    assert f"{_FLAG}: false" in (result.distill_action or "")
    assert emitted[-1]["tier"] == "pro"


@pytest.mark.parametrize(("behind", "served"), [(10, True), (11, False)])
def test_ancestor_past_the_bound_is_refused(
    tmp_path: Path, emitted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, behind: int, served: bool
) -> None:
    monkeypatch.setenv("TRW_HINT_SIDECAR_MAX_COMMITS_BEHIND", "10")
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: behind}))

    assert result.distill_status == ("hint_available_stale" if served else "sidecar_too_far_behind")
    if not served:
        assert "hint_sidecar_max_commits_behind=10 (the nearest is 11 commits behind)" in (result.distill_action or "")


def test_the_nearest_ancestor_wins_over_the_newest_file(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _FAR, mtime=3_000.0)  # newest file, but farthest
    _batch(repo, _NEAR, mtime=1_000.0)
    _batch(repo, _MID, mtime=2_000.0)
    _batch(repo, "d" * 40, mtime=4_000.0)  # newest of all, not an ancestor

    result = _hint(repo, FakeGit(behind={_FAR: 300, _NEAR: 5, _MID: 40, "d" * 40: None}))

    assert (result.distill_status, result.distill_sidecar_sha) == ("hint_available_stale", _NEAR)


def test_a_cache_hit_asks_git_nothing_and_a_head_move_recomputes(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, head = _repo(tmp_path)
    _batch(repo, _NEAR)
    cold = FakeGit(behind={_NEAR: 7})
    _hint(repo, cold)
    assert cold.calls == ["ancestor_distance", "changed_paths", "worktree_changed"]
    assert (repo / ".trw" / "distill" / "map-cache" / f"ancestry-{head}.json").is_file()

    warm = FakeGit(behind={_NEAR: 999})  # would change the answer if it were asked
    result = _hint(repo, warm)
    assert warm.calls == ["worktree_changed"]  # only the never-cached working-tree check
    assert result.distill_as_of is not None and result.distill_as_of.commits_behind == 7

    _commit(repo, "later.py", "z = 3\n")
    moved = FakeGit(behind={_NEAR: 8})
    result = _hint(repo, moved)
    assert moved.calls == ["ancestor_distance", "changed_paths", "worktree_changed"]
    assert result.distill_as_of is not None and result.distill_as_of.commits_behind == 8


@pytest.mark.parametrize(
    "corrupt",
    [
        "{ not json",
        '{"schema_version": "trw-sidecar-ancestry/v1", "head": "WRONG", "entries": {}}',
        '{"schema_version": "trw-sidecar-ancestry/v1", "head": "<HEAD>", "entries": {"<NEAR>": {"is_ancestor": true}}}',
    ],
    ids=["not-json", "wrong-head", "ancestor-without-distance"],
)
def test_a_corrupt_cache_is_recomputed_and_rewritten(
    tmp_path: Path, emitted: list[dict[str, Any]], corrupt: str
) -> None:
    repo, head = _repo(tmp_path)
    _batch(repo, _NEAR)
    cache_file = repo / ".trw" / "distill" / "map-cache" / f"ancestry-{head}.json"
    cache_file.write_text(corrupt.replace("<HEAD>", head).replace("<NEAR>", _NEAR))
    git = FakeGit(behind={_NEAR: 4})

    result = _hint(repo, git)

    assert git.calls == ["ancestor_distance", "changed_paths", "worktree_changed"]
    assert result.distill_status == "hint_available_stale"
    assert json.loads(cache_file.read_text())["entries"][_NEAR]["commits_behind"] == 4


@pytest.mark.parametrize("failing", ["changed_paths", "ancestor_distance", "worktree_changed"])
def test_a_git_failure_is_sidecar_diff_failed_and_not_t2(
    tmp_path: Path, emitted: list[dict[str, Any]], failing: str
) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: 2}, fail=frozenset({failing})))

    assert result.distill_status == "sidecar_diff_failed"
    assert result.distill_hint is None
    assert "simulated" in (result.distill_action or "") and f"{_FLAG}: false" in (result.distill_action or "")
    assert emitted[-1]["tier"] == "pro"


def test_flag_off_never_reads_an_ancestor(
    tmp_path: Path, emitted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRW_HINT_SIDECAR_ANCESTOR_ENABLED", "false")
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)
    git = FakeGit(behind={_NEAR: 1})

    result = _hint(repo, git)

    assert (result.distill_status, result.distill_hint, git.calls) == ("sidecar_missing", None, [])


def test_a_corrupt_ancestor_sidecar_is_malformed_not_missing(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR).write_text("{ not json")

    result = _hint(repo, FakeGit(behind={_NEAR: 2}))

    assert result.distill_status == "sidecar_malformed"


def test_no_batch_sidecar_at_all_stays_missing(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, _head = _repo(tmp_path)
    git = FakeGit()

    result = _hint(repo, git)

    assert (result.distill_status, git.calls) == ("sidecar_missing", [])


def test_an_absolute_path_matches_the_repo_relative_entry(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    """The edit hooks pass absolute paths; batch entries are repo-relative."""
    repo, head = _repo(tmp_path)
    _batch(repo, head)

    result = _hint(repo, FakeGit(), file_path=str(repo / "foo.py"))

    assert result.distill_status == "hint_available"


# --- the production git adapter and hook ------------------------------------


@pytest.mark.parametrize("bad", ["--output=/tmp/x", "HEAD", "abc", "a" * 65, "abcdefg..HEAD", "-p"])
def test_only_hex_shas_reach_git(bad: str, tmp_path: Path) -> None:
    reader = SubprocessGitReader(tmp_path)
    with pytest.raises(GitReadError, match="not a 7-64 character hex sha"):
        reader.ancestor_distance(bad, "a" * 40)
    assert valid_sha("AbC1234") == "AbC1234"


def test_the_subprocess_reader_answers_from_real_git(tmp_path: Path) -> None:
    repo, first = _repo(tmp_path)
    _commit(repo, "bar.py", "y = 2\n")
    (repo / "foo.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "drop foo")
    head = _git(repo, "rev-parse", "HEAD")
    # This checks git result parsing; fake-clock regressions check the production budget.
    reader = SubprocessGitReader(repo, timeout_s=30)

    assert reader.ancestor_distance(first, head) == 2
    assert reader.ancestor_distance(head, head) == 0
    assert reader.ancestor_distance(head, first) is None  # a descendant is not an ancestor
    # A commit the object store does not hold raises (so it is never cached as "not an ancestor" and is
    # asked again after a fetch), under the subclass that lets the lookup report "no proven ancestor".
    with pytest.raises(CommitUnavailableError, match="not in this repository's object store"):
        reader.ancestor_distance("0" * 40, head)
    tree = _git(repo, "rev-parse", f"{first}^{{tree}}")
    side = _git(repo, "commit-tree", "-p", first, "-m", "side", tree)
    assert reader.ancestor_distance(side, head) is None  # diverged: not reachable from HEAD
    assert reader.changed_paths(first, head) == {"bar.py": "A", "foo.py": "D"}


@pytest.mark.parametrize("state", ["clean", "unstaged", "staged", "untracked"])
def test_the_subprocess_reader_sees_every_working_tree_change(tmp_path: Path, state: str) -> None:
    repo, _head = _repo(tmp_path)
    target = "new.py" if state == "untracked" else "foo.py"
    if state != "clean":
        (repo / target).write_text("x = 99\n")
    if state == "staged":
        _git(repo, "add", target)

    assert SubprocessGitReader(repo).worktree_changed(target) is (state != "clean")


def test_name_status_parsing_handles_renames() -> None:
    raw = b"M\0a.py\0R100\0old.py\0new.py\0D\0gone.py\0"
    assert parse_name_status_z(raw) == {"a.py": "M", "old.py": "R", "new.py": "A", "gone.py": "D"}


def test_a_linked_worktree_reads_the_main_checkout_cache(tmp_path: Path) -> None:
    repo, _head = _repo(tmp_path)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    rel = ".trw/distill/map-cache"

    assert shared_cache_dir(worktree, rel).resolve() == (repo / rel).resolve()
    assert shared_cache_dir(repo, rel) == repo / rel


def _run_cc03(repo: Path, tool_use_id: str) -> str:
    """One CC-03 hook run for an absolute Edit path; returns the model-visible context."""
    from tests.test_sidecar_worktrees import _behavior_clock

    behavior_env = _behavior_clock(repo)
    event = {"tool_use_id": tool_use_id, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "foo.py")}}
    proc = subprocess.run(
        ["sh", str(deploy_distill_hint(repo))],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=30,
        cwd=repo,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "PYTHONPATH": CHECKOUT_PYTHONPATH,
            "TRW_PROJECT_DIR": str(repo),
            "HOME": str(repo),
            **behavior_env,
        },
    )
    assert proc.returncode == 0, proc.stderr
    context: str = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    return context


def test_the_claude_code_hook_renders_a_stale_hint_with_real_git(tmp_path: Path) -> None:
    """Production call site: the CC-03 hook, an absolute Edit path, and a sidecar two commits back.

    A deterministic clock isolates rendering from scheduler load; dedicated
    timeout tests retain coverage of the production alarm and fallback.
    """
    repo, first = _repo(tmp_path)
    _commit(repo, "one.py", "a = 1\n")
    _commit(repo, "two.py", "b = 2\n")
    _batch(repo, first)
    trw = repo / ".trw"
    (trw / "channels").mkdir()
    (trw / "channels" / "cc03-python.txt").write_text(sys.executable)
    (trw / "config.yaml").write_text("cc03_hook_enabled: true\n")

    context = _run_cc03(repo, "toolu-stale")

    lines = context.splitlines()
    assert lines[0] == "[TRW Distill Hint — T2]", context
    assert lines[1] == f"  AS-OF: {first[:9]}, 2 commits behind HEAD; historical, not current ({_FLAG})"
    assert "  RISK: 0.42" in context  # foo.py is untouched since the sidecar, so its fields stand "as of" it
    assert "CO-CHANGE: bar.py, baz.py" in context
