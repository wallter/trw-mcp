"""Closed plan schema with typed refusals (PRD-CORE-275-FR04, FR07).

A body arriving from a peer is UNTRUSTED DATA. This module is the only place it
is interpreted, and it accepts exactly the declared shape — no unknown keys, no
coercion, no "close enough". Anything else is refused locally with a typed
reason, which produces no message, no retry and no transport state: a
non-protocol body is data, never an error condition of the transport.

The authority rule (FR07) is enforced twice over, and neither check is the
security argument on its own. The allowlist means an approval-shaped key cannot
survive parsing; the real argument is that nothing in this package can reach a
permission, completion or safety mutator at all, which is asserted by import
closure in the tests.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from trw_mcp.plan._body import PROPOSAL_KEYS, REVIEW_KEYS, SCHEMA, digest_of, normalize_paths

PLAN_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_SUMMARY_BYTES = 1024
MAX_FINDING_BYTES = 512
MIN_PATHS = 1
MAX_PATHS = 64
MAX_FINDINGS = 32
MAX_PATH_BYTES = 1024
#: A revision is a counter, not an arbitrary integer. Unbounded, it digests and
#: parses at 10**30 — found by an independent review.
MAX_REVISION = 1_000_000

#: A Windows drive-letter prefix (`C:/`, `c:\`). It does not start with "/", so a
#: naive absolute check misses it entirely.
DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
#: A URL-ish scheme (`file://`, `http://`). Also misses a leading-slash check.
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

#: Keys that would make a body read as authority. Refused on sight. This is a
#: tripwire for an obvious mistake, not the boundary that makes the feature
#: safe — see the module docstring.
AUTHORITY_KEYS = frozenset(
    {
        "approved",
        "approval",
        "permission",
        "permissions",
        "grant",
        "granted",
        "complete",
        "completed",
        "completion",
        "verdict",
        "signoff",
        "sign_off",
        "authorize",
        "authorized",
        "safety",
        "override",
    }
)


class PlanRefusal(str, Enum):
    """Closed refusal vocabulary. Each value is one operator-actionable cause."""

    NOT_JSON = "body_not_json"
    NOT_OBJECT = "body_not_object"
    WRONG_SCHEMA = "unknown_schema"
    WRONG_TYPE = "unknown_body_type"
    UNKNOWN_KEY = "unknown_key"
    MISSING_KEY = "missing_key"
    WRONG_FIELD_TYPE = "wrong_field_type"
    BAD_PLAN_ID = "malformed_plan_id"
    BAD_REVISION = "malformed_revision"
    DUPLICATE_PATH = "duplicate_path"
    ABSOLUTE_PATH = "absolute_path"
    EMPTY_PATH = "empty_path"
    TRAVERSAL_PATH = "traversal_path"
    TOO_MANY = "too_many_items"
    TOO_LARGE = "field_too_large"
    AUTHORITY_FIELD = "authority_field_present"
    DIGEST_MISMATCH = "digest_mismatch"
    CONTROL_CHARACTER = "control_character"
    TOO_FEW = "too_few_items"


class PlanError(Exception):
    """Every refusal. Carries a closed reason, never raw exception text.

    ``detail`` may name the caller's own plan id, revision and repo-relative
    paths — those are the caller's own inputs or in-formation facts — but never
    an absolute path, a traceback, or an interpreter message.
    """

    def __init__(self, refusal: PlanRefusal, detail: str = "") -> None:
        super().__init__(f"{refusal.value}: {detail}" if detail else refusal.value)
        self.refusal = refusal
        self.detail = detail


def _has_control_chars(text: str) -> bool:
    """Any C0/C1 control, including newline and ESC.

    Findings are printed and read by another agent. A newline lets a hostile
    peer forge a line that looks like this tool's own output ("CURRENT — ..."),
    and an ESC lets it move the cursor or recolour the terminal. Neither is a
    legitimate character in a path, a summary or a finding.
    """

    return any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in text)


def _require(condition: bool, refusal: PlanRefusal, detail: str = "") -> None:
    if not condition:
        raise PlanError(refusal, detail)


def check_path(raw: str) -> str:
    """One repo-relative path, or a typed refusal.

    Absolute paths are refused OUTRIGHT, including one that happens to sit
    inside the project. That is a protocol rule of this slice, not inherited
    behaviour: `formation.relative_to_root` deliberately ACCEPTS an in-root
    absolute path by normalizing it. Refusing is what lets a peer recompute the
    same digest without knowing the sender's project root.
    """

    _require(bool(raw), PlanRefusal.EMPTY_PATH, "a path is empty")
    _require(len(raw.encode("utf-8")) <= MAX_PATH_BYTES, PlanRefusal.TOO_LARGE, f"path exceeds {MAX_PATH_BYTES} bytes")
    _require(not _has_control_chars(raw), PlanRefusal.CONTROL_CHARACTER, "a path contains a control character")
    # `startswith("/")` alone is not an absolute-path check. An independent
    # review got `C:/repo/a.py`, `C:\repo\a.py` and `file:///etc/passwd` past it,
    # none of which begin with a slash.
    _require(not raw.startswith("/"), PlanRefusal.ABSOLUTE_PATH, f"{raw!r} is absolute; send a repo-relative path")
    _require(
        not DRIVE_LETTER.match(raw), PlanRefusal.ABSOLUTE_PATH, f"{raw!r} names a drive; send a repo-relative path"
    )
    _require(not SCHEME.match(raw), PlanRefusal.ABSOLUTE_PATH, f"{raw!r} looks like a URL; send a repo-relative path")
    # POSIX separators only. A backslash is a literal filename character on
    # POSIX, so `..\\..\\etc\\passwd` is one impossible name rather than a
    # traversal — but accepting it invites a Windows consumer to read it as one.
    _require("\\" not in raw, PlanRefusal.TRAVERSAL_PATH, f"{raw!r} uses backslashes; paths are POSIX")
    _require(".." not in raw.split("/"), PlanRefusal.TRAVERSAL_PATH, f"{raw!r} traverses outside the project")
    return raw


def _check_paths(values: object, field: str, *, minimum: int = 0) -> list[str]:
    if not isinstance(values, list):
        raise PlanError(PlanRefusal.WRONG_FIELD_TYPE, f"{field} is not a list")
    items: list[object] = list(values)
    _require(len(items) >= minimum, PlanRefusal.TOO_FEW, f"{field} needs at least {minimum} entry")
    _require(len(items) <= MAX_PATHS, PlanRefusal.TOO_MANY, f"{field} exceeds {MAX_PATHS} entries")
    for item in items:
        _require(isinstance(item, str), PlanRefusal.WRONG_FIELD_TYPE, f"{field} holds a non-string")
    paths = [check_path(str(item)) for item in items]
    _require(len(set(paths)) == len(paths), PlanRefusal.DUPLICATE_PATH, f"{field} repeats a path")
    return paths


def _check_common(body: dict[str, Any], keys: tuple[str, ...], body_type: str) -> None:
    for key in body:
        _require(key not in AUTHORITY_KEYS, PlanRefusal.AUTHORITY_FIELD, f"{key!r} is not a plan field")
        _require(key in keys, PlanRefusal.UNKNOWN_KEY, f"{key!r} is not part of this schema")
    for key in keys:
        _require(key in body, PlanRefusal.MISSING_KEY, f"{key!r} is required")
    _require(body["schema"] == SCHEMA, PlanRefusal.WRONG_SCHEMA, "body declares another schema")
    _require(body["type"] == body_type, PlanRefusal.WRONG_TYPE, f"body is not a {body_type}")
    _require(
        isinstance(body["plan_id"], str) and bool(PLAN_ID.match(body["plan_id"])),
        PlanRefusal.BAD_PLAN_ID,
        "plan_id is not 32 lowercase hex characters",
    )
    revision = body["revision"]
    _require(
        isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1 and revision <= MAX_REVISION,
        PlanRefusal.BAD_REVISION,
        f"revision is not an integer in 1..{MAX_REVISION}",
    )


def loads(raw: str) -> dict[str, Any]:
    """Decode a body, refusing anything that is not a JSON object."""

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PlanError(PlanRefusal.NOT_JSON, "body is not decodable JSON") from exc
    _require(isinstance(parsed, dict), PlanRefusal.NOT_OBJECT, "body is not a JSON object")
    return dict(parsed)


def parse_proposal(raw: str) -> dict[str, Any]:
    """Validate a proposal AND its digest. Returns the parsed body."""

    body = loads(raw)
    _check_common(body, PROPOSAL_KEYS, "proposal")
    body["paths"] = _check_paths(body["paths"], "paths", minimum=MIN_PATHS)
    body["test_paths"] = _check_paths(body["test_paths"], "test_paths")
    summary = body["summary"]
    _require(isinstance(summary, str), PlanRefusal.WRONG_FIELD_TYPE, "summary is not a string")
    _require(len(summary.encode("utf-8")) <= MAX_SUMMARY_BYTES, PlanRefusal.TOO_LARGE, "summary is too long")
    _require(not _has_control_chars(summary), PlanRefusal.CONTROL_CHARACTER, "summary contains a control character")
    _require(isinstance(body["digest"], str), PlanRefusal.WRONG_FIELD_TYPE, "digest is not a string")
    # Normalized before comparison, so a body whose arrays arrive unsorted but
    # otherwise identical verifies rather than being rejected on presentation.
    body["paths"] = normalize_paths(body["paths"])
    body["test_paths"] = normalize_paths(body["test_paths"])
    _require(digest_of(body) == body["digest"], PlanRefusal.DIGEST_MISMATCH, "digest does not match the body")
    return body


def parse_review(raw: str) -> dict[str, Any]:
    """Validate a review body. It carries no digest of its own by design."""

    body = loads(raw)
    _check_common(body, REVIEW_KEYS, "review")
    _require(isinstance(body["digest"], str), PlanRefusal.WRONG_FIELD_TYPE, "digest is not a string")
    findings = body["findings"]
    _require(isinstance(findings, list), PlanRefusal.WRONG_FIELD_TYPE, "findings is not a list")
    _require(len(findings) <= MAX_FINDINGS, PlanRefusal.TOO_MANY, f"findings exceeds {MAX_FINDINGS} entries")
    for finding in findings:
        _require(isinstance(finding, str), PlanRefusal.WRONG_FIELD_TYPE, "findings holds a non-string")
        _require(len(finding.encode("utf-8")) <= MAX_FINDING_BYTES, PlanRefusal.TOO_LARGE, "a finding is too long")
        _require(
            not _has_control_chars(str(finding)),
            PlanRefusal.CONTROL_CHARACTER,
            "a finding contains a control character",
        )
    return body
