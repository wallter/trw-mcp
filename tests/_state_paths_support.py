from __future__ import annotations

from pathlib import Path

from trw_mcp.state.persistence import FileStateWriter


def _make_run(
    base: Path,
    task: str,
    run_id: str,
    status: str = "active",
    phase: str = "implement",
    writer: FileStateWriter | None = None,
) -> Path:
    """Create a minimal run directory with run.yaml."""
    run_dir = base / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    data = {
        "run_id": run_id,
        "task": task,
        "status": status,
        "phase": phase,
    }
    if writer:
        writer.write_yaml(meta / "run.yaml", data)
    else:
        import yaml

        (meta / "run.yaml").write_text(yaml.dump(data))
    return run_dir
