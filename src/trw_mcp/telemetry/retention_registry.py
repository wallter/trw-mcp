"""Fail-closed artifact retention registry (PRD-CORE-181-FR01).

The SOLE deletion authority for local runtime artifacts. Every artifact must
be registered with authority class, producer, owner, sensitivity, retention
class, content digest, references, and restore need before it can EVER be
eligible for collection — and uncertainty always retains:

- unknown (unregistered) artifacts are retained;
- unreadable artifacts or registries are retained;
- digest conflicts (bytes changed since registration) are retained;
- referenced, sensitive, or authoritative artifacts are retained;
- duplicate/conflicting registrations retain until reconciled.

The existing fail-open ``artifact_registry.SurfaceRegistry`` is observational
HPO telemetry and is deliberately NEVER imported or consulted here — it is not
a deletion authority and cannot make anything eligible.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

REGISTRY_RELATIVE_PATH = Path(".trw") / "retention" / "registry.json"


class AuthorityClass(str, Enum):
    """Whether the artifact is an authoritative record or derived observation."""

    AUTHORITATIVE = "authoritative"  # source of truth — never collectible
    DERIVED = "derived"  # reproducible from an authoritative source
    OBSERVATIONAL = "observational"  # telemetry/diagnostics


class SensitivityClass(str, Enum):
    NONE = "none"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"  # never enters automated cleanup


class RetentionClass(str, Enum):
    PERMANENT = "permanent"
    RUN_SCOPED = "run_scoped"  # retained through run archival
    BOUNDED_DAYS = "bounded_days"


class RetentionDecision(str, Enum):
    ELIGIBLE = "eligible"
    RETAINED = "retained"


# Typed retention reasons (closed vocabulary; every RETAINED carries one).
REASON_UNREGISTERED = "unregistered_artifact"
REASON_UNREADABLE = "unreadable_artifact"
REASON_DIGEST_CONFLICT = "digest_conflict_since_registration"
REASON_REGISTRY_CONFLICT = "conflicting_registrations"
REASON_REFERENCED = "artifact_is_referenced"
REASON_SENSITIVE = "sensitive_artifact"
REASON_AUTHORITATIVE = "authoritative_artifact"
REASON_PERMANENT = "permanent_retention_class"
REASON_NOT_EXPIRED = "retention_window_not_expired"
REASON_REGISTRY_UNREADABLE = "registry_unreadable"


class RetentionEntry(BaseModel):
    """One registered artifact's retention contract."""

    model_config = ConfigDict(strict=True, frozen=True)

    path: str = Field(min_length=1)  # repository-relative
    authority_class: AuthorityClass
    producer: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    sensitivity: SensitivityClass
    retention_class: RetentionClass
    digest: str = Field(min_length=1)  # sha256:… of the registered bytes
    references: tuple[str, ...] = ()  # inbound references keeping it alive
    restore_required: bool = False
    retention_days: int = Field(default=0, ge=0)  # for BOUNDED_DAYS
    registered_epoch_days: int = Field(default=0, ge=0)


class ArtifactClassification(BaseModel):
    """Typed outcome for one artifact: ELIGIBLE or RETAINED with a reason."""

    model_config = ConfigDict(strict=True, frozen=True)

    path: str
    decision: RetentionDecision
    reason: str = ""


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_registry(root: Path) -> tuple[list[RetentionEntry], bool]:
    """Load entries; ``(entries, readable)``. An unreadable registry fails closed."""
    target = root / REGISTRY_RELATIVE_PATH
    if not target.exists():
        return [], True  # empty registry is valid — everything is unregistered
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return [RetentionEntry.model_validate(item, strict=False) for item in raw["entries"]], True
    except Exception:  # justified: unreadable/malformed registry must retain everything
        logger.warning("retention_registry_unreadable", exc_info=True)
        return [], False


def save_registry(root: Path, entries: list[RetentionEntry]) -> Path:
    target = root / REGISTRY_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"entries": [entry.model_dump(mode="json") for entry in entries]}
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def classify_artifact(
    path: str,
    root: Path,
    entries: list[RetentionEntry],
    *,
    registry_readable: bool = True,
    now_epoch_days: int = 0,
) -> ArtifactClassification:
    """Classify ONE artifact fail-closed. Only a current, valid, unreferenced,
    non-sensitive, non-authoritative, expired-window registration is ELIGIBLE."""

    def retained(reason: str) -> ArtifactClassification:
        return ArtifactClassification(path=path, decision=RetentionDecision.RETAINED, reason=reason)

    if not registry_readable:
        return retained(REASON_REGISTRY_UNREADABLE)
    matches = [entry for entry in entries if entry.path == path]
    if not matches:
        return retained(REASON_UNREGISTERED)
    if len(matches) > 1 and len({entry.digest for entry in matches}) > 1:
        return retained(REASON_REGISTRY_CONFLICT)
    entry = matches[0]
    if entry.sensitivity is SensitivityClass.SENSITIVE:
        return retained(REASON_SENSITIVE)
    if entry.authority_class is AuthorityClass.AUTHORITATIVE:
        return retained(REASON_AUTHORITATIVE)
    if entry.references:
        return retained(REASON_REFERENCED)
    if entry.retention_class is RetentionClass.PERMANENT:
        return retained(REASON_PERMANENT)
    target = root / path
    try:
        if not target.is_file():
            return retained(REASON_UNREADABLE)
        current = digest_file(target)
    except OSError:
        return retained(REASON_UNREADABLE)
    if current != entry.digest:
        return retained(REASON_DIGEST_CONFLICT)
    if entry.retention_class is RetentionClass.BOUNDED_DAYS:
        if now_epoch_days - entry.registered_epoch_days <= entry.retention_days:
            return retained(REASON_NOT_EXPIRED)
        return ArtifactClassification(path=path, decision=RetentionDecision.ELIGIBLE)
    # RUN_SCOPED without references and past registration is run-archival
    # territory; collection decisions there need an explicit archival receipt,
    # so fail closed here.
    return retained(REASON_NOT_EXPIRED)


def classify_all(
    paths: list[str],
    root: Path,
    *,
    now_epoch_days: int = 0,
) -> list[ArtifactClassification]:
    """Classify every path against the persisted registry (fail-closed)."""
    entries, readable = load_registry(root)
    return [
        classify_artifact(path, root, entries, registry_readable=readable, now_epoch_days=now_epoch_days)
        for path in paths
    ]
