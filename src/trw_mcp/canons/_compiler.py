"""Deterministic, fail-closed canon compiler (PRD-CORE-207 FR02/FR03/FR04).

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

One marked authoring source compiles into three deterministic views:

* ``combined`` -- the backward-compatible legacy document. It is the authoring
  source with every marker line and every ``core_stub`` span body removed, so it
  is byte-identical to the frozen baseline (``FRAMEWORK.md`` / ``AARE-F-FRAMEWORK.md``
  consumers keep reading the same bytes during the compatibility window -- FR04).
* ``core`` -- the compact normative core: spans destined ``core``/``both`` plus
  the ``core_stub`` pointer spans that link to reference detail (FR03).
* ``reference`` -- explanations, matrices, and examples: spans destined
  ``reference``/``both`` (FR04).

STRICT STANDARD LIBRARY ONLY (NFR02): dataclasses, enum, hashlib, re. No
third-party imports, no timestamps, no host paths, no locale-dependent ordering
-- identical source bytes always yield identical output bytes and digests.

Fail-closed (FR02): a malformed marker, duplicate id, unknown enum value, span
before the first marker, or a normative obligation routed reference-only raises
:class:`CanonRegistryError` BEFORE any output is produced. The compiler never
writes a file; ``scripts/compile-framework-canons.py`` owns I/O and atomic replace.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum

from trw_mcp.canons._errors import CanonErrorCode, CanonRegistryError

COMPILER_SCHEMA_VERSION = 1

# Line-anchored whole-line marker match (never substring -- see trw-mcp rules).
_MARKER_RE = re.compile(
    r"^<!--\s*trw:span\s+id=(?P<id>[A-Za-z0-9._-]+)\s+"
    r"dest=(?P<dest>[a-z_]+)\s+class=(?P<cls>[a-z_]+)\s*-->$"
)
# Any line that looks like a trw:span marker but is malformed (fail-closed).
_MARKER_SHAPE_RE = re.compile(r"^<!--\s*trw:span\b")


class SpanDest(str, Enum):
    """Where a span's body is rendered."""

    CORE = "core"
    REFERENCE = "reference"
    BOTH = "both"
    CORE_STUB = "core_stub"


class ObligationClass(str, Enum):
    """Classification of a source span (FR01 vocabulary)."""

    NORMATIVE = "normative"
    DEFINITION = "definition"
    RATIONALE = "rationale"
    EXAMPLE = "example"
    REFERENCE = "reference"
    COMPATIBILITY = "compatibility"


# Classes that MUST reach the compact core (self-sufficiency, FR03/NFR01).
_CORE_RESIDENT_CLASSES = frozenset(
    {ObligationClass.NORMATIVE, ObligationClass.DEFINITION, ObligationClass.COMPATIBILITY}
)
_CORE_DESTS = frozenset({SpanDest.CORE, SpanDest.BOTH, SpanDest.CORE_STUB})


@dataclass(frozen=True)
class Span:
    """One classified, contiguous span of authoring source."""

    id: str
    dest: SpanDest
    cls: ObligationClass
    body: tuple[str, ...]
    start_line: int  # 1-based line of the marker

    @property
    def nonblank(self) -> tuple[str, ...]:
        return tuple(line for line in self.body if line.strip())


@dataclass(frozen=True)
class CompileResult:
    """Frozen deterministic compilation of one canon."""

    canon_id: str
    combined: str
    core: str
    reference: str
    spans: tuple[Span, ...]
    inventory: dict[str, object]


def _fail(code: CanonErrorCode, message: str) -> CanonRegistryError:
    return CanonRegistryError(code, message)


def parse_source(canon_id: str, text: str) -> tuple[Span, ...]:
    """Split marked authoring ``text`` into validated spans (fail-closed).

    ``text`` is normalized to ``\\n`` line endings. Every non-marker line belongs
    to the most recent preceding marker; content before the first marker, a
    malformed marker, or a duplicate id fails closed.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    # A trailing newline yields a final empty element; keep it so joins round-trip.
    spans: list[Span] = []
    seen: set[str] = set()
    current: dict[str, object] | None = None
    body: list[str] = []
    pre_marker_content = False

    def _flush() -> None:
        if current is not None:
            spans.append(
                Span(
                    id=str(current["id"]),
                    dest=SpanDest(str(current["dest"])),
                    cls=ObligationClass(str(current["cls"])),
                    body=tuple(body),
                    start_line=int(str(current["line"])),
                )
            )

    for index, line in enumerate(lines, start=1):
        marker = _MARKER_RE.match(line)
        if marker:
            _flush()
            body = []
            span_id = marker.group("id")
            if span_id in seen:
                raise _fail(CanonErrorCode.DUPLICATE_ID, f"{canon_id}: duplicate span id: {span_id}")
            seen.add(span_id)
            dest_raw = marker.group("dest")
            cls_raw = marker.group("cls")
            try:
                SpanDest(dest_raw)
            except ValueError as exc:
                raise _fail(
                    CanonErrorCode.MALFORMED_VALUE, f"{canon_id}: span {span_id} unknown dest: {dest_raw}"
                ) from exc
            try:
                ObligationClass(cls_raw)
            except ValueError as exc:
                raise _fail(
                    CanonErrorCode.MALFORMED_VALUE, f"{canon_id}: span {span_id} unknown class: {cls_raw}"
                ) from exc
            current = {"id": span_id, "dest": dest_raw, "cls": cls_raw, "line": index}
            continue
        if _MARKER_SHAPE_RE.match(line):
            raise _fail(
                CanonErrorCode.MALFORMED_VALUE, f"{canon_id}: malformed trw:span marker at line {index}: {line!r}"
            )
        if current is None:
            if line.strip():
                pre_marker_content = True
            body.append(line)
            continue
        body.append(line)

    if pre_marker_content:
        raise _fail(
            CanonErrorCode.MALFORMED_VALUE,
            f"{canon_id}: non-blank content precedes the first trw:span marker",
        )
    if current is None:
        raise _fail(CanonErrorCode.MISSING_FIELD, f"{canon_id}: source has no trw:span markers")
    _flush()
    return tuple(spans)


def _validate_self_sufficiency(canon_id: str, spans: tuple[Span, ...]) -> None:
    """FR03: no core-resident obligation may be routed reference-only."""
    for span in spans:
        if span.cls in _CORE_RESIDENT_CLASSES and span.dest is SpanDest.REFERENCE:
            raise _fail(
                CanonErrorCode.MALFORMED_VALUE,
                f"{canon_id}: {span.cls.value} span {span.id} is reference-only; "
                "core-resident obligations MUST be dest=core or both",
            )


def _join(bodies: list[tuple[str, ...]]) -> str:
    """Join span bodies back into a document preserving exact line structure."""
    lines: list[str] = []
    for body in bodies:
        lines.extend(body)
    return "\n".join(lines)


def render_combined(spans: tuple[Span, ...]) -> str:
    """Backward-compatible legacy document: everything except ``core_stub`` bodies."""
    return _join([s.body for s in spans if s.dest is not SpanDest.CORE_STUB])


def render_core(spans: tuple[Span, ...]) -> str:
    """Compact normative core: core/both spans plus core_stub pointers."""
    return _join([s.body for s in spans if s.dest in _CORE_DESTS])


def render_reference(spans: tuple[Span, ...]) -> str:
    """Reference document: reference/both spans."""
    return _join([s.body for s in spans if s.dest in (SpanDest.REFERENCE, SpanDest.BOTH)])


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_inventory(
    canon_id: str, spans: tuple[Span, ...], combined: str, core: str, reference: str
) -> dict[str, object]:
    """Machine-readable obligation inventory with source + output digests (FR01).

    Deterministic: sorted structure, no timestamps. Records every span's stable
    id, class, destination, line range, and body digest, plus the combined/core/
    reference output digests so parity checks bind source to generated bytes.
    """
    obligations: list[dict[str, object]] = []
    cursor = 1
    for span in spans:
        line_count = len(span.body)
        obligations.append(
            {
                "id": span.id,
                "class": span.cls.value,
                "dest": span.dest.value,
                "start_line": span.start_line,
                "body_lines": line_count,
                "nonblank_lines": len(span.nonblank),
                "digest": _sha256("\n".join(span.body)),
            }
        )
        cursor += line_count
    return {
        "schema": COMPILER_SCHEMA_VERSION,
        "canon_id": canon_id,
        "span_count": len(spans),
        "classes": _class_counts(spans),
        "source_digest_combined": _sha256(combined),
        "core_digest": _sha256(core),
        "reference_digest": _sha256(reference),
        "obligations": obligations,
    }


def _class_counts(spans: tuple[Span, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for span in spans:
        counts[span.cls.value] = counts.get(span.cls.value, 0) + 1
    return dict(sorted(counts.items()))


def provenance_footer(source_basename: str) -> str:
    """Deterministic 'do not edit' banner naming the source + regen command (FR04).

    No timestamp, username, or host path -- identical inputs yield identical bytes.
    """
    return (
        f"\n\n<!-- GENERATED FILE -- do not edit. Source: {source_basename}. "
        "Regenerate: python3 scripts/compile-framework-canons.py --write. "
        f"compiler_schema={COMPILER_SCHEMA_VERSION}. -->\n"
    )


def compile_canon(canon_id: str, source_text: str, *, source_basename: str | None = None) -> CompileResult:
    """Compile one marked authoring source into all three deterministic views.

    Fail-closed on any malformed marker, duplicate id, unknown enum, pre-marker
    content, or reference-only normative obligation. Pure and deterministic.

    ``combined`` is byte-identical to the frozen baseline (no footer); ``core``
    and ``reference`` carry a deterministic provenance footer (FR04) when
    ``source_basename`` is given.
    """
    spans = parse_source(canon_id, source_text)
    _validate_self_sufficiency(canon_id, spans)
    combined = render_combined(spans)
    footer = provenance_footer(source_basename) if source_basename else ""
    core = render_core(spans) + footer
    reference = render_reference(spans) + footer
    inventory = build_inventory(canon_id, spans, combined, core, reference)
    return CompileResult(
        canon_id=canon_id,
        combined=combined,
        core=core,
        reference=reference,
        spans=spans,
        inventory=inventory,
    )


def core_byte_ratio(result: CompileResult, baseline_bytes: int) -> float:
    """Compact-core bytes as a fraction of the frozen combined baseline (NFR04)."""
    if baseline_bytes <= 0:
        raise _fail(CanonErrorCode.MALFORMED_VALUE, f"{result.canon_id}: baseline byte count must be positive")
    return len(result.core.encode("utf-8")) / baseline_bytes


__all__ = [
    "COMPILER_SCHEMA_VERSION",
    "CompileResult",
    "ObligationClass",
    "Span",
    "SpanDest",
    "build_inventory",
    "compile_canon",
    "core_byte_ratio",
    "parse_source",
    "provenance_footer",
    "render_combined",
    "render_core",
    "render_reference",
]
