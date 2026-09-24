"""One locked read-modify-write path for ``<run>/meta/run.yaml`` (N1).

Every writer that loads run.yaml, changes some keys and writes it back goes
through :func:`update_run_yaml`. The read happens INSIDE the exclusive lock, so
two concurrent writers (a phase transition and a formation stamp, say) apply
their changes in sequence instead of the second one silently erasing the first.
The lock is the shared ``lock_for_rmw`` sibling-file protocol; the write stays
``FileStateWriter.write_yaml`` (atomic temp-file + rename).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

from trw_mcp.state.persistence import FileStateReader, FileStateWriter, lock_for_rmw

__all__ = ["complete_run_yaml", "create_run_yaml", "run_yaml_path", "update_run_yaml"]


def run_yaml_path(run_path: Path) -> Path:
    """Return the run.yaml location for *run_path*."""
    return run_path / "meta" / "run.yaml"


def create_run_yaml(run_path: Path, data: dict[str, object]) -> None:
    """Write a NEW run.yaml under the same lock; refuse if one already exists (N1, W1 half).

    Run initialization must never overwrite a live run's state: an existing file
    (a racing init, or a reused run id) raises StateError instead of being
    silently replaced. The check and the write share the lock every update takes.
    """
    from trw_mcp.exceptions import StateError

    target = run_yaml_path(run_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with lock_for_rmw(target):
        if target.exists():
            raise StateError("run.yaml already exists; refusing to overwrite a run", path=str(target))
        FileStateWriter().write_yaml(target, data)


def complete_run_yaml(run_path: Path, data: dict[str, object]) -> None:
    """Replace the scaffold's minimal run.yaml with the full init record, under the lock.

    ``trw_init`` scaffolds the run (``create_run_yaml`` writes a minimal record),
    then resolves its profile and writes the full record. That second write may
    replace ONLY the record this same init created: the existing ``run_id`` must
    equal ``data["run_id"]``. Anything else, including a missing file, is refused,
    so an init can never overwrite a different run. The replacement is WHOLE: the
    scaffold's own keys (``source: local_cli``, ``created_at``) must not survive into
    the init record. That is safe only while nothing else writes run.yaml between
    the scaffold and this call; a writer added there (a formation stamp, a
    surface-snapshot pointer) would be erased and must be folded into *data*.
    """
    from trw_mcp.exceptions import StateError

    target = run_yaml_path(run_path)
    with lock_for_rmw(target):
        existing = FileStateReader().read_yaml(target) if target.is_file() else None
        if existing is None or existing.get("run_id") != data.get("run_id"):
            raise StateError("run.yaml is not this init's scaffold; refusing to overwrite", path=str(target))
        FileStateWriter().write_yaml(target, data)


def update_run_yaml(run_path: Path, mutate: Callable[[dict[str, object]], object]) -> bool:
    """Apply *mutate* to run.yaml under an exclusive lock and persist the result.

    *mutate* receives the freshly-read mapping and changes it in place; its
    return value is ignored. Returns ``False`` without writing when run.yaml
    does not exist (an update never creates a run), else ``True``. A mutation
    that changes nothing writes nothing, so it never refreshes the file's mtime.
    """
    target = run_yaml_path(run_path)
    if not target.parent.is_dir():
        return False  # no run here: taking the lock would create meta/ for it
    with lock_for_rmw(target):
        if not target.is_file():
            return False
        data = FileStateReader().read_yaml(target)
        before = copy.deepcopy(data)
        mutate(data)
        if data != before:
            FileStateWriter().write_yaml(target, data)
        return True
