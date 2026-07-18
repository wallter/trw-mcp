"""Tests for anchor wiring in the trw_learn() flow — PRD-CORE-111."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig


def _make_config() -> TRWConfig:
    return TRWConfig()


class TestLearnCreatesAnchors:
    """Verify anchor generation is wired into execute_learn."""

    def test_learn_creates_anchors_when_git_diff_returns_py_files(self, tmp_path: Path) -> None:
        """When git diff returns modified .py files, anchors are generated and passed to store."""
        from trw_mcp.tools._learn_impl import execute_learn

        # Create a real .py file that anchor_generation can read
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
        trw_dir.mkdir()

        # Mock git diff to return the py_file path
        git_stdout = str(py_file.relative_to(trw_dir.parent)) + "\n"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = git_stdout

        with patch("trw_mcp.tools._learn_anchors.subprocess.run", return_value=mock_result):
            # Run the actual flow with real file
            result = execute_learn(
                summary="Test anchor wiring",
                detail="Verifying anchors flow through execute_learn",
                trw_dir=trw_dir,
                config=_make_config(),
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result.get("status") == "recorded"
        # Anchors should have been captured
        assert len(captured_anchors) >= 1
        assert captured_anchors[0]["symbol_name"] == "my_func"

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
        trw_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""  # no modified files

        with patch("trw_mcp.tools._learn_anchors.subprocess.run", return_value=mock_result):
            execute_learn(
                summary="No anchors test",
                detail="Git returns empty diff",
                trw_dir=trw_dir,
                config=_make_config(),
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

    def test_learn_git_failure_still_records(self, tmp_path: Path) -> None:
        """If git subprocess fails, anchor generation is skipped but learn still records."""
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

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Simulate git failure
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("trw_mcp.tools._learn_anchors.subprocess.run", return_value=mock_result):
            result = execute_learn(
                summary="Git fails gracefully",
                detail="Anchor failure must not block learning",
                trw_dir=trw_dir,
                config=_make_config(),
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test3",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result.get("status") == "recorded"
        # Anchors should be empty (git failed, returncode != 0)
        assert stored[0]["anchors"] == []

    def test_learn_subprocess_exception_still_records(self, tmp_path: Path) -> None:
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

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch(
            "trw_mcp.tools._learn_anchors.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 5),
        ):
            result = execute_learn(
                summary="Subprocess timeout",
                detail="Timeout must not block learning",
                trw_dir=trw_dir,
                config=_make_config(),
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


class TestModifiedFilesFromEvents:
    """FR04 step 1: read file_modified paths from run events.jsonl."""

    def _write_events(self, trw_dir: Path, lines: list[dict[str, object]]) -> Path:
        meta = trw_dir / "runs" / "task-x" / "run-1" / "meta"
        meta.mkdir(parents=True)
        events = meta / "events.jsonl"
        events.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        return events

    def test_reads_nested_data_path_shape(self, tmp_path: Path) -> None:
        from trw_mcp.tools._learn_anchors import _modified_files_from_events

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._write_events(
            trw_dir,
            [
                {"event": "session_start", "ts": "t0"},
                {"event": "file_modified", "data": {"path": "src/a.py"}},
                {"event": "file_modified", "data": {"path": "src/b.py"}},
            ],
        )
        assert _modified_files_from_events(trw_dir) == ["src/a.py", "src/b.py"]

    def test_reads_flat_type_path_shape(self, tmp_path: Path) -> None:
        from trw_mcp.tools._learn_anchors import _modified_files_from_events

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        self._write_events(
            trw_dir,
            [{"type": "file_modified", "path": "lib/c.py"}],
        )
        assert _modified_files_from_events(trw_dir) == ["lib/c.py"]

    def test_no_events_returns_empty(self, tmp_path: Path) -> None:
        from trw_mcp.tools._learn_anchors import _modified_files_from_events

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        assert _modified_files_from_events(trw_dir) == []

    def test_events_used_before_git_fallback(self, tmp_path: Path) -> None:
        """resolve_learn_anchors prefers events.jsonl over git diff (FR04 step 1)."""
        from trw_mcp.tools import _learn_anchors

        project_root = tmp_path
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # A real source file the events point at.
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "a.py").write_text("def from_events(): pass\n")
        self._write_events(
            trw_dir,
            [{"event": "file_modified", "data": {"path": "src/a.py"}}],
        )

        # git must NOT be consulted for the file list when events exist; make it
        # explode if called for name-only. -U0 (ranges) may still be called.
        def fake_run(cmd: list[str], **_kw: Any) -> MagicMock:
            result = MagicMock()
            result.returncode = 1  # ranges: no git repo -> empty ranges
            result.stdout = ""
            assert "--name-only" not in cmd, "events present: name-only diff should be skipped"
            return result

        with patch.object(_learn_anchors.subprocess, "run", side_effect=fake_run):
            anchors, validity = _learn_anchors.resolve_learn_anchors(project_root, trw_dir, "L-x")

        assert [a["symbol_name"] for a in anchors] == ["from_events"]
        assert validity == 1.0


class TestGitDiffLineRanges:
    """FR04 step 2: parse git diff -U0 hunk headers into changed line ranges."""

    def test_parses_hunk_headers(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _learn_anchors

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
        with patch.object(_learn_anchors.subprocess, "run", return_value=result):
            ranges = _learn_anchors._git_diff_line_ranges(tmp_path)

        # +2,3 -> lines 2..4 ; +14,1 -> line 14 ; +5 (no count) -> line 5
        assert ranges == {
            "src/mod.py": [(2, 4), (14, 14)],
            "src/other.go": [(5, 5)],
        }

    def test_git_child_inherits_explicit_session_pin(self, tmp_path: Path) -> None:
        """FR12: producer-owned subprocesses propagate the caller session pin."""
        from trw_mcp.tools import _learn_anchors

        result = MagicMock(returncode=0, stdout="")
        with patch.object(_learn_anchors.subprocess, "run", return_value=result) as run:
            _learn_anchors._git_diff_line_ranges(tmp_path, session_id="session-from-caller")

        assert run.call_args.kwargs["env"]["TRW_SESSION_ID"] == "session-from-caller"

    def test_deletion_hunk_anchors_at_start(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _learn_anchors

        diff = (
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -7,3 +6,0 @@\n"  # pure deletion: +6,0
        )
        result = MagicMock()
        result.returncode = 0
        result.stdout = diff
        with patch.object(_learn_anchors.subprocess, "run", return_value=result):
            ranges = _learn_anchors._git_diff_line_ranges(tmp_path)
        assert ranges == {"x.py": [(6, 6)]}

    def test_dev_null_target_skipped(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _learn_anchors

        diff = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n"
        result = MagicMock()
        result.returncode = 0
        result.stdout = diff
        with patch.object(_learn_anchors.subprocess, "run", return_value=result):
            ranges = _learn_anchors._git_diff_line_ranges(tmp_path)
        assert ranges == {}


class TestAnchorStoredInSqliteAndYaml:
    """FR04 (PRD :467): a learning created via execute_learn persists anchors to
    BOTH the SQLite entry and the YAML backup file."""

    def test_anchor_stored_in_sqlite_and_yaml(self, tmp_project: Path) -> None:
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

        result = execute_learn(
            summary="Anchor persistence across SQLite and YAML",
            detail="Verifying dual-write of anchors for FR04",
            trw_dir=trw_dir,
            config=config,
        )
        learning_id = str(result["learning_id"])

        # --- SQLite side ---
        backend = get_backend(trw_dir)
        entry = backend.get(learning_id)
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
