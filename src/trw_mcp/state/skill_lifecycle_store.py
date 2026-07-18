"""Persisted skill-lifecycle store at ``.trw/skills/lifecycle.json``.

PRD-CORE-218-FR07 (adversarial remediation P0-3): the lifecycle state machine
in :mod:`trw_mcp.state.skill_lifecycle` was never consulted by production
discovery because no persisted state existed. This module is that persistence
layer — it owns the on-disk representation of each skill's current lifecycle
state plus its transition history, and it enforces the existing transition
contract by delegating every mutation to ``skill_lifecycle.advance`` /
``restore`` (owner / evidence_window / expiry / replacement / rollback_snapshot,
adjacent-forward-only, ``removed`` terminal).

Durability + safety invariants:

- **Atomic writes**: tmp file + ``fsync`` + ``os.replace`` so a crash mid-write
  never leaves a torn ``lifecycle.json``. The ``.trw/skills/`` dir is created
  ``0700`` and the file chmod'd ``0600`` (transition owners are PII-adjacent).
- **Fail-open to EMPTY**: a missing OR corrupt/unparseable store loads as ``{}``
  so discovery never breaks. Corruption is NEVER silent — it emits a WARN log
  (``skill_lifecycle_store_malformed_fallback``). Treating corrupt-as-empty
  means discovery advertises ALL skills (the safe default is "surface", not
  "silently withhold on unknown state"), but the WARN makes the degradation
  loud and auditable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.state.skill_lifecycle import (
    LifecycleTransition,
    SkillLifecycleRecord,
    SkillLifecycleState,
    advance,
    restore,
)

logger = structlog.get_logger(__name__)

_SKILLS_SUBDIR = "skills"
_LIFECYCLE_FILENAME = "lifecycle.json"
_TMP_SUFFIX = ".tmp"
_SCHEMA_VERSION = 1

__all__ = [
    "load_lifecycle_records",
    "load_lifecycle_states",
    "restore_skill",
    "skill_lifecycle_store_path",
    "transition_skill",
]


def skill_lifecycle_store_path() -> Path:
    """Return the absolute path of ``.trw/skills/lifecycle.json``.

    Resolved lazily so test fixtures can redirect ``resolve_trw_dir`` to a
    per-test ``tmp_path`` via monkeypatch.
    """
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir() / _SKILLS_SUBDIR / _LIFECYCLE_FILENAME


# --- (de)serialization -------------------------------------------------------


def _transition_to_dict(t: LifecycleTransition) -> dict[str, str]:
    return {
        "skill_name": t.skill_name,
        "from_state": t.from_state.value,
        "to_state": t.to_state.value,
        "owner": t.owner,
        "evidence_window": t.evidence_window,
        "expiry": t.expiry,
        "replacement": t.replacement,
        "rollback_snapshot": t.rollback_snapshot,
    }


def _record_to_dict(record: SkillLifecycleRecord) -> dict[str, Any]:
    return {
        "skill_name": record.skill_name,
        "state": record.state.value,
        "history": [_transition_to_dict(t) for t in record.history],
    }


def _dict_to_transition(raw: Any) -> LifecycleTransition:
    if not isinstance(raw, dict):
        raise TypeError("transition entry is not an object")
    return LifecycleTransition(
        skill_name=str(raw["skill_name"]),
        from_state=SkillLifecycleState(str(raw["from_state"])),
        to_state=SkillLifecycleState(str(raw["to_state"])),
        owner=str(raw["owner"]),
        evidence_window=str(raw["evidence_window"]),
        expiry=str(raw["expiry"]),
        replacement=str(raw["replacement"]),
        rollback_snapshot=str(raw["rollback_snapshot"]),
    )


def _dict_to_record(raw: Any) -> SkillLifecycleRecord:
    if not isinstance(raw, dict):
        raise TypeError("record entry is not an object")
    history_raw = raw.get("history", [])
    if not isinstance(history_raw, list):
        raise TypeError("record history is not a list")
    return SkillLifecycleRecord(
        skill_name=str(raw["skill_name"]),
        state=SkillLifecycleState(str(raw["state"])),
        history=tuple(_dict_to_transition(h) for h in history_raw),
    )


# --- load path ---------------------------------------------------------------


def load_lifecycle_records() -> dict[str, SkillLifecycleRecord]:
    """Return every persisted lifecycle record keyed by skill name.

    Fail-open: a missing store returns ``{}`` silently; a corrupt/unparseable
    store returns ``{}`` after a WARN log so retired skills are never withheld
    on a state we cannot read, yet the corruption is loud and auditable.
    """
    store_path = skill_lifecycle_store_path()
    if not store_path.exists():
        return {}

    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.warning(
            "skill_lifecycle_store_malformed_fallback",
            path=str(store_path),
            error=type(exc).__name__,
            detail=str(exc),
        )
        return {}

    if not isinstance(raw, dict):
        logger.warning(
            "skill_lifecycle_store_malformed_fallback",
            path=str(store_path),
            error="root_not_object",
            root_type=type(raw).__name__,
        )
        return {}

    records_raw = raw.get("records")
    if not isinstance(records_raw, dict):
        logger.warning(
            "skill_lifecycle_store_malformed_fallback",
            path=str(store_path),
            error="records_not_object",
        )
        return {}

    records: dict[str, SkillLifecycleRecord] = {}
    for name, entry in records_raw.items():
        try:
            records[str(name)] = _dict_to_record(entry)
        except (ValueError, KeyError, TypeError) as exc:
            # A single bad entry poisons the whole store: an unreadable state
            # for one skill means we cannot trust the file, and partially
            # advertising is worse than the loud fail-open-empty contract.
            logger.warning(
                "skill_lifecycle_store_malformed_fallback",
                path=str(store_path),
                error=type(exc).__name__,
                detail=f"skill={name!r}: {exc}",
            )
            return {}
    return records


def load_lifecycle_states() -> dict[str, SkillLifecycleState]:
    """Return a ``skill_name -> state`` map for discovery's advertising filter.

    Convenience projection of :func:`load_lifecycle_records`; shares its
    fail-open (missing/corrupt -> ``{}``) contract so discovery cannot break.
    """
    return {name: rec.state for name, rec in load_lifecycle_records().items()}


# --- save path ---------------------------------------------------------------


def _atomic_write(records: dict[str, SkillLifecycleRecord]) -> None:
    """Write *records* atomically with a 0700 dir and 0600 file (owner-only)."""
    from trw_mcp.state._paths_permissions import harden_dir_mode

    store_path = skill_lifecycle_store_path()
    harden_dir_mode(store_path.parent, create=True)
    payload = {
        "version": _SCHEMA_VERSION,
        "records": {name: _record_to_dict(rec) for name, rec in sorted(records.items())},
    }
    tmp_path = store_path.with_suffix(store_path.suffix + _TMP_SUFFIX)
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, store_path)
        try:
            os.chmod(store_path, 0o600)
        except OSError as exc:  # Windows ignores POSIX mode bits.
            logger.debug("skill_lifecycle_store_chmod_failed", error=type(exc).__name__)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# --- mutation APIs (contract-enforcing) --------------------------------------


def transition_skill(
    skill_name: str,
    to_state: SkillLifecycleState,
    *,
    owner: str,
    evidence_window: str,
    expiry: str,
    replacement: str,
    rollback_snapshot: str,
) -> SkillLifecycleRecord:
    """Advance *skill_name* one forward lifecycle step and persist the store.

    The transition contract (all fields required, adjacent-forward-only,
    ``removed`` terminal) is enforced by ``_skill_lifecycle.advance``; a refused
    transition raises ``LifecycleTransitionError`` and writes NOTHING.
    """
    records = load_lifecycle_records()
    current = records.get(skill_name, SkillLifecycleRecord(skill_name))
    updated = advance(
        current,
        to_state,
        owner=owner,
        evidence_window=evidence_window,
        expiry=expiry,
        replacement=replacement,
        rollback_snapshot=rollback_snapshot,
    )
    records[skill_name] = updated
    _atomic_write(records)
    return updated


def restore_skill(skill_name: str, *, owner: str, reason: str) -> SkillLifecycleRecord:
    """Reverse *skill_name*'s most recent transition and persist the store.

    Reversibility (only BEFORE ``removed``, and only with a prior transition to
    reverse) is enforced by ``_skill_lifecycle.restore``. An unknown skill or a
    refused restore raises ``LifecycleTransitionError`` / ``KeyError`` and writes
    nothing.
    """
    records = load_lifecycle_records()
    current = records[skill_name]
    updated = restore(current, owner=owner, reason=reason)
    records[skill_name] = updated
    _atomic_write(records)
    return updated
