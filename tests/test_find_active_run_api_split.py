"""PRD-FIX-085 FR01: find_active_run() is pin-only.

The legacy mtime-scan fallback that previously kicked in when context is
None was split behind an explicit opt-in and then deleted outright by
PRD-FIX-132, which found it had never acquired a production caller. Five
hot-path regressions in one week shared the root cause "caller forgot
context= and fell through to the slow scan." Removing the implicit
fallback eliminates the regression class mechanically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.state._paths import find_active_run
from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


def _make_run_dir(tmp_path: Path, task: str = "t", run_id: str = "r-001") -> Path:
    run_dir = tmp_path / ".trw" / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    _writer.write_yaml(
        meta / "run.yaml",
        {"run_id": run_id, "task": task, "status": "active", "phase": "implement"},
    )
    return run_dir


def test_find_active_run_returns_none_when_no_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """find_active_run() with no pin must NOT scan the filesystem."""
    _make_run_dir(tmp_path)
    # Even though run.yaml files exist on disk, no pin = None.
    with patch("trw_mcp.state._paths.get_pinned_run", return_value=None):
        result = find_active_run()
    assert result is None


def test_find_active_run_returns_pinned_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """find_active_run() returns the pinned run when one exists."""
    run_dir = _make_run_dir(tmp_path)
    with patch("trw_mcp.state._paths.get_pinned_run", return_value=run_dir):
        result = find_active_run()
    assert result == run_dir


def test_find_active_run_does_not_read_run_yaml_on_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mechanical assertion: zero run.yaml reads when no pin.

    This is the durable defense against the regression class. The
    legacy scan PyYAML-parsed every run.yaml; if that ever returns,
    this assertion fails loudly.
    """
    from trw_mcp.state.persistence import FileStateReader

    for i in range(5):
        _make_run_dir(tmp_path, task=f"task-{i}", run_id=f"r-{i}")

    read_paths: list[Path] = []
    real_read = FileStateReader.read_yaml

    def counting_read(self: FileStateReader, path: Path) -> Any:
        read_paths.append(Path(path))
        return real_read(self, path)

    monkeypatch.setattr(FileStateReader, "read_yaml", counting_read)
    monkeypatch.setattr("trw_mcp.state._paths.get_pinned_run", lambda **_: None)

    result = find_active_run()
    assert result is None
    yaml_reads = [p for p in read_paths if p.name == "run.yaml"]
    assert yaml_reads == [], f"find_active_run() with no pin must NOT scan run.yaml; got {yaml_reads}"


def test_find_active_run_passes_context_through_to_pin_lookup() -> None:
    """find_active_run(context=ctx) routes the ctx to get_pinned_run."""
    from trw_mcp.state._paths import TRWCallContext

    ctx = TRWCallContext(
        session_id="probe-ctx",
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )

    captured: dict[str, Any] = {}

    def fake_get_pinned_run(**kwargs: Any) -> None:
        captured.update(kwargs)
        return None

    with patch("trw_mcp.state._paths.get_pinned_run", side_effect=fake_get_pinned_run):
        find_active_run(context=ctx)

    assert captured["context"] is ctx


def test_no_callers_of_find_active_run_without_context() -> None:
    """FR01 acceptance: every find_active_run() call site in src/ either
    passes context= or is explicitly annotated with ``# noqa: PRD-FIX-085``.

    The grep is the durable mechanical guard. Reads .py files only;
    shell hooks have their own ``find_active_run`` function in
    ``data/hooks/lib-trw.sh`` which is a separate namespace.
    """
    import subprocess

    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            r"find_active_run(",
            "src/",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode not in (0, 1):
        pytest.skip(f"grep failed: {result.stderr}")

    offenders: list[str] = []
    for line in result.stdout.splitlines():
        # Strip "path:lineno:" prefix to inspect the source line.
        try:
            _, _, source = line.split(":", 2)
        except ValueError:
            continue
        stripped = source.strip()

        # Skip non-call lines.
        if "find_active_run(" not in stripped:
            continue
        # Skip the function definition itself.
        if stripped.startswith("def find_active_run"):
            continue
        # Skip docstring / comment mentions (line begins with #, ", or ').
        if stripped.startswith(("#", '"', "'")):
            continue
        # Skip imports.
        if stripped.startswith(("from ", "import ")):
            continue
        # Skip docstring mentions: function name wrapped in backticks
        # (`find_active_run()` or ``find_active_run()``) is a sphinx/rst
        # reference, never a real call.
        if "`find_active_run(" in stripped or "``find_active_run(" in stripped:
            continue
        # Allow callers that pass context= or session_id= keyword arguments.
        if "context=" in stripped or "session_id=" in stripped:
            continue
        # Allow callers explicitly annotated as PRD-FIX-085 compat OR the
        # pre-existing "compat: legacy zero-argument test doubles" marker.
        if "noqa: PRD-FIX-085" in source or "compat: legacy" in source:
            continue
        offenders.append(line)

    assert not offenders, (
        "find_active_run() callers without context=, session_id=, or "
        "`# noqa: PRD-FIX-085` annotation:\n" + "\n".join(offenders)
    )
