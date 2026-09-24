"""Formation pause and resume with per-member acknowledgement (next batch P0).

Belongs to the ``trw_mcp.formation`` facade; re-exported there.

The pause lives in a SIBLING file, ``formation.pause.yaml``, never in the manifest
(lane C MF1). ``FormationManifest`` is ``extra="forbid"``: a ``pause:`` key would
make every member server built before this change refuse EVERY manifest read for
exactly the length of the pause. An old server never reads the sibling, so it
misses the pause silently and shows up in the roll call as not acked.

- ``pause`` and ``resume`` are orchestrator-only (the same authority as revise and
  add-slot). One pause at a time; resume deletes the file.
- ``ack`` writes only ``acks[<own member_id>]``, must name the ``pause_id`` the
  member was taught (``pause_id_mismatch`` otherwise, lane C SF2), and is
  idempotent. The pause file is re-read INSIDE its lock, so a late ack racing a
  resume refuses ``not_paused`` and never recreates the file.
- Events: pause and resume go to the orchestrator run, each ack to the ACKING
  member's own run (lane C SF3: no cross-run writes).
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog
import yaml

from trw_mcp.formation._manifest import FormationError, FormationManifest
from trw_mcp.formation._store import _exclusive, read_manifest, resolve_manifest_path, write_owner_only

logger = structlog.get_logger(__name__)

PAUSE_FILE = "formation.pause.yaml"


class PauseError(FormationError):
    """A pause verb refused with a closed reason (``not_paused``, ``pause_id_mismatch``, ...)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PauseRecord:
    pause_id: str
    reason: str
    since_utc: str
    until_utc: str | None = None
    acks: dict[str, str] = field(default_factory=dict)

    def overdue(self, now: datetime | None = None) -> bool:
        """Past ``until_utc``. Advisory only: resume is always explicit."""
        if not self.until_utc:
            return False
        try:
            until = datetime.fromisoformat(self.until_utc.replace("Z", "+00:00"))
        except ValueError:
            # trw-fail-silent-allow: pause() validated until_utc; a hand-edited value is simply not overdue
            return False
        return (now or datetime.now(timezone.utc)) > until


def orchestrator_run_of(manifest: FormationManifest) -> Path:
    """The ONE definition of "the orchestrator" for pause (C review SF1): the manifest's own field.

    Never ``manifest_path.parent``: that equals it only while the manifest lives in the
    orchestrator run, and a relocated manifest would silently close the status/reply
    path to the lead that must stay open mid-pause.
    """
    return Path(manifest.orchestrator_run_path).resolve()


def pause_path(manifest_path: Path) -> Path:
    return manifest_path.parent / PAUSE_FILE


def read_pause(manifest_path: Path) -> PauseRecord | None:
    """The active pause beside *manifest_path*, or ``None``. A malformed file raises."""
    path = pause_path(manifest_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # trw-fail-silent-allow: no pause file is the normal "not paused" answer
        return None
    except (OSError, yaml.YAMLError) as exc:
        raise FormationError(f"the pause record at {path} is unreadable: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("pause_id"), str):
        raise FormationError(f"the pause record at {path} is malformed")
    acks = raw.get("acks") or {}
    if not isinstance(acks, dict):
        raise FormationError(f"the pause record at {path} has malformed acks")
    until = raw.get("until_utc")
    return PauseRecord(
        pause_id=raw["pause_id"],
        reason=str(raw.get("reason", "")),
        since_utc=str(raw.get("since_utc", "")),
        until_utc=str(until) if until else None,
        acks={str(k): str(v) for k, v in acks.items()},
    )


def _write(path: Path, record: PauseRecord) -> None:
    data: dict[str, object] = {"pause_id": record.pause_id, "reason": record.reason, "since_utc": record.since_utc}
    if record.until_utc:
        data["until_utc"] = record.until_utc
    data["acks"] = dict(record.acks)
    tmp = path.with_suffix(path.suffix + ".tmp")
    write_owner_only(tmp, yaml.safe_dump(data, sort_keys=False).encode("utf-8"))
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_event(run_path: Path, event_type: str, data: dict[str, object]) -> None:
    from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

    FileEventLogger(FileStateWriter()).log_event(run_path / "meta" / "events.jsonl", event_type, data)


def _orchestrator_manifest(trw_dir: Path, formation_id: str, caller_run_path: Path | None) -> Path:
    manifest_path = resolve_manifest_path(trw_dir, formation_id)
    expected = orchestrator_run_of(read_manifest(manifest_path))
    if caller_run_path is None or caller_run_path.resolve() != expected:
        raise PauseError("not_orchestrator", f"pause and resume of {formation_id!r} are orchestrator-only")
    return manifest_path


def pause(
    *,
    trw_dir: Path,
    formation_id: str,
    caller_run_path: Path | None,
    reason: str,
    until_utc: str | None,
    lock_timeout_seconds: float,
) -> PauseRecord:
    """Start a pause. Refused when one is already active."""
    if until_utc is not None:
        try:
            datetime.fromisoformat(until_utc.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PauseError("invalid_until", f"--until must be an ISO-8601 time, not {until_utc!r}") from exc
    manifest_path = _orchestrator_manifest(trw_dir, formation_id, caller_run_path)
    path = pause_path(manifest_path)
    with _exclusive(path, timeout_seconds=lock_timeout_seconds):
        current = read_pause(manifest_path)
        if current is not None:
            raise PauseError("already_paused", f"formation {formation_id!r} is already paused ({current.pause_id})")
        record = PauseRecord(pause_id=secrets.token_hex(8), reason=reason, since_utc=_now(), until_utc=until_utc)
        _write(path, record)
    assert caller_run_path is not None  # noqa: S101 - checked by _orchestrator_manifest
    _log_event(caller_run_path, "formation_paused", {"formation_id": formation_id, "pause_id": record.pause_id})
    logger.info("formation_paused", formation_id=formation_id, pause_id=record.pause_id)
    return record


def resume(*, trw_dir: Path, formation_id: str, caller_run_path: Path | None, lock_timeout_seconds: float) -> str:
    """End the active pause; returns its ``pause_id``. Refused when not paused."""
    manifest_path = _orchestrator_manifest(trw_dir, formation_id, caller_run_path)
    path = pause_path(manifest_path)
    with _exclusive(path, timeout_seconds=lock_timeout_seconds):
        current = read_pause(manifest_path)
        if current is None:
            raise PauseError("not_paused", f"formation {formation_id!r} is not paused")
        path.unlink()
    assert caller_run_path is not None  # noqa: S101 - checked by _orchestrator_manifest
    _log_event(caller_run_path, "formation_resumed", {"formation_id": formation_id, "pause_id": current.pause_id})
    logger.info("formation_resumed", formation_id=formation_id, pause_id=current.pause_id)
    return current.pause_id


def ack(
    *, manifest_path: Path, member_id: str, member_run_path: Path, pause_id: str, lock_timeout_seconds: float
) -> bool:
    """Record *member_id*'s ack of *pause_id*. Returns False when it was already recorded.

    The caller has bound *member_id* as an eligible member; this writes nothing else.
    """
    path = pause_path(manifest_path)
    with _exclusive(path, timeout_seconds=lock_timeout_seconds):
        current = read_pause(manifest_path)  # re-read inside the lock: a resume may have won the race
        if current is None:
            raise PauseError("not_paused", "the formation is not paused")
        if current.pause_id != pause_id:
            raise PauseError("pause_id_mismatch", "that pause_id is not the active pause")
        if member_id in current.acks:
            return False
        _write(path, PauseRecord(**{**current.__dict__, "acks": {**current.acks, member_id: _now()}}))
    _log_event(member_run_path, "formation_pause_acked", {"pause_id": pause_id, "member_id": member_id})
    return True


def roll_call(manifest_path: Path) -> dict[str, object] | None:
    """The ``formation status`` pause block: who of the eligible members has acked.

    Eligible means joined or active and not the orchestrator's own run; a terminal
    or pending member is not in the roll call.
    """
    record = read_pause(manifest_path)
    if record is None:
        return None
    manifest = read_manifest(manifest_path)
    orchestrator = orchestrator_run_of(manifest)
    eligible = [
        m.member_id
        for m in manifest.members
        if str(m.status) in ("joined", "active") and (m.run_path is None or Path(m.run_path).resolve() != orchestrator)
    ]
    block: dict[str, object] = {"pause_id": record.pause_id, "reason": record.reason, "since_utc": record.since_utc}
    if record.until_utc:
        block["until_utc"] = record.until_utc
        block["overdue"] = record.overdue()
    block["acked"] = [m for m in eligible if m in record.acks]
    block["not_acked"] = [m for m in eligible if m not in record.acks]
    return block


__all__ = [
    "PAUSE_FILE",
    "PauseError",
    "PauseRecord",
    "ack",
    "orchestrator_run_of",
    "pause",
    "pause_path",
    "read_pause",
    "resume",
    "roll_call",
]
