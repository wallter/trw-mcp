from __future__ import annotations

from pathlib import Path

from tests._factories import make_run_dir_with_structure
from trw_mcp.state.persistence import FileStateWriter


def _make_run_dir(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a minimal run directory with run.yaml present."""
    return make_run_dir_with_structure(
        tmp_path,
        task="coverage-test",
        writer=writer,
        with_scratch_orchestrator=True,
    )
