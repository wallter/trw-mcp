"""Helper briefs projected from a handoff record, and a mechanical check of what helpers return.

Belongs to the :mod:`trw_mcp.handoff` package (``trw-mcp handoff brief`` / ``brief-check``). A lead that
fans a read-only sweep out to cheaper helpers needs two things a small model cannot be trusted to supply
itself: the governing scope and constraints carried unchanged, and a return shape code can check.

- :func:`render_briefs` projects the record's commit, ``objective.paths`` and ``constraints`` (verbatim,
  never truncated) plus a fixed item ledger into one brief per item. Everything shared comes first and is
  byte-identical across items, so a harness can cache it; only the item varies at the end.
- :func:`check_results` checks the returned rows against the same items: one row per id, an allowed
  status and label, and every cited line present in scope exactly as quoted. A row that fails any check is
  ``inconclusive``. A row that passes is ``citation_valid``: the cited lines exist as quoted. That says
  nothing about whether the answer is right, and nothing here ever labels a claim ``verified``.

A leaf: it opens only repository-confined files and imports nothing from dispatch, telemetry or run state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from trw_mcp.handoff._glob import covers
from trw_mcp.handoff._quotes import text_lines
from trw_mcp.handoff._repo import confined_path, read_capped
from trw_mcp.handoff._validate import PLACEHOLDER, AhrInputError

__all__ = ["MAX_ITEMS", "MAX_ROWS", "check_results", "load_items", "parse_rows", "render_briefs"]

JsonDoc = dict[str, Any]
MAX_ITEMS = 100
#: One line of item text (question, hint, must_contain): a brief stays small whatever the items file holds.
MAX_ITEM_TEXT = 500
#: Rows accepted from a results file; helpers return one row per item, so far more than that is not a sweep.
MAX_ROWS = 10 * MAX_ITEMS
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*$")
_NON_SPACE = re.compile(r"\S")
_STATUSES = frozenset({"answered", "not_answered", "inconclusive"})
_LABELS = frozenset({"observed", "inferred"})
_MAX_FILE_BYTES = 2 * 1024 * 1024
#: Cited-file bytes one check may read and hold; rows are helper output, so the total is bounded too.
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_EVIDENCE = 10
_RULES = """Output: one JSON object and nothing else (no code fence, no prose), then stop.
{"item":"<your id>","status":"answered|not_answered|inconclusive","answer":"<short answer>","evidence":[{"path":"<repository-relative path>","line":<number>,"text":"<that line, copied exactly>"}],"label":"observed|inferred"}
Rules:
1. Open each file with a read tool before citing it. Copy text character for character, never from memory.
2. Cite only lines you opened this turn. Never write "verified": that label is not yours.
3. Out of scope, file missing, tool error, or you cannot tell: status "inconclusive", evidence []. Never answer "no" to avoid uncertainty.
4. Use at most {max_reads} reads. If the item is not settled by then, return inconclusive. Do not summarise other items."""


def load_items(raw: object) -> list[JsonDoc]:
    """The sweep's items: ``[{"id", "question", "hint"?, "must_contain"?}]`` with unique, id-shaped ids."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ITEMS:
        raise AhrInputError(f"items must be a JSON array of 1 to {MAX_ITEMS} objects")
    seen: set[str] = set()
    items: list[JsonDoc] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - {"id", "question", "hint", "must_contain"}:
            raise AhrInputError("each item is an object with id, question and optional hint, must_contain")
        item_id = entry.get("id")
        if not isinstance(item_id, str) or not _ID.match(item_id) or item_id in seen:
            raise AhrInputError(f"item id {item_id!r} is not a unique identifier")
        for key in ("question", "hint", "must_contain"):
            value = entry.get(key)
            if key != "question" and key not in entry:
                continue
            if not isinstance(value, str) or not value.strip() or "\n" in value or len(value) > MAX_ITEM_TEXT:
                raise AhrInputError(
                    f"item {item_id}: {key} must be one non-empty line of at most {MAX_ITEM_TEXT} characters"
                )
        seen.add(item_id)
        items.append(dict(entry))
    return items


def _scope(record: JsonDoc) -> list[str]:
    """The record's ``objective.paths``. How wide it is, is the lead's choice (``**`` is the whole
    repository); a citation is confined to the repository whatever these patterns say."""
    objective = record.get("objective")
    paths = objective.get("paths") if isinstance(objective, dict) else None
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise AhrInputError("the record has no objective.paths: a brief needs an exact scope (handoff new --path)")
    return [str(p) for p in paths]


def _constraint_lines(record: JsonDoc) -> list[str]:
    constraints = record.get("constraints")
    if "constraints" not in record or (isinstance(constraints, dict) and constraints.get("none_known") is True):
        return []  # absent, or the explicit {none_known: true, checked}
    if not isinstance(constraints, list):
        raise AhrInputError("the record's constraints are neither a list nor {none_known: true, checked}")
    lines = []
    for con in constraints:
        text = con.get("text") if isinstance(con, dict) else con
        if not isinstance(text, str) or not text.strip() or PLACEHOLDER in text:
            raise AhrInputError("the record's constraints are unfilled: carry them with --constraint/--constraint-from")
        source = con.get("source") if isinstance(con, dict) else None
        cited = f"  [source {source['uri']}]" if isinstance(source, dict) and isinstance(source.get("uri"), str) else ""
        lines.append("- " + text.replace("\n", "\n  ") + cited)
    return lines


def render_briefs(
    record: JsonDoc, items: list[JsonDoc], *, today: str, max_reads: int = 6, max_chars: int = 16000
) -> JsonDoc:
    """``{"prefix_sha256", "briefs": {id: text}}``: one read-only sweep brief per item.

    Refuses instead of truncating: a prefix over ``max_chars`` is an error, because a shortened constraint
    is a paraphrased constraint.
    """
    as_of = record.get("as_of")
    base = as_of.get("base_ref") if isinstance(as_of, dict) else None
    commit = base["commit"] if isinstance(base, dict) and isinstance(base.get("commit"), str) else "unknown"
    constraints = _constraint_lines(record)
    prefix = "\n".join(
        [
            f"Date: {today}. Repository at commit {commit}. Read-only: edit or write nothing.",
            "Scope (only these paths; any other path is out of scope):",
            *(f"- {path}" for path in _scope(record)),
            *(["Constraints (verbatim; obey all):", *constraints] if constraints else ["Constraints: none recorded."]),
            "Ledger (every item in this sweep; you answer only yours): " + ", ".join(i["id"] for i in items),
            _RULES.replace("{max_reads}", str(max_reads)),
            "---",
        ]
    )
    if len(prefix) > max_chars:
        raise AhrInputError(
            f"the shared brief is {len(prefix)} characters, over the {max_chars} cap: split the sweep or cite "
            "long constraints by pointer; a brief never truncates a constraint"
        )
    briefs = {}
    for item in items:
        tail = [f"Your item: {item['id']}", f"Question: {item['question']}"]
        if "hint" in item:
            tail.append(f"Hint (the lead's search, not evidence): {item['hint']}")
        briefs[item["id"]] = prefix + "\n" + "\n".join(tail) + "\n"
    return {"prefix_sha256": hashlib.sha256(prefix.encode("utf-8")).hexdigest(), "briefs": briefs}


@dataclass
class _Opened:
    """The cited files read so far in one check, under one byte budget for the whole check."""

    remaining: int
    lines: dict[Path, list[str]] = field(default_factory=dict)


def _cited_line(evidence: object, scope: list[str], root: Path, opened_files: _Opened) -> tuple[str, str]:
    """``("", line)`` when the citation holds, else ``(reason, "")``.

    The quoted text must equal the file's line at that number, indentation included; only one line ending
    the helper copied along is dropped. A near miss is inconclusive, which costs the lead one re-read.
    """
    if not isinstance(evidence, dict):
        return "malformed_evidence", ""
    path, line, text = evidence.get("path"), evidence.get("line"), evidence.get("text")
    if not isinstance(path, str) or not isinstance(line, int) or isinstance(line, bool) or not isinstance(text, str):
        return "malformed_evidence", ""
    if path.startswith("/") or ".." in PurePosixPath(path).parts or not any(covers(glob, path) for glob in scope):
        return "path_out_of_scope", ""
    resolved, _reason = confined_path("file:" + path, root)
    if resolved is None:
        return "path_out_of_scope", ""  # every confinement refusal: a symlink out of the repository, an odd URI
    opened = resolved.relative_to(root.resolve()).as_posix()
    if not any(covers(glob, opened) for glob in scope):
        return "path_out_of_scope", ""  # an in-scope name that resolves to an out-of-scope file
    if not resolved.is_file():
        return "path_missing", ""
    if resolved not in opened_files.lines:
        if opened_files.remaining <= 0:
            return "read_budget_exhausted", ""
        try:
            data = read_capped(resolved, _MAX_FILE_BYTES)
            opened_files.lines[resolved] = text_lines(data.decode("utf-8"))
        except (OSError, UnicodeDecodeError):  # trw-fail-silent-allow: unreadable or non-text is never a match
            return "path_unreadable", ""
        opened_files.remaining -= len(data)
    lines = opened_files.lines[resolved]
    if not 1 <= line <= len(lines):
        return "line_out_of_range", ""
    found = lines[line - 1].removesuffix("\r")
    if not text.strip() or found != text.removesuffix("\n").removesuffix("\r"):
        return "text_mismatch", ""
    return "", found


def _row_reasons(row: JsonDoc, item: JsonDoc, scope: list[str], root: Path, opened_files: _Opened) -> list[str]:
    status, label = row.get("status"), row.get("label")
    if not isinstance(status, str) or status not in _STATUSES:
        return ["malformed_row"]
    if status != "answered":
        return [f"helper_{status}"]
    reasons: list[str] = []
    if not isinstance(label, str) or label not in _LABELS:
        reasons.append("label_not_allowed")
    if not isinstance(row.get("answer"), str) or not row["answer"].strip():
        reasons.append("no_answer")
    evidence = row.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= _MAX_EVIDENCE:
        return [*reasons, "no_evidence"]
    cited: list[str] = []
    for entry in evidence:
        reason, line = _cited_line(entry, scope, root, opened_files)
        if reason:
            reasons.append(reason)
        else:
            cited.append(line)
    needle = item.get("must_contain")
    if needle and not any(needle in line for line in cited):
        reasons.append("hint_not_matched")
    return list(dict.fromkeys(reasons))


def check_results(record: JsonDoc, items: list[JsonDoc], rows: object, root: Path) -> JsonDoc:
    """Per-item outcome for the returned ``rows``: ``citation_valid`` or ``inconclusive`` with reasons."""
    scope = _scope(record)
    by_id: dict[str, list[JsonDoc]] = {}
    unassigned = 0
    known = {item["id"] for item in items}
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise AhrInputError(f"results must hold at most {MAX_ROWS} rows")
    for row in rows:
        row_id = row.get("item") if isinstance(row, dict) else None
        if isinstance(row_id, str) and row_id in known:
            by_id.setdefault(row_id, []).append(row)
        else:
            unassigned += 1
    opened_files = _Opened(remaining=_MAX_TOTAL_BYTES)
    outcomes = []
    for item in items:
        found = by_id.get(item["id"], [])
        if len(found) != 1:
            reasons = ["missing_row" if not found else "duplicate_rows"]
        else:
            reasons = _row_reasons(found[0], item, scope, root, opened_files)
        outcomes.append(
            {"id": item["id"], "outcome": "inconclusive" if reasons else "citation_valid"}
            | ({"reasons": reasons} if reasons else {})
        )
    valid = sum(1 for o in outcomes if o["outcome"] == "citation_valid")
    return {
        "items": outcomes,
        "counts": {"citation_valid": valid, "inconclusive": len(outcomes) - valid, "unassigned_rows": unassigned},
        "note": "citation_valid: the cited lines exist in scope exactly as quoted. It is not a judgement that "
        "the answer is right; an inconclusive item is never a negative finding.",
    }


def parse_rows(text: str) -> list[Any]:
    """Rows from helper output: a JSON array, or JSON objects one after another.

    Helpers return one object each, on one line, pretty-printed, or inside a code fence (3 of 8 did in the
    first sweep), so fence lines are dropped and values are read in sequence. Any other text is not an
    answer: the rest of its line becomes one malformed row (``None``).
    """
    text = "\n".join(line for line in text.split("\n") if not _FENCE.match(line))
    decoder = json.JSONDecoder()
    rows: list[Any] = []
    pos = 0
    while (match := _NON_SPACE.search(text, pos)) is not None:
        try:
            value, pos = decoder.raw_decode(text, match.start())
        except (ValueError, RecursionError):  # trw-fail-silent-allow: text that is not JSON is a malformed row
            end = text.find("\n", match.start())
            value, pos = None, len(text) if end < 0 else end + 1
        rows.extend(value) if isinstance(value, list) else rows.append(value)
    return rows
