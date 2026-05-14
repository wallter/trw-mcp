"""Substrate-First gate for PRD validation (PRD-DIST-218 FR-2).

Heuristic-but-deterministic check that flags PRDs proposing module-level
hardcoded vocabulary collections without an acknowledged justification.
Surfaced inside ``trw_prd_validate`` as ``substrate_first.{...}``.

Verdict semantics:
  - **PASS** — no flagged collections, OR (acknowledged + evidence
    section + sign-off comment present).
  - **WARN** — flagged collection exists, frontmatter sets
    ``hand_curation_acknowledged: true``, but no operator sign-off yet.
  - **FAIL** — flagged collection exists with no acknowledgment.

The check inspects the PRD itself (frontmatter + body) plus any Python
diffs the PRD inlines. Pure-function design keeps it trivially testable
and keeps the validator entrypoint short.

NFR-1: O(text size) — single regex scan of PRD body. Fits the 200 ms
per-PRD budget by a large margin.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal

# Module-level literal collection assignment header. Matches:
#   FOO = frozenset({...})
#   _BAR = ("a", "b", ...)
#   BAZ: dict[str, str] = {"a": "1", "b": "2", ...}
# We capture only the assignment header + the opening bracket; the
# body span is then found by a balanced-bracket scan so multi-line
# literals (including nested calls like ``frozenset({...})``) work.
_ASSIGNMENT_HEADER_PATTERN = re.compile(
    r"""
    ^
    [ \t]*                                 # module-level (no leading non-ws)
    (?P<name>_?[A-Z][A-Z0-9_]+)
    [ \t]*
    (?::\s*[^=\n]+)?                       # optional type annotation
    [ \t]*=[ \t]*
    (?P<kind>frozenset|tuple|set|dict|list)?
    [ \t]*
    (?P<open>[\(\[\{])
    """,
    re.MULTILINE | re.VERBOSE,
)
_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}

# Match Python code-fence blocks. Allow ``` python ``` or ``` py ```.
_PY_FENCE_PATTERN = re.compile(
    r"^```(?:python|py)\b\s*\n(?P<body>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)

ACK_FRONTMATTER_PATTERN = re.compile(r"^hand_curation_acknowledged:\s*true\b", re.MULTILINE | re.IGNORECASE)

EVIDENCE_SECTION_PATTERN = re.compile(r"^##\s+Substrate-First evidence\b", re.MULTILINE)

SIGN_OFF_PATTERN = re.compile(
    r"<!--\s*substrate_first_sign_off:\s*[^\s>][^>]*-->",
    re.IGNORECASE,
)

ENV_DISABLE = "TRW_SUBSTRATE_FIRST_GATE"

# Names that are protocol-internal and explicitly OK to enumerate.
# The PRD calls these out (§3 Non-Goals): protocol invariants and
# small enums. The minimum-size gate (>5 entries) already filters most
# small enums, but these names are excused even when large because
# they're not external vocabulary tracking.
_PROTOCOL_INTERNAL_PREFIXES = ("_TRW_INTERNAL", "_ALWAYS_SKIP")

Verdict = Literal["pass", "warn", "fail", "disabled"]


@dataclass(frozen=True)
class FlaggedCollection:
    """A module-level literal collection with > threshold entries."""

    name: str
    kind: str
    entry_count: int
    line_hint: str  # truncated body for operator inspection


@dataclass(frozen=True)
class SubstrateFirstResult:
    verdict: Verdict
    flagged_collections: list[FlaggedCollection] = field(default_factory=list)
    evidence_section_present: bool = False
    sign_off_present: bool = False
    acknowledged: bool = False
    diagnostic: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "flagged_collections": [
                {
                    "name": fc.name,
                    "kind": fc.kind,
                    "entry_count": fc.entry_count,
                    "line_hint": fc.line_hint,
                }
                for fc in self.flagged_collections
            ],
            "evidence_section_present": self.evidence_section_present,
            "sign_off_present": self.sign_off_present,
            "acknowledged": self.acknowledged,
            "diagnostic": self.diagnostic,
        }


def _count_top_level_entries(body: str) -> int:
    """Count comma-separated entries in a (possibly nested) literal body.

    Increments per comma at depth 0, but only if some non-whitespace
    content was seen since the prior comma. Handles trailing commas
    (PEP 8 style) and skips strings so commas inside them don't count.
    """
    depth = 0
    entries = 0
    saw_content_since_last_comma = False
    in_str: str | None = None
    i = 0
    while i < len(body):
        ch = body[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            saw_content_since_last_comma = True
        else:
            if ch in ("'", '"'):
                in_str = ch
                saw_content_since_last_comma = True
            elif ch in "([{":
                depth += 1
                saw_content_since_last_comma = True
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                if saw_content_since_last_comma:
                    entries += 1
                saw_content_since_last_comma = False
            elif not ch.isspace():
                saw_content_since_last_comma = True
        i += 1
    if saw_content_since_last_comma:
        entries += 1
    return entries


def _scan_balanced_body(text: str, start: int, open_ch: str) -> tuple[str, int] | None:
    """Return the (body, end_index) span starting at the opener at ``start``.

    Tracks string literals so close brackets inside strings don't terminate.
    Returns None on unbalanced input — gate falls through, no false flag.
    """
    close_ch = _OPEN_TO_CLOSE[open_ch]
    depth = 1
    in_str: str | None = None
    i = start + 1
    body_start = i
    while i < len(text) and depth > 0:
        ch = text[i]
        if in_str is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ("'", '"'):
                in_str = ch
            elif ch in ("(", "[", "{"):
                # Nested open of any kind — track to keep balance honest.
                depth += 1
            elif ch in (")", "]", "}"):
                if ch == close_ch:
                    depth -= 1
                else:
                    depth -= 1  # tolerate cross-bracket nesting
        i += 1
    if depth != 0:
        return None
    return (text[body_start : i - 1], i)


def _scan_python_block(text: str, *, threshold: int) -> list[FlaggedCollection]:
    flagged: list[FlaggedCollection] = []
    for match in _ASSIGNMENT_HEADER_PATTERN.finditer(text):
        name = match.group("name")
        if name.startswith(_PROTOCOL_INTERNAL_PREFIXES):
            continue
        open_ch = match.group("open")
        kind = match.group("kind") or _kind_from_open(open_ch)
        scan = _scan_balanced_body(text, match.end() - 1, open_ch)
        if scan is None:
            continue
        body, _ = scan
        # If the kind is a callable like ``frozenset(...)`` whose body
        # is a literal collection (``{...}``), recurse into it: the
        # entries we care about are inside the inner brace.
        inner = body.strip()
        if kind in {"frozenset", "tuple", "set", "list", "dict"} and inner.startswith(("{", "[", "(")):
            inner_open = inner[0]
            inner_scan = _scan_balanced_body(inner, 0, inner_open)
            if inner_scan is not None:
                body = inner_scan[0]
        entry_count = _count_top_level_entries(body)
        if entry_count > threshold:
            flagged.append(
                FlaggedCollection(
                    name=name,
                    kind=kind,
                    entry_count=entry_count,
                    line_hint=(body[:80] + "...") if len(body) > 80 else body,
                )
            )
    return flagged


def _kind_from_open(open_ch: str) -> str:
    return {"(": "tuple", "[": "list", "{": "set_or_dict"}.get(open_ch, "unknown")


def substrate_first_check(
    prd_content: str,
    *,
    threshold: int = 5,
    extra_python_sources: list[str] | None = None,
) -> SubstrateFirstResult:
    """Evaluate a PRD against the Substrate-First gate.

    Inputs:
      prd_content: full PRD markdown (frontmatter + body).
      threshold: minimum entry-count to flag a collection (default 5
        per FR-2).
      extra_python_sources: optional already-loaded Python sources
        (e.g. fetched via ``git diff --name-only main..`` and read
        from disk). Each is scanned the same way as inline fences.

    Returns a SubstrateFirstResult; never raises on malformed input.
    """
    if os.environ.get(ENV_DISABLE, "1") == "0":
        return SubstrateFirstResult(
            verdict="disabled",
            diagnostic=f"{ENV_DISABLE}=0; gate skipped",
        )
    flagged: list[FlaggedCollection] = []
    for fence in _PY_FENCE_PATTERN.finditer(prd_content):
        flagged.extend(_scan_python_block(fence.group("body"), threshold=threshold))
    for source in extra_python_sources or ():
        flagged.extend(_scan_python_block(source, threshold=threshold))

    acknowledged = bool(ACK_FRONTMATTER_PATTERN.search(prd_content))
    evidence = bool(EVIDENCE_SECTION_PATTERN.search(prd_content))
    sign_off = bool(SIGN_OFF_PATTERN.search(prd_content))

    if not flagged:
        return SubstrateFirstResult(
            verdict="pass",
            flagged_collections=[],
            evidence_section_present=evidence,
            sign_off_present=sign_off,
            acknowledged=acknowledged,
            diagnostic="no flagged collections",
        )
    if acknowledged and evidence and sign_off:
        return SubstrateFirstResult(
            verdict="pass",
            flagged_collections=flagged,
            evidence_section_present=evidence,
            sign_off_present=sign_off,
            acknowledged=True,
            diagnostic="acknowledged + evidence + operator sign-off",
        )
    if acknowledged and evidence:
        return SubstrateFirstResult(
            verdict="warn",
            flagged_collections=flagged,
            evidence_section_present=evidence,
            sign_off_present=False,
            acknowledged=True,
            diagnostic=(
                "acknowledged + evidence; awaiting operator sign-off "
                "(<!-- substrate_first_sign_off: <reason> --> in PRD)"
            ),
        )
    return SubstrateFirstResult(
        verdict="fail",
        flagged_collections=flagged,
        evidence_section_present=evidence,
        sign_off_present=sign_off,
        acknowledged=acknowledged,
        diagnostic=(
            "module-level vocabulary collection of >5 entries with no "
            "Substrate-First acknowledgment. Add ``hand_curation_"
            "acknowledged: true`` to frontmatter, document the "
            "structural alternative considered in a ``## Substrate-First "
            "evidence`` section, and request operator sign-off."
        ),
    )
