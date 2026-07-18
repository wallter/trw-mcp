"""trw_adopt_run impl — extracted from ceremony.py.

Belongs to the ``ceremony.py`` facade. Re-exported there for back-compat.

Transfer an existing run's pin to the caller's session, with three guards:
out-of-project containment (no force), terminal-status (force=True
required), and live-owner (force=True required + WARN emission).

Extracted as DIST-243 batch 68 to push parent ``ceremony.py`` toward
the 350-LOC gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_pin_key
from trw_mcp.state._pin_store import (
    _iso_now,
    transfer_pin_entry,
)
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

if TYPE_CHECKING:
    from fastmcp import Context

    from trw_mcp.models.typed_dicts import TrwAdoptRunResultDict

logger = structlog.get_logger(__name__)

_events = FileEventLogger(FileStateWriter())


def _validate_adoptable_run(resolved: Path, project_root: Path) -> str:
    """Validate run metadata and event-log readability before pin mutation."""
    meta_dir = resolved / "meta"
    if not meta_dir.is_dir():
        raise StateError(f"run metadata directory missing: {meta_dir}", path=str(resolved))
    reader = FileStateReader(base_dir=project_root)
    run_yaml = meta_dir / "run.yaml"
    data = reader.read_yaml(run_yaml)
    run_id_raw = data.get("run_id", "") or data.get("id", "")
    if not isinstance(run_id_raw, str) or not run_id_raw.strip():
        raise StateError("run metadata missing run_id", path=str(run_yaml))
    run_id = run_id_raw.strip()
    if run_id != resolved.name:
        raise StateError(
            f"run metadata run_id does not match run directory: {run_id} != {resolved.name}",
            path=str(run_yaml),
            run_id=run_id,
            run_dir=resolved.name,
        )
    events_jsonl = meta_dir / "events.jsonl"
    if events_jsonl.exists():
        # trw:intentional strict=True — these reads are adoption integrity probes;
        # a malformed line must refuse adoption, not be leniently skipped.
        reader.read_jsonl(events_jsonl, strict=True)
    for checkpoints_jsonl in (meta_dir / "checkpoints.jsonl", resolved / "reports" / "checkpoints.jsonl"):
        if checkpoints_jsonl.exists():
            reader.read_jsonl(checkpoints_jsonl, strict=True)
    return str(data.get("status", "unknown"))


def adopt_run(
    ctx: Context | None,
    run_path: str,
    force: bool,
) -> TrwAdoptRunResultDict:
    """Transfer the run at ``run_path`` to the caller's pin.

    Raises :class:`StateError` for out-of-project paths, missing run
    directories, terminal-status runs (without ``force``), and live-owner
    runs (without ``force``). On success returns a TrwAdoptRunResultDict.
    """
    if not run_path:
        raise StateError("run_path is required for trw_adopt_run")

    # Containment check — must be under project root. No force override.
    # Import lazily so conftest's monkeypatch of the source attribute
    # reaches this call site (FR08 containment).
    from trw_mcp.state._paths import resolve_project_root

    resolved = Path(run_path).resolve()
    project_root = resolve_project_root()
    if not resolved.is_relative_to(project_root):
        raise StateError(f"run_path escapes project root: {resolved}", path=str(resolved))
    if not resolved.exists():
        raise StateError(f"run_path does not exist: {resolved}", path=str(resolved))

    caller_pin_key = resolve_pin_key(ctx=ctx, explicit=None)

    target_status = _validate_adoptable_run(resolved, project_root)

    if target_status in ("delivered", "complete", "failed") and not force:
        raise StateError(
            f"cannot adopt terminal-status run (status={target_status}); pass force=True to override",
            path=str(resolved),
            status=target_status,
        )

    config = get_config()
    _, previous_pin_key, previous_owner_heartbeat_age_hours, from_owner_was_live = transfer_pin_entry(
        caller_pin_key,
        resolved,
        force=force,
        pin_ttl_hours=float(config.pin_ttl_hours),
    )
    adopted_ts = _iso_now()

    if from_owner_was_live and force:
        structlog.get_logger(__name__).warning(
            "run_adopted_potential_writer_conflict",
            previous_pin_key=previous_pin_key,
            previous_owner_heartbeat_age_hours=previous_owner_heartbeat_age_hours,
            new_pin_key=caller_pin_key,
            run_path=str(resolved),
        )

    if (resolved / "meta").exists():
        _events.log_event(
            resolved / "meta" / "events.jsonl",
            "run_adopted",
            {
                "from_pin_key": previous_pin_key,
                "to_pin_key": caller_pin_key,
                "force_used": force,
                "previous_owner_heartbeat_age_hours": previous_owner_heartbeat_age_hours,
            },
        )

    logger.info(
        "run_adopted",
        run_path=str(resolved),
        from_pin_key=previous_pin_key,
        to_pin_key=caller_pin_key,
        force_used=force,
        from_owner_was_live=from_owner_was_live,
    )

    # Response carries previous_pin_key only; the run_adopted event keeps the
    # from/to naming — returning both names for the same value was duplication.
    return {
        "adopted_run_id": resolved.name,
        "previous_pin_key": previous_pin_key,
        "to_pin_key": caller_pin_key,
        "adopted_ts": adopted_ts,
        "from_owner_was_live": from_owner_was_live,
        "force_used": force,
    }
