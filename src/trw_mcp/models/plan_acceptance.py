"""PRD-CORE-249 — the typed per-identifier acceptance declaration.

A run declares, in ``{RUN_ROOT}/reports/acceptance.yaml``, one status token per
governing acceptance identifier. This module owns the schema and the token
parser; :mod:`trw_mcp.tools._plan_acceptance_gate` owns enumeration and the
block predicate, and :mod:`trw_mcp.tools._project_handoff` owns where accepted
blocked rows go to live.

The declared vocabulary is deliberately tiny — three shapes, no free-form
status::

    schema_version: 1
    run_id: 20260903T190257Z-1938c253
    gates:
      "P-1": "satisfied"
      "X-1": "blocked:human-only:ops@example - production credential rotation"
      "FR07": "unaddressed"

An enumerated identifier with **no entry** is ``unaddressed``. Omission is not a
way past the gate — that omission is the whole defect PRD-CORE-249 exists to
close, so an absent declaration is the strongest signal, not the weakest.

**Why the caps truncate here rather than at render time.** NFR03 requires the
bounds to be Pydantic ``Field(max_length=...)`` invariants and *also* requires an
over-long field to be truncated rather than to abort the run. Both hold because
the parser is the single truncation point: raw text is cut to the cap before the
model is constructed, so every :class:`AcceptanceStatus` in existence already
satisfies its own bound and no renderer ever slices. A renderer that sliced
would be the ad-hoc path NFR03 forbids.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Status kinds. ``blocked`` always carries a :data:`BLOCKING_CLASSES` class.
StatusKind = Literal["satisfied", "unaddressed", "blocked"]

#: Blocking classes, in the exact vocabulary the governing submission asks for.
#: ``automatable`` is work an agent could have done and is therefore NOT an
#: accepted blocker; the other two name a human or an operations dependency.
BlockingClass = Literal["", "automatable", "human-only", "ops-only"]

BLOCKING_CLASSES: Final[frozenset[str]] = frozenset({"automatable", "human-only", "ops-only"})

#: Classes whose declaration is ACCEPTED as a legitimate blocker and therefore
#: earns a durable handoff row (FR02). ``automatable`` is excluded on purpose.
ACCEPTED_BLOCKING_CLASSES: Final[frozenset[str]] = frozenset({"human-only", "ops-only"})

#: Field bounds (NFR03). ``MAX_REASON_CHARS`` matches
#: ``DeliveryLimits.MAX_REASON_CHARS`` so a declaration reason and an
#: acceptable-failure reason are bounded identically.
MAX_GATE_ID_CHARS: Final[int] = 64
MAX_OWNER_CHARS: Final[int] = 128
MAX_REASON_CHARS: Final[int] = 500

#: Separators accepted between ``blocked:<class>:<owner>`` and its free-text
#: reason. The em dash is the documented form; ``--`` exists because a human
#: editing YAML in a terminal often cannot type one.
#:
#: A single hyphen is deliberately NOT a separator. It occurs inside real owner
#: values (``ops - team@example``, ``sre-oncall - us-east``), so accepting it
#: split the owner at its first hyphen-space and filed the rest as the reason —
#: silently mis-attributing the person who holds the work, which is the ONE
#: field a handoff row exists to carry.
_REASON_SEPARATORS: Final[tuple[str, ...]] = (" — ", "—", " -- ")


class AcceptanceDeclarationError(ValueError):
    """Raised when a declaration file or status token cannot be understood.

    The gate is fail-CLOSED on this error (NFR02): a declaration that cannot be
    read is indistinguishable from one that was never written, and passing there
    would restore the exact defect this PRD closes.
    """


class AcceptanceStatus(BaseModel):
    """One identifier's declared status, parsed into typed fields."""

    model_config = ConfigDict(frozen=True)

    gate_id: str = Field(max_length=MAX_GATE_ID_CHARS)
    kind: StatusKind
    blocking_class: BlockingClass = ""
    owner: str = Field(default="", max_length=MAX_OWNER_CHARS)
    reason: str = Field(default="", max_length=MAX_REASON_CHARS)

    @property
    def is_unmet(self) -> bool:
        """True when this status must not be delivered over (FR04)."""
        return self.kind == "unaddressed" or (self.kind == "blocked" and self.blocking_class == "automatable")

    @property
    def is_accepted_blocked(self) -> bool:
        """True when this status earns a durable handoff row (FR02)."""
        return self.kind == "blocked" and self.blocking_class in ACCEPTED_BLOCKING_CLASSES

    @property
    def label(self) -> str:
        """Canonical token for messages — never the raw declaration text."""
        if self.kind != "blocked":
            return self.kind
        return f"blocked:{self.blocking_class}"


def _split_reason(remainder: str) -> tuple[str, str]:
    """Split ``<owner> - <reason>`` on the first accepted separator."""
    for separator in _REASON_SEPARATORS:
        owner, found, reason = remainder.partition(separator)
        if found:
            return owner.strip(), reason.strip()
    return remainder.strip(), ""


def parse_status_token(gate_id: str, token: object) -> AcceptanceStatus:
    """Parse one declaration entry into a typed :class:`AcceptanceStatus`.

    Accepts ``satisfied``, ``unaddressed``, and
    ``blocked:<class>[:<owner>][ - <reason>]``. Any other token — including an
    unknown blocking class, a non-string value, or a bare ``blocked`` — raises
    :class:`AcceptanceDeclarationError`, because a status the machine cannot
    classify must not silently count as satisfied.
    """
    if not isinstance(token, str):
        raise AcceptanceDeclarationError(f"{gate_id}: status must be a string, got {type(token).__name__}")
    text = token.strip()
    gate = gate_id.strip()[:MAX_GATE_ID_CHARS]
    if text in {"satisfied", "unaddressed"}:
        # A Literal narrowing mypy cannot infer from the ``in`` test alone.
        kind: StatusKind = "satisfied" if text == "satisfied" else "unaddressed"
        return AcceptanceStatus(gate_id=gate, kind=kind)
    if not text.startswith("blocked"):
        raise AcceptanceDeclarationError(
            f"{gate}: unknown status {text!r}; expected 'satisfied', 'unaddressed', or 'blocked:<class>:<owner>'"
        )
    parts = text.split(":", 2)
    blocking_class = parts[1].strip() if len(parts) > 1 else ""
    if blocking_class not in BLOCKING_CLASSES:
        raise AcceptanceDeclarationError(
            f"{gate}: unknown blocking class {blocking_class!r}; expected one of {sorted(BLOCKING_CLASSES)}"
        )
    owner, reason = _split_reason(parts[2]) if len(parts) > 2 else ("", "")
    return AcceptanceStatus(
        gate_id=gate,
        kind="blocked",
        blocking_class=blocking_class,  # type: ignore[arg-type]
        owner=owner[:MAX_OWNER_CHARS],
        reason=reason[:MAX_REASON_CHARS],
    )


def parse_declaration(raw: object) -> dict[str, AcceptanceStatus]:
    """Parse a loaded ``reports/acceptance.yaml`` document into statuses.

    ``raw`` is whatever ``yaml.safe_load`` produced. An empty document is an
    empty declaration (every enumerated identifier is then ``unaddressed``); a
    document that is not a mapping, or whose ``gates`` is not a mapping, raises
    :class:`AcceptanceDeclarationError` so the fail-closed gate can name it.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AcceptanceDeclarationError(f"declaration root must be a mapping, got {type(raw).__name__}")
    gates = raw.get("gates", {})
    if gates is None:
        return {}
    if not isinstance(gates, dict):
        raise AcceptanceDeclarationError(f"'gates' must be a mapping, got {type(gates).__name__}")
    return {str(key).strip(): parse_status_token(str(key), value) for key, value in gates.items()}


__all__ = [
    "ACCEPTED_BLOCKING_CLASSES",
    "BLOCKING_CLASSES",
    "MAX_GATE_ID_CHARS",
    "MAX_OWNER_CHARS",
    "MAX_REASON_CHARS",
    "AcceptanceDeclarationError",
    "AcceptanceStatus",
    "BlockingClass",
    "StatusKind",
    "parse_declaration",
    "parse_status_token",
]
