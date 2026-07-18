"""Canonical caller-stable delivery request identity — PRD-CORE-208 FR01.

Belongs to the ``tools/_delivery_operations.py`` facade. Re-exported there so
callers keep a single import point.

Pure, I/O-free, standard-library-only (NFR03): UUIDv7 validation with bounded
age/skew, the SHA-256 project-scope identifier derived from installation identity
(never an absolute checkout path, NFR02), the canonical JSON request digest that
binds every replay-relevant delivery flag, and the salted capability hash with
constant-time comparison (FR04 token secrecy). The digest reuses the CORE-205
``domain_digest`` primitive so a request digest can never collide with a
manifest/scope/plan/receipt-set digest even on identical byte payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from typing import Final

from trw_mcp.models._evidence_core import canonical_json, domain_digest

# --- Fixed v1 lifecycle + safety bounds (NFR04). These are hard ceilings, not
# tunables; the wave-tunable operational knobs (stale lease minutes, queue depth,
# busy timeout, mode) live in TRWConfig. ---


class DeliveryLimits:
    """Fixed v1 numeric bounds (PRD-CORE-208 §5 table). Documented, not magic."""

    UUID_VERSION: Final[int] = 7
    FUTURE_SKEW_MS: Final[int] = 5 * 60 * 1000  # 5 minutes
    ACCEPTANCE_HORIZON_MS: Final[int] = 180 * 24 * 60 * 60 * 1000  # 180 days
    TERMINAL_FULL_RETENTION_MS: Final[int] = 30 * 24 * 60 * 60 * 1000  # 30 days
    UNRESOLVED_FULL_RETENTION_MS: Final[int] = 90 * 24 * 60 * 60 * 1000  # 90 days
    TOMBSTONE_TTL_MS: Final[int] = ACCEPTANCE_HORIZON_MS  # 180 days
    STORE_MAX_BYTES: Final[int] = 64 * 1024 * 1024  # 64 MiB
    STORE_MAX_ROWS: Final[int] = 20_000
    QUEUE_DEPTH_MAX: Final[int] = 128
    MAX_RECORD_BYTES: Final[int] = 128 * 1024  # 128 KiB serialized payload
    MAX_REASON_CHARS: Final[int] = 500
    MAX_EVIDENCE_REF_CHARS: Final[int] = 1024
    MIN_CAPABILITY_BYTES: Final[int] = 16  # >= 128 bits of caller entropy
    CAPABILITY_SALT_BYTES: Final[int] = 16
    SCHEMA_VERSION: Final[int] = 1


class DeliveryRequestError(ValueError):
    """Raised when a delivery ID or request fails FR01 validation.

    ``code`` is a stable machine reason (never leaks token/secret material).
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CanonicalRequest:
    """The immutable, replay-relevant delivery request bound to one identifier."""

    schema_version: int
    project_scope: str
    run_identity: str
    skip_reflect: bool
    skip_index_sync: bool
    allow_unverified: bool
    acceptable_failure_digest: str

    def digest(self) -> str:
        """Domain-separated SHA-256 over the canonical request (FR01)."""
        return domain_digest("core208.request", self._canonical_obj())

    def _canonical_obj(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_scope": self.project_scope,
            "run_identity": self.run_identity,
            "skip_reflect": self.skip_reflect,
            "skip_index_sync": self.skip_index_sync,
            "allow_unverified": self.allow_unverified,
            "acceptable_failure_digest": self.acceptable_failure_digest,
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic UTF-8 canonical JSON, used for size-bound checks."""
        return canonical_json(self._canonical_obj())


def compute_project_scope(installation_identity: str) -> str:
    """SHA-256 project-scope namespace from installation identity, not a path.

    The caller passes a stable installation/project identity (e.g. the resolved
    project root *name*, never its absolute checkout path) so the operation key
    namespace is portable and does not leak a filesystem location (NFR02).
    """
    if not installation_identity:
        raise DeliveryRequestError("empty_project_identity", "installation identity must be non-empty")
    return hashlib.sha256(b"trw.core208.project_scope.v1\x00" + installation_identity.encode("utf-8")).hexdigest()


def _uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Extract the embedded 48-bit unix-millis timestamp from a UUIDv7."""
    return value.int >> 80


def validate_delivery_id(delivery_id: str, effective_now_ms: int) -> uuid.UUID:
    """Validate a caller UUIDv7 against FR01 parse/version/skew/age rules.

    ``effective_now_ms`` is the later of valid UTC wall time and the store's
    monotonic ``max_observed_utc_ms`` high-water — a backward wall-clock jump can
    never reopen an already-expired identifier (NFR04). Raises
    :class:`DeliveryRequestError` with a stable code before any store mutation.
    """
    if not delivery_id or "\x00" in delivery_id:
        raise DeliveryRequestError("malformed_id", "delivery_id is empty or contains a NUL byte")
    if "/" in delivery_id or "\\" in delivery_id or ".." in delivery_id:
        raise DeliveryRequestError("malformed_id", "delivery_id must be a bare UUID, not a path")
    try:
        parsed = uuid.UUID(delivery_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise DeliveryRequestError("malformed_id", "delivery_id is not a valid UUID") from exc
    # Reject a hyphen-free / brace / urn spelling that would not round-trip.
    if str(parsed) != delivery_id.lower():
        raise DeliveryRequestError("malformed_id", "delivery_id must be canonical 8-4-4-4-12 form")
    if parsed.version != DeliveryLimits.UUID_VERSION:
        raise DeliveryRequestError("wrong_uuid_version", "delivery_id must be a UUIDv7")
    ts_ms = _uuid7_timestamp_ms(parsed)
    if ts_ms - effective_now_ms > DeliveryLimits.FUTURE_SKEW_MS:
        raise DeliveryRequestError("future_skew", "delivery_id timestamp is more than 5 minutes in the future")
    if effective_now_ms - ts_ms > DeliveryLimits.ACCEPTANCE_HORIZON_MS:
        raise DeliveryRequestError("expired_id", "delivery_id timestamp is older than the 180-day horizon")
    return parsed


def validate_capability_strength(capability_token: str) -> None:
    """Reject a recovery capability below 128 bits of entropy (FR01), pre-claim."""
    if len(capability_token.encode("utf-8")) < DeliveryLimits.MIN_CAPABILITY_BYTES:
        raise DeliveryRequestError("weak_capability", "recovery capability needs >= 128 bits of entropy")


def _salted_hash(capability_token: str, salt_bytes: bytes) -> str:
    return hashlib.sha256(salt_bytes + capability_token.encode("utf-8")).hexdigest()


def hash_capability(capability_token: str, salt: bytes | None = None) -> tuple[str, str]:
    """Return ``(salt_hex, capability_hash)`` for a caller recovery capability.

    Only the salted SHA-256 hash is ever stored (FR01/NFR02). The token must
    carry at least 128 bits of caller entropy (enforced here at claim time). A
    fresh random salt is generated when not supplied.
    """
    validate_capability_strength(capability_token)
    salt_bytes = salt if salt is not None else os.urandom(DeliveryLimits.CAPABILITY_SALT_BYTES)
    return salt_bytes.hex(), _salted_hash(capability_token, salt_bytes)


def verify_capability(capability_token: str, salt_hex: str, expected_hash: str) -> bool:
    """Constant-time capability check (FR04); never logs or returns token material.

    Does NOT enforce the strength gate — a short or wrong candidate simply fails
    the constant-time comparison rather than raising.
    """
    try:
        salt_bytes = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    return hmac.compare_digest(_salted_hash(capability_token, salt_bytes), expected_hash)


def normalize_run_identity(run_identity: str | None) -> str:
    """Normalize a project-relative run identity; empty strings collapse to ''."""
    if not run_identity:
        return ""
    normalized = run_identity.replace("\\", "/").strip()
    if normalized.startswith("/") or ".." in normalized.split("/"):
        raise DeliveryRequestError("bad_run_identity", "run identity must be project-relative")
    return normalized


def build_canonical_request(
    *,
    project_scope: str,
    run_identity: str | None,
    skip_reflect: bool,
    skip_index_sync: bool,
    allow_unverified: bool,
    acceptable_failure_digest: str,
) -> CanonicalRequest:
    """Assemble the FR01 canonical request and enforce the serialized-size cap."""
    request = CanonicalRequest(
        schema_version=DeliveryLimits.SCHEMA_VERSION,
        project_scope=project_scope,
        run_identity=normalize_run_identity(run_identity),
        skip_reflect=skip_reflect,
        skip_index_sync=skip_index_sync,
        allow_unverified=allow_unverified,
        acceptable_failure_digest=acceptable_failure_digest or "",
    )
    if len(request.canonical_bytes()) > DeliveryLimits.MAX_RECORD_BYTES:
        raise DeliveryRequestError("oversize_request", "serialized request exceeds 128 KiB")
    return request
