"""Typed tool-result envelope — PRD-CORE-215 FR02.

One strict, mostly-frozen Pydantic v2 schema every ceremony operation projects
into. It separates transport state from operation state and carries the
retry-safety, redaction, output-budget, truncation, and bounded-diagnostic
evidence a stdio caller needs to retry safely without duplicating effects.

Authority boundary: the envelope owns NO operation state. It is a lossless
projection surface. Contradictory legacy result keys can never override the
typed ``outcome``:

* direct construction forbids unknown keys (``extra="forbid"``), so a legacy
  dict cannot smuggle an ``outcome``/``status``/``success`` key past the schema;
* :meth:`ToolResultEnvelope.from_legacy` accepts a legacy mapping explicitly,
  records every field that *contradicts* the typed outcome in
  ``legacy_conflicts``, and never lets it change ``outcome``.

Secret-looking diagnostic keys (matching ``password``/``token``/``secret``/
``api_key`` and friends) are redacted by a model serializer, so a serialized
envelope never leaks a credential regardless of how it was populated.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)

__all__ = [
    "CeremonyExecutionClass",
    "CompatibilityException",
    "Outcome",
    "OutputEstimateUnit",
    "RedactionState",
    "RetrySafety",
    "ToolResultEnvelope",
    "TruncationState",
    "compatibility_permits_projection",
    "redact_mapping",
]


# --- Closed vocabularies ---


class Outcome(str, Enum):
    """The four terminal-or-in-flight outcomes a caller must distinguish."""

    COMPLETED = "completed"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"


class RetrySafety(str, Enum):
    """Whether re-issuing the exact same request is duplicate-safe."""

    SAFE_EXACT_RETRY = "safe_exact_retry"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class RedactionState(str, Enum):
    """Whether any field in this envelope was redacted before serialization."""

    NONE = "none"
    REDACTED = "redacted"


class TruncationState(str, Enum):
    """Whether output was cut, and why."""

    NONE = "none"
    TRUNCATED = "truncated"
    HARD_BUDGET_STOPPED = "hard_budget_stopped"


class OutputEstimateUnit(str, Enum):
    """Unit of ``output_estimate`` — the two the caller may interpret."""

    CHARS = "chars"
    TOKENS = "tokens"


class CeremonyExecutionClass(str, Enum):
    """How a tool completes: only ``operation_backed`` may return a handle."""

    SYNCHRONOUS_ONLY = "synchronous_only"
    SYNCHRONOUS_BOUNDED = "synchronous_bounded"
    OPERATION_BACKED = "operation_backed"


# --- Documented bounds (not magic numbers) ---

REDACTED_PLACEHOLDER: Final[str] = "***REDACTED***"
MAX_DIAGNOSTIC_ENTRIES: Final[int] = 32
MAX_DIAGNOSTIC_VALUE_CHARS: Final[int] = 2048
MAX_REASON_CODE_CHARS: Final[int] = 128
MAX_HINT_CHARS: Final[int] = 1024

#: Legacy keys whose value asserts an outcome — checked for contradiction.
_LEGACY_OUTCOME_KEYS: Final[tuple[str, ...]] = ("outcome", "status", "success", "result")

#: Substrings that mark a diagnostic key as secret-bearing (NFR03 alignment).
_SECRET_KEY_PATTERNS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "token",
        "secret",
        "api_key",
        "apikey",
        "credential",
        "authorization",
        "private_key",
        "access_key",
    }
)

_POSITIVE_OUTCOMES: Final[frozenset[Outcome]] = frozenset({Outcome.COMPLETED, Outcome.ACCEPTED})
_POSITIVE_TOKENS: Final[frozenset[str]] = frozenset({"success", "succeeded", "ok", "done", "true", "pass"})
_NEGATIVE_TOKENS: Final[frozenset[str]] = frozenset({"error", "failed", "failure", "conflict", "false", "reject"})
_OUTCOME_VALUES: Final[frozenset[str]] = frozenset(o.value for o in Outcome)


def _is_secret_key(key: str) -> bool:
    # Match separator-insensitively so header-style keys ("X-Api-Key") and
    # snake_case keys ("api_key") are both caught. Patterns are normalized the
    # same way so "api_key" collapses to "apikey" and matches either form.
    normalized = "".join(ch for ch in key.lower() if ch.isalnum())
    return any(pattern.replace("_", "") in normalized for pattern in _SECRET_KEY_PATTERNS)


def redact_mapping(data: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``data`` with secret-looking keys' values redacted."""
    return {k: (REDACTED_PLACEHOLDER if _is_secret_key(k) else v) for k, v in data.items()}


def _asserted_outcome(value: object) -> tuple[str | None, Outcome | None]:
    """Classify a legacy value as (polarity, exact-outcome).

    ``polarity`` is ``"positive"``/``"negative"``/``None`` (not outcome-bearing).
    ``exact`` is the specific :class:`Outcome` only when the value names one.
    """
    if isinstance(value, bool):
        return ("positive" if value else "negative", None)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _OUTCOME_VALUES:
            exact = Outcome(token)
            return ("positive" if exact in _POSITIVE_OUTCOMES else "negative", exact)
        if token in _POSITIVE_TOKENS:
            return ("positive", None)
        if token in _NEGATIVE_TOKENS:
            return ("negative", None)
    return (None, None)


def _detect_outcome_conflicts(typed: Outcome, legacy: Mapping[str, object]) -> tuple[str, ...]:
    """Return the legacy keys whose asserted outcome contradicts ``typed``."""
    typed_polarity = "positive" if typed in _POSITIVE_OUTCOMES else "negative"
    conflicts: list[str] = []
    for key in _LEGACY_OUTCOME_KEYS:
        if key not in legacy:
            continue
        polarity, exact = _asserted_outcome(legacy[key])
        if polarity is None:
            continue
        # Conflict when the legacy field names a different exact outcome, or when
        # its polarity (positive/negative) contradicts the typed outcome.
        if (exact is not None and exact is not typed) or polarity != typed_polarity:
            conflicts.append(key)
    return tuple(conflicts)


class CompatibilityException(BaseModel):
    """A typed, EXPIRING approval for a legacy result projector (NFR04).

    NFR04: "A legacy result projector shall exist if and only if an approved named
    external-caller CompatibilityException is active." A projector may keep a
    conflicting legacy outcome ONLY while an exception like this is *active*.
    Fields are intentionally allowed to be empty at construction so an
    *incomplete* record can be represented and then REFUSED by
    :func:`compatibility_permits_projection` — expired, incomplete, and
    internal-only (no named external caller) records all fail the check, so none
    can rescue a failing decision.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    #: The named EXTERNAL caller this exception exists for. Empty => internal-only,
    #: which is refused (a repository caller migrates instead of getting a window).
    external_caller: str = ""
    #: Evidence that removing the projector breaks that caller.
    breakage_evidence: str = ""
    #: Owner accountable for completing the migration before expiry.
    migration_owner: str = ""
    #: ISO-8601 date or datetime after which the projector must NOT be used.
    expiry_iso: str = ""
    #: The telemetry field emitted whenever this projector is exercised.
    telemetry_field: str = ""
    #: Reference to the test that proves the projector is removed on expiry.
    removal_test_ref: str = ""


def _parse_expiry(expiry_iso: str) -> datetime | None:
    """Parse an ISO-8601 date/datetime into a tz-aware UTC instant, else None."""
    raw = expiry_iso.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compatibility_permits_projection(exc: CompatibilityException, *, now: datetime | None = None) -> bool:
    """Return True only when a COMPLETE, named-external, UNEXPIRED exception is active.

    Refuses (returns False) any incomplete record (a required field is empty),
    an internal-only record (no named external caller), or an expired/malformed
    expiry — so "expired, incomplete, or internal-only compatibility shall not
    rescue a failing decision" (NFR04) is enforced in one place.
    """
    if not (
        exc.external_caller
        and exc.breakage_evidence
        and exc.migration_owner
        and exc.expiry_iso
        and exc.telemetry_field
        and exc.removal_test_ref
    ):
        return False
    expiry = _parse_expiry(exc.expiry_iso)
    if expiry is None:
        return False
    reference = now if now is not None else datetime.now(tz=timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference < expiry


class ToolResultEnvelope(BaseModel):
    """One typed result surface for every ceremony operation (FR02).

    ``strict``/``frozen``/``extra="forbid"``: the outcome vocabulary is closed
    and no unknown legacy key can be attached at construction time. Use
    :meth:`from_legacy` to fold a legacy dict in without letting it override the
    typed outcome.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    outcome: Outcome
    reason_code: str = Field(default="", max_length=MAX_REASON_CODE_CHARS)
    operation_id: str = ""
    request_id: str = ""
    input_digest: str = ""
    execution_class: CeremonyExecutionClass = CeremonyExecutionClass.SYNCHRONOUS_ONLY
    receipt_refs: tuple[str, ...] = ()
    retry_safety: RetrySafety = RetrySafety.UNKNOWN
    redaction_state: RedactionState = RedactionState.NONE
    output_estimate: int = Field(default=0, ge=0)
    output_estimate_unit: OutputEstimateUnit = OutputEstimateUnit.CHARS
    truncation_state: TruncationState = TruncationState.NONE
    omitted_sections: tuple[str, ...] = ()
    hard_budget_stop_reason: str = Field(default="", max_length=MAX_REASON_CODE_CHARS)
    safe_reproduction_hint: str = Field(default="", max_length=MAX_HINT_CHARS)
    diagnostics: dict[str, str] = Field(default_factory=dict)
    legacy_conflicts: tuple[str, ...] = ()

    @field_validator("diagnostics")
    @classmethod
    def _bound_diagnostics(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_DIAGNOSTIC_ENTRIES:
            raise ValueError(f"diagnostics exceeds {MAX_DIAGNOSTIC_ENTRIES} entries")
        for key, entry in value.items():
            if len(entry) > MAX_DIAGNOSTIC_VALUE_CHARS:
                raise ValueError(f"diagnostics['{key}'] exceeds {MAX_DIAGNOSTIC_VALUE_CHARS} chars")
        return value

    @model_serializer(mode="wrap")
    def _redact_on_dump(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Serialize once, redacting any secret-looking diagnostic keys (NFR03)."""
        data: dict[str, Any] = handler(self)
        diagnostics = data.get("diagnostics")
        if isinstance(diagnostics, dict):
            data["diagnostics"] = redact_mapping(diagnostics)
        return data

    @classmethod
    def from_legacy(
        cls,
        *,
        outcome: Outcome,
        legacy: Mapping[str, object],
        compatibility: CompatibilityException | None = None,
        now: datetime | None = None,
        **fields: object,
    ) -> ToolResultEnvelope:
        """Build an envelope from a typed ``outcome`` plus a legacy result dict.

        Conflicting legacy outcome keys are recorded in ``legacy_conflicts`` and
        folded (redacted) into diagnostics for auditability — they never change
        ``outcome``. If any recorded diagnostic key is secret-bearing, the
        ``redaction_state`` is forced to ``redacted``.

        NFR04 compatibility gate: ``compatibility`` is the ONLY way to run this as
        a "legacy result projector" that keeps a *conflicting* legacy outcome.
        When conflicts exist and a projector is requested, the outcome is kept
        ONLY while :func:`compatibility_permits_projection` approves the record
        (complete, named-external, unexpired). An expired/incomplete/internal-only
        record cannot rescue the decision — the outcome degrades to
        :attr:`Outcome.UNCERTAIN` and the refusal is recorded. When
        ``compatibility is None`` no projector is applied: the typed outcome stays
        authoritative (a positive legacy flag still cannot flip a negative typed
        outcome — that is the default FR02 behavior).
        """
        conflicts = _detect_outcome_conflicts(outcome, legacy)
        raw_diag = fields.pop("diagnostics", {})
        diag_source: Mapping[object, object] = raw_diag if isinstance(raw_diag, Mapping) else {}
        diagnostics = {str(k): str(v) for k, v in diag_source.items()}
        for key in conflicts:
            diagnostics[f"legacy.{key}"] = str(legacy[key])

        effective_outcome = outcome
        if conflicts and compatibility is not None:
            if compatibility_permits_projection(compatibility, now=now):
                # Active exception: documented legacy semantics kept until expiry.
                diagnostics["compatibility.external_caller"] = compatibility.external_caller
                diagnostics["compatibility.telemetry_field"] = compatibility.telemetry_field
                diagnostics["compatibility.expiry_iso"] = compatibility.expiry_iso
            else:
                # Expired / incomplete / internal-only cannot rescue a decision.
                effective_outcome = Outcome.UNCERTAIN
                diagnostics["compatibility.refused"] = "expired_incomplete_or_internal_only"

        secret_present = any(_is_secret_key(k) for k in diagnostics)
        redaction_state = fields.pop("redaction_state", RedactionState.NONE)
        if secret_present:
            redaction_state = RedactionState.REDACTED
        payload: dict[str, object] = dict(fields)
        payload.update(
            outcome=effective_outcome,
            legacy_conflicts=conflicts,
            diagnostics=diagnostics,
            redaction_state=redaction_state,
        )
        return cls.model_validate(payload)
