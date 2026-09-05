"""Tests for anchor wiring in the trw_learn() flow — PRD-CORE-111, PRD-CORE-267.

PRD-CORE-267 FR01 moved the candidate-file source from "the mtime-newest
events.jsonl anywhere under .trw, else a name-only git diff over the shared
working tree" to "the run THIS caller pinned, and nothing else". Every test
here that used to lean on the git fallback now seeds a real pinned run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig


def _make_config() -> TRWConfig:
    return TRWConfig()


def _pin_run(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_id: str,
    modified: list[str],
    run_id: str = "20260101T000000Z-aaaa1111",
) -> Path:
    """Seed a run owned by *session_id* whose events name *modified*, and pin it.

    Returns the run directory. The event shape is the FLAT one the bundled
    ``post-tool-event.sh`` hook actually writes (path under the key ``file``).
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project_root))
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs, pin_active_run

    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()

    run_dir = project_root / ".trw" / "runs" / "task-a" / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "events.jsonl").write_text(
        "".join(json.dumps({"event": "file_modified", "file": path}) + "\n" for path in modified),
        encoding="utf-8",
    )
    pin_active_run(run_dir, session_id=session_id)
    return run_dir


class TestLearnCreatesAnchors:
    """Verify anchor generation is wired into execute_learn."""

    def test_learn_creates_anchors_from_the_sessions_own_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Files the caller's OWN run recorded become anchors (FR01 + FR02).

        Rewritten from ``test_learn_creates_anchors_when_git_diff_returns_py_files``,
        which mocked a name-only ``git diff`` over the whole working tree — the
        source FR01 deletes because on a shared checkout it is every concurrent
        agent's edits, not the caller's.
        """
        from trw_mcp.tools._learn_impl import execute_learn

        py_file = tmp_path / "mod.py"
        py_file.write_text("def my_func(): pass\n")

        captured_anchors: list[Any] = []

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            captured_anchors.extend(kwargs.get("anchors") or [])
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        trw_dir = tmp_path / ".trw"
        _pin_run(tmp_path, monkeypatch, session_id="sess-anchor", modified=[str(py_file)])

        result = execute_learn(
            summary="Test anchor wiring",
            detail="Verifying anchors flow through execute_learn for my_func",
            trw_dir=trw_dir,
            config=_make_config(),
            session_id="sess-anchor",
            _adapter_store=fake_store,
            _generate_learning_id=lambda: "L-test",
            _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
            _update_analytics=lambda *a, **kw: None,
            _list_active_learnings=lambda *a, **kw: [],
            _check_and_handle_dedup=lambda *a, **kw: None,
        )

        assert result.get("status") == "recorded"
        assert [a["symbol_name"] for a in captured_anchors] == ["my_func"]

    def test_learn_no_modified_files_empty_anchors(self, tmp_path: Path) -> None:
        """When git diff returns nothing, anchors passed to store are empty."""
        from trw_mcp.tools._learn_impl import execute_learn

        captured_anchors: list[Any] = [None]  # sentinel

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            captured_anchors[0] = kwargs.get("anchors", [])
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)

        execute_learn(
            summary="No anchors test",
            detail="No pinned run, so nothing can be anchored",
            trw_dir=trw_dir,
            config=_make_config(),
            session_id="sess-no-run",
            _adapter_store=fake_store,
            _generate_learning_id=lambda: "L-test2",
            _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
            _update_analytics=lambda *a, **kw: None,
            _list_active_learnings=lambda *a, **kw: [],
            _check_and_handle_dedup=lambda *a, **kw: None,
        )

        assert captured_anchors[0] == []

    def test_anchors_not_in_tool_params(self) -> None:
        """execute_learn does not accept anchors from the caller — they are auto-generated."""
        import inspect

        from trw_mcp.tools._learn_impl import execute_learn

        sig = inspect.signature(execute_learn)
        # anchors must NOT be a parameter of execute_learn
        assert "anchors" not in sig.parameters, "anchors should be auto-generated, not a caller-supplied parameter"

    def test_learn_git_failure_still_records(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing ``git diff -U0`` costs the ranges, never the learning."""
        from trw_mcp.tools._learn_impl import execute_learn

        stored: list[dict[str, object]] = []

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            stored.append({"anchors": kwargs.get("anchors", [])})
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        py_file = tmp_path / "mod.py"
        py_file.write_text("def my_func(): pass\n")
        trw_dir = tmp_path / ".trw"
        _pin_run(tmp_path, monkeypatch, session_id="sess-gitfail", modified=[str(py_file)])

        # Simulate git failure — no ranges at all.
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("trw_mcp.tools._learn_anchor_sources.subprocess.run", return_value=mock_result):
            result = execute_learn(
                summary="Git fails gracefully",
                detail="Anchor failure must not block learning",
                trw_dir=trw_dir,
                config=_make_config(),
                session_id="sess-gitfail",
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test3",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result.get("status") == "recorded"
        # No ranges and no name overlap: nothing qualifies, and that is the
        # answer rather than the file's first symbol (PRD-CORE-267 FR02).
        assert stored[0]["anchors"] == []

    def test_learn_subprocess_exception_still_records(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If subprocess.run raises, anchor generation is skipped but learn still records."""
        from trw_mcp.tools._learn_impl import execute_learn

        stored: list[dict[str, object]] = []

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            stored.append({"anchors": kwargs.get("anchors", [])})
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        py_file = tmp_path / "mod.py"
        py_file.write_text("def my_func(): pass\n")
        trw_dir = tmp_path / ".trw"
        _pin_run(tmp_path, monkeypatch, session_id="sess-timeout", modified=[str(py_file)])

        with patch(
            "trw_mcp.tools._learn_anchor_sources.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 5),
        ):
            result = execute_learn(
                summary="Subprocess timeout",
                detail="Timeout must not block learning",
                trw_dir=trw_dir,
                config=_make_config(),
                session_id="sess-timeout",
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test4",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result.get("status") == "recorded"
        assert stored[0]["anchors"] == []


class TestAnchorYamlRoundTrip:
    """Verify anchors survive through store_learning / _memory_transforms."""

    def test_learning_to_memory_entry_with_anchors(self, tmp_path: Path) -> None:
        """Anchors in dict form are converted to Anchor objects in MemoryEntry."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        # Use relative path (Anchor model requires it)
        anchor_dict: dict[str, object] = {
            "file": "src/mod.py",
            "symbol_name": "my_func",
            "symbol_type": "function",
            "signature": "def my_func(): pass",
            "line_range": (1, 1),
        }

        entry = _learning_to_memory_entry(
            "L-test",
            "test summary",
            "test detail",
            anchors=[anchor_dict],
        )

        assert len(entry.anchors) == 1
        assert entry.anchors[0].symbol_name == "my_func"
        assert entry.anchors[0].symbol_type == "function"
        assert entry.anchors[0].file == "src/mod.py"

    def test_learning_to_memory_entry_absolute_path_converted(self, tmp_path: Path) -> None:
        """Absolute file paths in anchor dicts are converted to relative paths."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        # Create a real file so we can get a meaningful relative path
        anchor_dict: dict[str, object] = {
            "file": "/home/user/project/src/mod.py",
            "symbol_name": "abs_func",
            "symbol_type": "function",
            "signature": "def abs_func(): pass",
            "line_range": (1, 1),
        }

        entry = _learning_to_memory_entry(
            "L-abs",
            "absolute path test",
            "test detail",
            anchors=[anchor_dict],
        )

        # Entry should have an anchor with a relative path
        assert len(entry.anchors) == 1
        assert not entry.anchors[0].file.startswith("/")

    def test_learning_to_memory_entry_no_anchors(self) -> None:
        """When no anchors provided, entry.anchors is empty."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        entry = _learning_to_memory_entry(
            "L-noanchor",
            "no anchor summary",
            "detail",
        )
        assert entry.anchors == []

    def test_learning_to_memory_entry_malformed_anchor_skipped(self) -> None:
        """Malformed anchor dicts are silently skipped."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        anchor_dict: dict[str, object] = {
            # Missing required 'symbol_name' key — Anchor model will reject
            "file": "src/mod.py",
            "symbol_name": "",  # empty — fails Anchor's min_length=1
            "symbol_type": "function",
            "signature": "",
        }

        entry = _learning_to_memory_entry(
            "L-bad",
            "malformed anchor",
            "detail",
            anchors=[anchor_dict],
        )
        # Malformed anchor is skipped — no crash
        assert entry.anchors == []


class TestModifiedFilesForRun:
    """PRD-CORE-267 FR01: read ``file_modified`` paths from ONE run's events."""

    def _write_events(self, run_dir: Path, lines: list[dict[str, object]]) -> Path:
        meta = run_dir / "meta"
        meta.mkdir(parents=True, exist_ok=True)
        events = meta / "events.jsonl"
        events.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        return events

    def test_reads_nested_data_path_shape(self, tmp_path: Path) -> None:
        from trw_mcp.tools._learn_anchor_sources import modified_files_for_run

        run_dir = tmp_path / "runs" / "task-x" / "run-1"
        self._write_events(
            run_dir,
            [
                {"event": "session_start", "ts": "t0"},
                {"event": "file_modified", "data": {"path": "src/a.py"}},
                {"event": "file_modified", "data": {"path": "src/b.py"}},
            ],
        )
        assert modified_files_for_run(run_dir) == ["src/a.py", "src/b.py"]

    def test_reads_flat_type_path_shape(self, tmp_path: Path) -> None:
        from trw_mcp.tools._learn_anchor_sources import modified_files_for_run

        run_dir = tmp_path / "runs" / "task-x" / "run-1"
        self._write_events(run_dir, [{"type": "file_modified", "path": "lib/c.py"}])
        assert modified_files_for_run(run_dir) == ["lib/c.py"]

    def test_reads_the_flat_file_key_the_bundled_hook_writes(self, tmp_path: Path) -> None:
        """FR01: the shape ``data/hooks/post-tool-event.sh`` actually appends.

        The hook writes ``{"event": "file_modified", "tool": ..., "file": ...}``.
        The pre-FR01 reader looked only for ``data.path`` and ``path``, so it
        returned nothing for every hook-written run and the caller silently fell
        through to a name-only diff over the whole shared working tree. This is
        the assertion that fails on the parent commit.
        """
        from trw_mcp.tools._learn_anchor_sources import modified_files_for_run

        run_dir = tmp_path / "runs" / "task-x" / "run-1"
        self._write_events(
            run_dir,
            [{"event": "file_modified", "tool": "Edit", "file": "/abs/src/hooked.py"}],
        )
        assert modified_files_for_run(run_dir) == ["/abs/src/hooked.py"]

    def test_torn_tail_line_does_not_lose_the_complete_records(self, tmp_path: Path) -> None:
        """A partially-written JSONL line is skipped; everything before it survives."""
        from trw_mcp.tools._learn_anchor_sources import modified_files_for_run

        run_dir = tmp_path / "runs" / "task-x" / "run-1"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "events.jsonl").write_text(
            json.dumps({"event": "file_modified", "file": "src/a.py"}) + '\n{"event": "file_mod',
            encoding="utf-8",
        )
        assert modified_files_for_run(run_dir) == ["src/a.py"]

    def test_absent_events_file_returns_empty(self, tmp_path: Path) -> None:
        from trw_mcp.tools._learn_anchor_sources import modified_files_for_run

        run_dir = tmp_path / "runs" / "task-x" / "run-1"
        run_dir.mkdir(parents=True)
        assert modified_files_for_run(run_dir) == []


class TestGitDiffLineRanges:
    """FR04 step 2: parse git diff -U0 hunk headers into changed line ranges.

    Moved to ``_learn_anchor_sources`` by PRD-CORE-267; the parsing contract is
    unchanged, only the module that owns it.
    """

    def test_parses_hunk_headers(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _learn_anchor_sources

        diff = (
            "diff --git a/src/mod.py b/src/mod.py\n"
            "index 111..222 100644\n"
            "--- a/src/mod.py\n"
            "+++ b/src/mod.py\n"
            "@@ -1,0 +2,3 @@\n"
            "+added line\n"
            "@@ -10,2 +14,1 @@\n"
            "+one line\n"
            "diff --git a/src/other.go b/src/other.go\n"
            "--- a/src/other.go\n"
            "+++ b/src/other.go\n"
            "@@ -5 +5 @@\n"
            "+changed\n"
        )
        result = MagicMock()
        result.returncode = 0
        result.stdout = diff
        with patch.object(_learn_anchor_sources.subprocess, "run", return_value=result):
            ranges = _learn_anchor_sources.git_diff_line_ranges(tmp_path)

        # +2,3 -> lines 2..4 ; +14,1 -> line 14 ; +5 (no count) -> line 5
        assert ranges == {
            "src/mod.py": [(2, 4), (14, 14)],
            "src/other.go": [(5, 5)],
        }

    def test_git_child_inherits_explicit_session_pin(self, tmp_path: Path) -> None:
        """FR12: producer-owned subprocesses propagate the caller session pin."""
        from trw_mcp.tools import _learn_anchor_sources

        result = MagicMock(returncode=0, stdout="")
        with patch.object(_learn_anchor_sources.subprocess, "run", return_value=result) as run:
            _learn_anchor_sources.git_diff_line_ranges(tmp_path, session_id="session-from-caller")

        assert run.call_args.kwargs["env"]["TRW_SESSION_ID"] == "session-from-caller"

    def test_deletion_hunk_anchors_at_start(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _learn_anchor_sources

        diff = (
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -7,3 +6,0 @@\n"  # pure deletion: +6,0
        )
        result = MagicMock()
        result.returncode = 0
        result.stdout = diff
        with patch.object(_learn_anchor_sources.subprocess, "run", return_value=result):
            ranges = _learn_anchor_sources.git_diff_line_ranges(tmp_path)
        assert ranges == {"x.py": [(6, 6)]}

    def test_dev_null_target_skipped(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _learn_anchor_sources

        diff = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n"
        result = MagicMock()
        result.returncode = 0
        result.stdout = diff
        with patch.object(_learn_anchor_sources.subprocess, "run", return_value=result):
            ranges = _learn_anchor_sources.git_diff_line_ranges(tmp_path)
        assert ranges == {}


class TestAnchorStoredInSqliteAndYaml:
    """FR04 (PRD :467): a learning created via execute_learn persists anchors to
    BOTH the SQLite entry and the YAML backup file."""

    def test_anchor_stored_in_sqlite_and_yaml(self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess as _sp

        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state._memory_connection import get_backend
        from trw_mcp.tools._learn_impl import execute_learn

        trw_dir = tmp_project / ".trw"
        config = TRWConfig(trw_dir=str(trw_dir))

        # Real git repo with a committed baseline + an uncommitted change so
        # `git diff -U0 HEAD` produces a real hunk over a function body.
        src = tmp_project / "svc.py"
        src.write_text("def handle():\n    return 0\n")
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": __import__("os").environ.get("PATH", ""),
        }
        _sp.run(["git", "init", "-q"], cwd=tmp_project, check=True, env=env)
        _sp.run(["git", "add", "svc.py"], cwd=tmp_project, check=True, env=env)
        _sp.run(["git", "commit", "-q", "-m", "base"], cwd=tmp_project, check=True, env=env)
        # Modify the function body (line 2 changes) so a hunk targets `handle`.
        src.write_text("def handle():\n    return 42\n")

        # PRD-CORE-267 FR01: the candidate file must come from THIS session's
        # own run, so the run that recorded the edit is pinned to this caller.
        _pin_run(tmp_project, monkeypatch, session_id="sess-persist", modified=[str(src)])

        result = execute_learn(
            summary="Anchor persistence across SQLite and YAML",
            detail="Verifying dual-write of anchors for FR04",
            trw_dir=trw_dir,
            config=config,
            session_id="sess-persist",
        )
        learning_id = str(result["learning_id"])

        # --- SQLite side ---
        backend = get_backend(trw_dir)
        entry = backend.get(learning_id, namespace="default")
        assert entry is not None, "learning must be stored in SQLite"
        assert len(entry.anchors) >= 1, "anchors must be persisted to SQLite"
        assert entry.anchors[0].symbol_name == "handle"
        assert entry.anchor_validity == 1.0

        # --- YAML backup side ---
        yaml_path = Path(str(result["path"]))
        assert yaml_path.exists(), "YAML backup must be written"
        from ruamel.yaml import YAML

        loaded = YAML(typ="safe").load(yaml_path.read_text())
        assert loaded.get("anchors"), "anchors must be present in YAML backup"
        assert loaded["anchors"][0]["symbol_name"] == "handle"


class TestUnanchoredLearningHasNoValidity:
    """PRD-CORE-244 FR01: an unassessed anchor score is None, never a perfect 1.0.

    ``anchor_validity`` fed the recall ranking boost, so defaulting it to 1.0
    handed every unanchored learning — 7,541 rows in the live store — the top
    anchor score without a single anchor ever being checked.
    """

    def test_no_anchors_resolves_to_none_validity(self, tmp_path: Path) -> None:
        """The resolver reports "not assessed", not "perfect".

        With no pinned run there is no candidate file set at all (PRD-CORE-267
        FR01), so the resolver never reaches generation.
        """
        from trw_mcp.tools import _learn_anchors

        (tmp_path / ".trw").mkdir()
        anchors, validity = _learn_anchors.resolve_learn_anchors(tmp_path, "L-none", session_id="sess-with-no-pin")

        assert anchors == []
        assert validity is None

    def test_learn_without_anchors_persists_null_validity(self, tmp_project: Path) -> None:
        """End of the real learn path: the stored entry claims no anchor score.

        No git repo and no run events, so nothing can be anchored — exactly the
        shape of the majority of stored learnings.
        """
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state._memory_connection import get_backend
        from trw_mcp.tools._learn_impl import execute_learn

        trw_dir = tmp_project / ".trw"
        config = TRWConfig(trw_dir=str(trw_dir))

        result = execute_learn(
            summary="Unanchored learning carries no anchor score",
            detail="PRD-CORE-244 FR01 — a default must not assert a positive result",
            trw_dir=trw_dir,
            config=config,
        )
        learning_id = str(result["learning_id"])

        backend = get_backend(trw_dir)
        entry = backend.get(learning_id, namespace="default")
        assert entry is not None
        assert entry.anchors == []
        assert entry.anchor_validity is None
