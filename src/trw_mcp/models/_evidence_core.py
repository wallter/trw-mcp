"""Core content-binding + validation-result models — PRD-CORE-205 FR01/FR03.

Belongs to the ``models/evidence_receipts.py`` facade. Re-exported there so
callers keep a single import point.

This module owns the *pure, I/O-free* data contracts and canonicalization
primitive that every receipt family (review, build, verification) shares:

- :class:`EvidenceLimits` — the FR09 exact bounds, enforced before hashing.
- Closed enums (:class:`ReceiptState`, :class:`EntryState`, ...).
- :class:`ContentEntry` / :class:`ContentBinding` — a deterministic,
  repository-relative content manifest with a canonical SHA-256 digest.
- :class:`RunOwnedScope` — a server-issued scope whose required paths a caller
  cannot shrink.
- :class:`ReceiptValidationResult` — the closed result domain every gate reader
  consumes instead of interpreting mode-specific dicts.

Canonicalization is deterministic and byte-exact (FR01/NFR06): sorted keys,
UTF-8, ``/`` separators, entries sorted by path, no timestamps. The stable
filesystem reads that *populate* an entry live in the service layer
(``tools/_evidence_binding.py``) so this module stays subprocess/network free
(NFR03).
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: int = 1
CANONICAL_ALGORITHM: Literal["sha256"] = "sha256"

# Domain-separation prefixes keep a manifest digest, a review-input digest, and
# a plan digest from ever colliding even on identical byte payloads (FR02).
_MANIFEST_DOMAIN = b"trw.core205.manifest.v1\x00"


class EvidenceLimits:
    """FR09 exact V1 bounds. Enforced BEFORE hashing allocates unbounded work.

    These are hard ceilings, not tunables — a receipt that exceeds any of them
    fails toward no-evidence with a stable reason rather than silently
    truncating (NFR01/NFR03).
    """

    MAX_CONTENT_ENTRIES = 4096
    MAX_BOUND_FILE_BYTES = 64 * 1024 * 1024  # 64 MiB per bound file
    MAX_TOTAL_BOUND_BYTES = 256 * 1024 * 1024  # 256 MiB total
    MAX_EVIDENCE_ARTIFACTS = 256
    MAX_PLAN_RESULTS = 256
    MAX_FINDINGS = 1000
    MAX_PATH_BYTES = 1024
    MAX_FREE_TEXT_BYTES = 4096
    MAX_CANONICAL_RECEIPT_BYTES = 1024 * 1024  # 1 MiB per canonical receipt


class ReceiptState(str, Enum):
    """Closed result domain for receipt validation (FR03).

    Only ``VALID`` is positive evidence. Every other state fails toward
    no-evidence and never falls back to a legacy positive projection (NFR01).
    """

    VALID = "valid"
    STALE_CONTENT = "stale_content"
    INVALID = "invalid"
    DEGRADED = "degraded"
    LEGACY_UNBOUND = "legacy_unbound"
    MISSING = "missing"
    SCOPE_UNVERIFIABLE = "scope_unverifiable"
    PLAN_INCOMPLETE = "plan_incomplete"
    UNSTABLE_READ = "unstable_read"
    RECEIPT_ID_COLLISION = "receipt_id_collision"
    UNKNOWN_MODE = "unknown_mode"


class EntryState(str, Enum):
    """Content-entry filesystem state."""

    FILE = "file"
    DELETED = "deleted"
    SYMLINK = "symlink"


class ScopeConfidence(str, Enum):
    """How trustworthy the server's run-owned scope derivation is (FR01)."""

    VERIFIED = "verified"
    UNVERIFIABLE = "scope_unverifiable"


class EvidenceMode(str, Enum):
    """Observe/enforce compatibility mode (FR08)."""

    OBSERVE = "observe"
    ENFORCE = "enforce"


class ContentEntry(BaseModel):
    """One repository-relative bound path and its stable content descriptor.

    ``byte_digest``/``byte_size`` are populated only for ``FILE`` (and,
    optionally, a symlink whose target resolves beneath the project root).
    ``link_target`` is the raw link descriptor for a ``SYMLINK`` entry.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    path: str = Field(description="Repository-relative path, normalized with '/'.")
    state: EntryState
    byte_digest: str | None = Field(default=None, description="Lowercase hex SHA-256 of raw file bytes.")
    byte_size: int | None = Field(default=None, ge=0)
    link_target: str | None = Field(default=None, description="Raw symlink target descriptor.")

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if not value:
            raise ValueError("content entry path must be non-empty")
        if len(value.encode("utf-8")) > EvidenceLimits.MAX_PATH_BYTES:
            raise ValueError("content entry path exceeds MAX_PATH_BYTES")
        if "\x00" in value:
            raise ValueError("content entry path contains a NUL byte")
        normalized = value.replace("\\", "/")
        if normalized.startswith("/"):
            raise ValueError("content entry path must be repository-relative, not absolute")
        if normalized != value:
            raise ValueError("content entry path must already use '/' separators")
        parts = normalized.split("/")
        if ".." in parts or "." in parts:
            raise ValueError("content entry path must not contain '.' or '..' segments")
        return value

    @model_validator(mode="after")
    def _validate_state_shape(self) -> ContentEntry:
        if self.state is EntryState.FILE:
            if self.byte_digest is None or self.byte_size is None:
                raise ValueError("file entry requires byte_digest and byte_size")
            if self.link_target is not None:
                raise ValueError("file entry must not carry a link_target")
        elif self.state is EntryState.DELETED:
            if self.byte_digest is not None or self.byte_size is not None or self.link_target is not None:
                raise ValueError("deleted entry must not carry byte or link descriptors")
        elif self.state is EntryState.SYMLINK:
            if not self.link_target:
                raise ValueError("symlink entry requires a link_target")
        return self


class ContentBinding(BaseModel):
    """Deterministic, repository-relative content manifest with canonical digest.

    The canonical SHA-256 covers canonical JSON (sorted keys, UTF-8, ``/``
    separators, no timestamps) over entries sorted by path — so any order
    permutation yields the same digest and any byte/state/link/add/delete change
    changes it (FR01 acceptance).
    """

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    algorithm: Literal["sha256"] = Field(default=CANONICAL_ALGORITHM)
    scope_id: str
    scope_digest: str
    project_identity: str = Field(description="Server-resolved project root identity, not caller-supplied.")
    entries: tuple[ContentEntry, ...] = Field(default_factory=tuple)
    manifest_digest: str

    @model_validator(mode="after")
    def _validate_binding(self) -> ContentBinding:
        if len(self.entries) > EvidenceLimits.MAX_CONTENT_ENTRIES:
            raise ValueError("content binding exceeds MAX_CONTENT_ENTRIES")
        paths = [e.path for e in self.entries]
        if len(paths) != len(set(paths)):
            raise ValueError("content binding contains duplicate normalized paths")
        total_bytes = sum(e.byte_size or 0 for e in self.entries)
        if total_bytes > EvidenceLimits.MAX_TOTAL_BOUND_BYTES:
            raise ValueError("content binding exceeds MAX_TOTAL_BOUND_BYTES")
        expected = compute_manifest_digest(self.entries)
        if self.manifest_digest != expected:
            raise ValueError("manifest_digest does not match canonical entries")
        return self


class RunOwnedScope(BaseModel):
    """Server-issued scope for one resolved project/run identity (FR01).

    The server derives ``required_paths`` from its durable run-owned file-change
    journal plus explicit operator ownership. A caller MAY propose additive
    ``proposed_paths`` but SHALL NOT delete or reclassify a required path — the
    validator enforces that required ⊆ effective.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION)
    scope_id: str
    scope_digest: str
    project_identity: str
    required_paths: tuple[str, ...] = Field(default_factory=tuple)
    proposed_paths: tuple[str, ...] = Field(default_factory=tuple)
    provenance: str = Field(default="run_journal", description="run_journal | operator_ownership | mixed.")
    confidence: ScopeConfidence = ScopeConfidence.VERIFIED

    @model_validator(mode="after")
    def _validate_scope(self) -> RunOwnedScope:
        if len(self.required_paths) + len(self.proposed_paths) > EvidenceLimits.MAX_CONTENT_ENTRIES:
            raise ValueError("run-owned scope exceeds MAX_CONTENT_ENTRIES")
        expected = compute_scope_digest(self.scope_id, self.project_identity, self.required_paths)
        if self.scope_digest != expected:
            raise ValueError("scope_digest does not match canonical required_paths")
        return self

    @property
    def effective_paths(self) -> tuple[str, ...]:
        """Required paths plus caller-proposed additive paths, deduped + sorted."""
        return tuple(sorted(set(self.required_paths) | set(self.proposed_paths)))

    def caller_cannot_shrink(self, proposed_required: tuple[str, ...]) -> bool:
        """True iff every server-required path is still present in a caller proposal."""
        return set(self.required_paths).issubset(set(proposed_required))


class ReceiptValidationResult(BaseModel):
    """Closed-domain validation outcome consumed by every gate reader (FR03).

    ``is_positive`` is the ONLY authority a reader should act on — a naked
    ``substantive`` boolean, artifact existence, or clean verdict never upgrades
    a non-``VALID`` state.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    state: ReceiptState
    reason_code: str = Field(description="Stable machine reason code, e.g. 'manifest_digest_mismatch'.")
    receipt_id: str | None = None
    typed_present: bool = Field(
        default=False,
        description="A typed receipt artifact existed (even if invalid). Blocks legacy fallback.",
    )
    diagnostics: str = Field(default="", description="Bounded, redacted human diagnostic.")

    @field_validator("diagnostics")
    @classmethod
    def _bound_diagnostics(cls, value: str) -> str:
        if len(value.encode("utf-8")) > EvidenceLimits.MAX_FREE_TEXT_BYTES:
            return value.encode("utf-8")[: EvidenceLimits.MAX_FREE_TEXT_BYTES].decode("utf-8", "ignore")
        return value

    @property
    def is_positive(self) -> bool:
        """Only VALID is positive evidence (NFR01 fail-toward-no-evidence)."""
        return self.state is ReceiptState.VALID


def canonical_json(payload: object) -> bytes:
    """Deterministic UTF-8 canonical JSON: sorted keys, compact, no whitespace.

    Shared by every digest so the same logical payload always serializes to the
    same bytes across platforms (NFR06).
    """
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _entry_canonical_obj(entry: ContentEntry) -> dict[str, object]:
    return {
        "path": entry.path,
        "state": entry.state.value,
        "byte_digest": entry.byte_digest,
        "byte_size": entry.byte_size,
        "link_target": entry.link_target,
    }


def compute_manifest_digest(entries: tuple[ContentEntry, ...]) -> str:
    """Canonical SHA-256 over path-sorted entries (order-independent, FR01)."""
    ordered = sorted(entries, key=lambda e: e.path)
    body = canonical_json([_entry_canonical_obj(e) for e in ordered])
    return hashlib.sha256(_MANIFEST_DOMAIN + body).hexdigest()


def compute_scope_digest(scope_id: str, project_identity: str, required_paths: tuple[str, ...]) -> str:
    """Immutable digest binding a scope to its exact required-path set."""
    body = canonical_json(
        {
            "scope_id": scope_id,
            "project_identity": project_identity,
            "required_paths": sorted(required_paths),
        }
    )
    return hashlib.sha256(b"trw.core205.scope.v1\x00" + body).hexdigest()


def domain_digest(domain: str, payload: object) -> str:
    """Domain-separated SHA-256 over canonical JSON (plan / review-input digests)."""
    prefix = f"trw.core205.{domain}.v1\x00".encode()
    return hashlib.sha256(prefix + canonical_json(payload)).hexdigest()
