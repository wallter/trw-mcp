"""Truthful maintenance health view (PRD-CORE-181-FR07).

Belongs to the ``state/_helpers.py`` facade (re-exported there). One view
distinguishes ``fresh``, ``stale``, ``missing``→``unknown``, ``locked``,
``corrupt``, and post-cleanup states per maintenance component — and NEVER
passes from absent evidence: a missing artifact is ``unknown`` with a
remediation, not healthy.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict

logger = structlog.get_logger(__name__)

INVENTORY_RECEIPT = Path(".trw") / "retention" / "inventory.json"
CLEANUP_RECEIPT = Path(".trw") / "retention" / "cleanup-receipt.json"
QUARANTINE_DIR = Path(".trw") / "retention" / "quarantine"
MEMORY_DB = Path(".trw") / "memory" / "memory.db"

STALE_INVENTORY_SECONDS = 7 * 86400  # inventory older than a week is stale

# FR01/FR07 truthfulness: the retention registry is 'ok' only when it holds
# entries AND the production cleanup path actually consults it as deletion
# authority. This flag records that wiring landed (scripts/trw_runtime_hygiene.py
# ::collect_report -> _runtime_retention.delete_candidates -> classify_all). A
# registry with entries that nothing consults is NOT healthy — it is a Potemkin
# authority, so 'ok' must never be reported on entry-count alone.
_CLEANUP_CONSULTS_REGISTRY = True


class ComponentHealth(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    component: str
    state: str  # fresh | stale | unknown | locked | corrupt | ok
    detail: str = ""
    remediation: str = ""


def _receipt_health(root: Path, relative: Path, component: str, *, now: float) -> ComponentHealth:
    """Shared JSON-receipt classifier: missing→unknown, stale, corrupt, fresh."""
    target = root / relative
    if not target.is_file():
        return ComponentHealth(
            component=component,
            state="unknown",
            detail="no receipt on disk",
            remediation=f"produce {relative} via its maintenance command",
        )
    try:
        json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ComponentHealth(
            component=component,
            state="corrupt",
            detail="receipt exists but is unreadable",
            remediation=f"regenerate {relative}; do not trust the current bytes",
        )
    age = now - target.stat().st_mtime
    if age > STALE_INVENTORY_SECONDS:
        return ComponentHealth(
            component=component,
            state="stale",
            detail=f"receipt is {int(age // 86400)} days old",
            remediation="re-run the producing maintenance command",
        )
    return ComponentHealth(component=component, state="fresh")


def _wal_health(root: Path) -> ComponentHealth:
    db = root / MEMORY_DB
    if not db.is_file():
        return ComponentHealth(
            component="memory_wal",
            state="unknown",
            detail="memory.db absent",
            remediation="initialize memory or run from the project root",
        )
    lock = db.with_name(db.name + ".lock")
    if lock.exists():
        return ComponentHealth(
            component="memory_wal",
            state="locked",
            detail="explicit lock file present",
            remediation="wait for the holder or investigate a stale lock",
        )
    wal = db.with_name(db.name + "-wal")
    wal_bytes = wal.stat().st_size if wal.exists() else 0
    return ComponentHealth(component="memory_wal", state="ok", detail=f"wal_bytes={wal_bytes}")


def _quarantine_health(root: Path) -> ComponentHealth:
    quarantine = root / QUARANTINE_DIR
    if not quarantine.is_dir():
        return ComponentHealth(
            component="quarantine_restore",
            state="unknown",
            detail="no quarantine directory",
            remediation="no restore window exists; nothing is pending",
        )
    # FR07 fix: count actual pending restorables (one meta sidecar per quarantined
    # payload), NOT the fixed top-level directory entries. The prior
    # ``quarantine.iterdir()`` returned a constant 2 (the ``blobs/`` + ``meta/``
    # dirs) regardless of how many restorable payloads existed.
    meta_dir = quarantine / "meta"
    pending = sum(1 for _ in meta_dir.glob("*.json")) if meta_dir.is_dir() else 0
    return ComponentHealth(
        component="quarantine_restore",
        state="ok",
        detail=f"pending_restorables={pending}",
    )


def maintenance_health(root: Path, *, now: float | None = None) -> dict[str, object]:
    """Classify every maintenance component; absent evidence is never a pass."""
    reference = time.time() if now is None else now
    components = [
        _receipt_health(root, INVENTORY_RECEIPT, "inventory", now=reference),
        _receipt_health(root, CLEANUP_RECEIPT, "last_cleanup", now=reference),
        _wal_health(root),
        _quarantine_health(root),
    ]
    # Registry-backed counts (fail-closed source: unreadable registry -> unknown).
    from trw_mcp.telemetry.retention_registry import load_registry

    entries, readable = load_registry(root)
    if not readable:
        components.append(
            ComponentHealth(
                component="retention_registry",
                state="corrupt",
                detail="registry unreadable — every artifact retains",
                remediation="repair .trw/retention/registry.json from history",
            )
        )
    else:
        wired = _CLEANUP_CONSULTS_REGISTRY
        registry_ok = bool(entries) and wired
        components.append(
            ComponentHealth(
                component="retention_registry",
                state="ok" if registry_ok else "unknown",
                detail=f"registered_entries={len(entries)}; deletion_authority_wired={wired}",
                remediation="" if registry_ok else "register artifacts before any cleanup",
            )
        )
    return {
        "healthy": all(item.state in ("fresh", "ok") for item in components),
        "components": [item.model_dump(mode="json") for item in components],
    }
