"""AHR L1 validation: parse (R-INT-4) -> schema -> tokens/calendar -> X-rules (PRD-CORE-347-FR03/FR06).

Order and messages follow the reference checker ``specs/handoff/tools/ahr_check.py``
(1.0-rc.1). Validation is pure: it never dereferences a URI, never evaluates record
content, and reads no file except the one ``load`` is given.
"""

from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from trw_mcp.handoff._jcs import JcsError, digest, jcs
from trw_mcp.handoff._rules import check_handoff, check_readback, instant

__all__ = [
    "DEFAULT_URI_SCHEMES",
    "MAX_CANONICAL_BYTES",
    "MAX_INPUT_BYTES",
    "AhrInputError",
    "AhrParseError",
    "Finding",
    "load",
    "loads",
    "schema_text",
    "validate",
]

JsonDoc = dict[str, Any]

MAX_CANONICAL_BYTES = 32 * 1024  # R-SIZE-2, measured on the JCS bytes
# Pre-parse bound on raw input (class B: unbounded caller work). Pretty-printed JSON is
# larger than its canonical form, so the raw bound is looser than R-SIZE-2 itself.
MAX_INPUT_BYTES = 8 * MAX_CANONICAL_BYTES
DEFAULT_URI_SCHEMES = frozenset({"file", "https", "trw"})
_NEVER_SCHEMES = frozenset({"javascript", "data"})
_TIME_KEYS = frozenset({"at", "created_at", "expires_at", "effective_at"})
_TOKEN_KEYS = frozenset(
    {
        "id",
        "handoff_id",
        "readback_id",
        "event_id",
        "subject",
        "uri",
        "digest",
        "commit",
        "observed_digest",
        "record_digest",
        "prev_event_digest",
        "readback_digest",
        "claim_id",
        "action_id",
        "risk_id",
        "owner",
        "producer",
        "depends_on",
        "reverify",
    }
    | _TIME_KEYS
)
_RULE_RE = re.compile(r"^(X-\d+|R-[A-Z]+-\d+|schema)\b:?\s*")


@dataclass(frozen=True)
class Finding:
    """One L1 violation. ``rule`` is the spec tag (``X-8``, ``R-INT-4``) or ``schema``."""

    rule: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "path": self.path, "message": self.message}


class AhrInputError(Exception):
    """Unreadable input or the wrong document kind: a usage error, never a rule violation."""


class AhrParseError(ValueError):
    """The bytes are not an I-JSON AHR document; carries the finding."""

    def __init__(self, finding: Finding) -> None:
        super().__init__(f"{finding.rule} {finding.message}")
        self.finding = finding


def _finding(text: str, path: str = "") -> Finding:
    match = _RULE_RE.match(text)
    if match is None:
        return Finding("X-0", path, text)
    return Finding(match.group(1), path, text[match.end() :] or text)


def _strict_object(pairs: list[tuple[str, Any]]) -> JsonDoc:
    obj: JsonDoc = {}
    for key, value in pairs:
        if key in obj:
            raise AhrParseError(Finding("R-INT-4", "", f"duplicate JSON member: {key}"))
        obj[key] = value
    return obj


def loads(raw: bytes) -> JsonDoc:
    """Parse raw bytes under R-INT-4 (UTF-8, no duplicate members); raises ``AhrParseError``."""
    if len(raw) > MAX_INPUT_BYTES:
        raise AhrParseError(Finding("R-SIZE-2", "", f"input of {len(raw)} bytes exceeds {MAX_INPUT_BYTES}"))
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AhrParseError(Finding("R-INT-4", "", f"not UTF-8: {exc}")) from exc
    try:
        doc = json.loads(text, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise AhrParseError(Finding("R-INT-4", "", f"not JSON: {exc}")) from exc
    if not isinstance(doc, dict):
        raise AhrParseError(Finding("R-DOC-1", "", "top level is not a JSON object"))
    return doc


def load(path: str | Path) -> JsonDoc:
    """Read and parse one record file; ``AhrInputError`` when it cannot be read."""
    try:
        # O_NONBLOCK: opening a FIFO with no writer must not hang before fstat can refuse it.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise AhrInputError(f"cannot read {path}: not a regular file")
            raw = handle.read(MAX_INPUT_BYTES + 1)  # one byte past the bound proves "too large" without reading it all
    except OSError as exc:
        raise AhrInputError(f"cannot read {path}: {exc.strerror or exc}") from exc
    return loads(raw)


def schema_text() -> str:
    """The packaged AHR v1 schema, byte-identical to the spec copy (PRD-CORE-347-FR01)."""
    return resources.files("trw_mcp.data").joinpath("ahr/ahr.schema.json").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_text()))


def _has_control(value: object) -> bool:
    return isinstance(value, str) and any(unicodedata.category(c) == "Cc" for c in value)


def _tokens(value: object, errs: list[str], *, top: bool = True) -> None:
    """X-0: token-like strings carry no control characters (R-INT-4 regex-dialect guard)."""
    if isinstance(value, list):
        for child in value:
            _tokens(child, errs, top=False)
        return
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        if top and key == "extensions":  # extensions are opaque (R-DOC-3)
            if isinstance(child, dict) and any(_has_control(k) for k in child):
                errs.append("X-0 control character in an extension key")
            continue
        vals = child if isinstance(child, list) else [child]
        if key in _TOKEN_KEYS and any(_has_control(x) for x in vals):
            errs.append(f"X-0 control character in {key}")
        _tokens(child, errs, top=False)


def _times(value: object, errs: list[str], *, top: bool = True) -> None:
    if isinstance(value, list):
        for child in value:
            _times(child, errs, top=False)
        return
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        if top and key == "extensions":
            continue
        if key in _TIME_KEYS and isinstance(child, str):
            try:
                instant(child)
            except ValueError:
                errs.append(f"R-TIME-1 impossible calendar timestamp in {key}: {child}")
        _times(child, errs, top=False)


def _uri_findings(value: object, allowed: frozenset[str], path: str, out: list[Finding]) -> None:
    """R-SEC-2: report (never dereference) URIs whose scheme is outside the allowlist."""
    if isinstance(value, list):
        for i, child in enumerate(value):
            _uri_findings(child, allowed, f"{path}/{i}", out)
        return
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        here = f"{path}/{key}"
        if path == "" and key == "extensions":
            continue
        if key == "uri" and isinstance(child, str):
            scheme = child.partition(":")[0].lower()
            if scheme in _NEVER_SCHEMES or scheme not in allowed:
                out.append(Finding("R-SEC-2", here, f"URI scheme {scheme!r} is not on the allowlist"))
        _uri_findings(child, allowed, here, out)


def _schema_findings(doc: JsonDoc) -> list[Finding]:
    return [
        Finding("schema", "/".join(map(str, e.absolute_path)) or "<root>", e.message[:160])
        for e in _validator().iter_errors(doc)
    ]


def _handoff_for_readback(handoff: JsonDoc | None) -> JsonDoc:
    if handoff is None:
        raise AhrInputError("a readback needs its handoff record (--handoff)")
    if handoff.get("type") != "handoff":
        raise AhrInputError("--handoff is not a handoff record")
    bad = validate(handoff)
    if bad:
        raise AhrInputError(f"--handoff is not a valid handoff record: {bad[0].rule} {bad[0].message}")
    return handoff


def validate(
    doc: JsonDoc,
    handoff: JsonDoc | None = None,
    *,
    allowed_schemes: frozenset[str] = DEFAULT_URI_SCHEMES,
) -> list[Finding]:
    """Return every L1 finding for ``doc``; empty means it conforms at L1.

    A read-back needs ``handoff``; a missing or invalid one raises ``AhrInputError``.
    [R] rules are never reported as passed: an empty list says nothing about them.
    """
    schema = _schema_findings(doc)
    if schema:
        return schema
    try:
        size = len(jcs(doc))
    except JcsError as exc:
        return [_finding(str(exc))]
    errs: list[str] = []
    _tokens(doc, errs)
    if errs:
        return [_finding(e) for e in errs]
    _times(doc, errs)
    if errs:
        return [_finding(e) for e in errs]
    if size > MAX_CANONICAL_BYTES:
        errs.append(f"X-0 canonical size {size} bytes exceeds 32 KiB (R-SIZE-2)")
    integ = doc.get("integrity")
    if integ and integ["digest"] != digest(doc):
        errs.append("X-1 integrity.digest does not match the recomputed digest")
    if doc["type"] == "event" and "verification" in doc:
        verifier = doc["verification"]["verifier"]
        if verifier["id"] == doc["actor"]["id"] and verifier["kind"] != doc["actor"]["kind"]:
            errs.append("X-21 principal id appears with two different kinds (actor vs verifier)")
    if doc["type"] == "handoff":
        errs += check_handoff(doc)
    elif doc["type"] == "readback":
        errs += check_readback(doc, _handoff_for_readback(handoff))
    findings = [_finding(e) for e in errs]
    _uri_findings(doc, allowed_schemes - _NEVER_SCHEMES, "", findings)
    return findings
