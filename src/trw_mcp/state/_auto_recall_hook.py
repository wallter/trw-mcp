"""The UserPromptSubmit hook's auto-recall: a filtered store read, then the PRD-FIX-124 scorer.

``user-prompt-submit.sh`` runs ``python -m trw_mcp.state._auto_recall_hook`` with the
project root, the prompt, the injected-ids file and the four tunables. The candidates
come from the checkout's store (:func:`~trw_mcp.state._store_selection.selected_store`,
the daemon), whose backend drops every identity the quarantine ledger blocks
(``StorageBackend.filter_quarantined``, PRD-CORE-333 FR02). The hook used to parse the
``.trw/learnings/entries`` YAML mirror itself, which no backend filter can reach
(PRD-CORE-333 FR03). A store it cannot reach yields no recall: failing closed, never
falling back to the mirror.

Scoring is unchanged (PRD-FIX-124): a learning's tokens are its summary plus its tags;
its score is the smoothed-IDF-weighted fraction of the prompt's keyword mass it holds;
the top ``max_results`` at or above ``min_score`` are printed as ``TRW RECALL:`` lines
within ``max_tokens * 4`` characters, and their ids are appended to the injected file so
a session never sees one twice. One ``event=AutoRecall`` diagnostic goes to stderr --
counts, scores and ids only, never prompt text.
"""

from __future__ import annotations

import math
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

START_NS = time.monotonic_ns()
#: FR08: the scan's budget, from the moment the store's rows are in hand.
TIMEOUT_NS = 500_000_000
MAX_KEYWORDS = 16
MIN_TOKEN_LEN = 4
# The hook's shell fallbacks mirror the typed TRWConfig defaults
# (models/config/_fields_build.py, _sub_models.py); tests/test_auto_recall_scoring.py
# asserts all declaration sites equal.
DEFAULT_MAX_RESULTS = 3
DEFAULT_MAX_TOKENS = 100
DEFAULT_MIN_SCORE = 0.35
DEFAULT_SCAN_CAP = 10000
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
STOP_WORDS = frozenset(
    {
        "also",
        "been",
        "call",
        "each",
        "even",
        "from",
        "have",
        "into",
        "just",
        "like",
        "make",
        "more",
        "only",
        "some",
        "take",
        "than",
        "that",
        "their",
        "them",
        "then",
        "they",
        "this",
        "what",
        "when",
        "where",
        "which",
        "will",
        "with",
        "your",
    }
)


@dataclass(frozen=True)
class Candidate:
    """One stored learning as the scorer sees it."""

    entry_id: str
    status: str
    summary: str
    tags: tuple[str, ...]


ReadRows = Callable[[Path, int], Sequence[Candidate]]


def store_rows(project_root: Path, scan_cap: int) -> list[Candidate]:
    """The *scan_cap* newest ACTIVE learnings of this checkout, read through its store.

    Raises :class:`~trw_mcp.state._store_selection.StoreUnavailableError` when the
    checkout has no reachable store.
    """
    from trw_mcp.state._store_selection import selected_store

    store, namespace = selected_store(project_root / ".trw")
    return [
        Candidate(entry.id, str(entry.status), entry.content, tuple(entry.tags))
        for entry in store.list_entries(namespace, status="active", limit=scan_cap)
    ]


def _tokenize(text: str) -> set[str]:
    return {word for word in TOKEN_RE.findall(text.lower()) if len(word) >= MIN_TOKEN_LEN and word not in STOP_WORDS}


def _as_int(raw: str, fallback: int, minimum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value >= minimum else fallback


def _as_float(raw: str, fallback: float, low: float, high: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if low <= value <= high else fallback


def _keywords(prompt: str) -> list[str]:
    keywords: list[str] = []
    for word in TOKEN_RE.findall(prompt.lower()):
        if len(word) < MIN_TOKEN_LEN or word in STOP_WORDS or word in keywords:
            continue
        if len(keywords) >= MAX_KEYWORDS:
            break
        keywords.append(word)
    return keywords


@dataclass
class Outcome:
    """What one scorer run decided (the FR05 diagnostic's fields)."""

    decision: str
    scanned: int = 0
    top_score: float = 0.0
    top_id: str = ""
    lines: tuple[str, ...] = ()
    emitted_ids: tuple[str, ...] = ()


def score(
    keywords: list[str],
    rows: Sequence[Candidate],
    *,
    injected_ids: set[str],
    max_results: int,
    max_chars: int,
    min_score: float,
    deadline_ns: int,
) -> Outcome:
    """Rank *rows* against *keywords*; a passed deadline scores what it already scanned (FR08)."""
    candidates: list[tuple[str, str, str, frozenset[str]]] = []
    doc_freq = dict.fromkeys(keywords, 0)
    doc_count = scanned = deduped = 0
    deadline_hit = False
    for row in rows:
        if time.monotonic_ns() >= deadline_ns:
            deadline_hit = True
            break
        scanned += 1
        # FR12: the stored status is the candidate gate.
        if row.status.lower() != "active" or not row.summary:
            continue
        if row.entry_id in injected_ids:
            # Already shown this session: not a candidate, but a match on it is why nothing new fires.
            deduped += any(keyword in _tokenize(row.summary + " " + " ".join(row.tags)) for keyword in keywords)
            continue
        doc_count += 1
        tokens = _tokenize(row.summary + " " + " ".join(row.tags))
        matched = frozenset(keyword for keyword in keywords if keyword in tokens)
        if not matched:
            continue
        for keyword in matched:
            doc_freq[keyword] += 1
        display_id = row.entry_id if row.entry_id.startswith("L-") else f"L-{row.entry_id}"
        candidates.append((row.entry_id, display_id, row.summary, matched))

    # Smoothed IDF: log((N + 1) / (df + 1)) + 1 keeps a perfect match on a 1-entry store above zero.
    idf = {keyword: math.log((doc_count + 1) / (doc_freq[keyword] + 1)) + 1.0 for keyword in keywords}
    total_idf = sum(idf.values())

    def _score(matched: frozenset[str]) -> float:
        if total_idf <= 0.0:
            return len(matched) / len(keywords)
        return sum(idf[keyword] for keyword in matched) / total_idf

    scored = [(_score(matched), entry_id, display_id, summary) for entry_id, display_id, summary, matched in candidates]
    outcome = Outcome(decision="", scanned=scanned)
    for value, entry_id, _display, _summary in scored:
        if value > outcome.top_score:
            outcome.top_score, outcome.top_id = value, entry_id
    selected = sorted((item for item in scored if item[0] >= min_score), key=lambda item: (-item[0], item[1]))
    lines: list[str] = []
    emitted: list[str] = []
    total_chars = 0
    for _value, entry_id, display_id, summary in selected[:max_results]:
        line = f"TRW RECALL: [{display_id}] {summary}"
        # NFR06: the newline after each line counts against the budget too.
        if total_chars + len(line) + 1 > max_chars:
            break
        lines.append(line)
        emitted.append(entry_id)
        total_chars += len(line) + 1
    outcome.lines, outcome.emitted_ids = tuple(lines), tuple(emitted)
    if deadline_hit:
        outcome.decision = "deadline"
    elif lines:
        outcome.decision = "fired"
    elif outcome.top_score <= 0.0:
        outcome.decision = "dedup" if deduped else "no_match"
    else:
        outcome.decision = "below_threshold"
    return outcome


def _diagnostic(outcome: Outcome, keywords: int, min_score: float) -> None:
    """FR05: one machine-readable record per run, on stderr only; NFR03: no prompt or detail text."""
    elapsed_ms = (time.monotonic_ns() - START_NS) // 1_000_000
    sys.stderr.write(
        f"event=AutoRecall keywords={keywords} scanned={outcome.scanned}"
        f" top_score={outcome.top_score:.3f} top_id={outcome.top_id or 'none'}"
        f" threshold={min_score:.3f} injected={len(outcome.lines)}"
        f" decision={outcome.decision} elapsed_ms={elapsed_ms}\n"
    )


def main(argv: Sequence[str], *, read_rows: ReadRows = store_rows, stdin: TextIO | None = None) -> int:
    """``<project_root> <prompt> <injected_file> <max_results> <max_tokens> <min_score> <scan_cap>``.

    *injected_file* is both the history of ids already delivered (read) and where the ids injected now are
    appended. The hook hands it a scratch copy of the real dedup file and moves the result over only once the
    text has actually been emitted, so a hook cancelled or cut off in between records nothing.

    A prompt of ``-`` is read from stdin: a shell hands a long prompt over argv only up to ARG_MAX
    (128 KB per argument on Linux), past which the exec fails and recall silently never runs.
    """
    project_root, prompt, injected_file = Path(argv[0]), argv[1], Path(argv[2])
    if prompt == "-":
        prompt = (stdin or sys.stdin).read()
    max_results = _as_int(argv[3], DEFAULT_MAX_RESULTS, 0)
    max_tokens = _as_int(argv[4], DEFAULT_MAX_TOKENS, 0)
    min_score = _as_float(argv[5], DEFAULT_MIN_SCORE, 0.0, 1.0)
    scan_cap = _as_int(argv[6], DEFAULT_SCAN_CAP, 1)
    keywords = _keywords(prompt)
    if not keywords:
        _diagnostic(Outcome("no_keywords"), 0, min_score)
        return 0
    try:
        rows = read_rows(project_root, scan_cap)
    except Exception as exc:  # justified: fail-open, any failed store read is no recall, recorded below
        # Fail closed on the CONTENT: no store, no recall -- never the unfiltered mirror.
        # Every failure counts, not only StoreUnavailableError: a daemon refusal
        # (ToolError) or an unreadable pin must record store_unavailable, not crash the
        # hook into exit 1, which the shell reads as "no interpreter" (PRD-CORE-333 S3b).
        sys.stderr.write(f"auto_recall_store_unavailable: {type(exc).__name__}\n")
        _diagnostic(Outcome("store_unavailable"), len(keywords), min_score)
        return 0
    injected_ids: set[str] = set()
    if injected_file.is_file():
        text = injected_file.read_text(encoding="utf-8", errors="replace")
        injected_ids = {line.strip() for line in text.splitlines() if line.strip()}
    outcome = score(
        keywords,
        rows[:scan_cap],
        injected_ids=injected_ids,
        max_results=max_results,
        max_chars=max_tokens * 4,
        min_score=min_score,
        deadline_ns=time.monotonic_ns() + TIMEOUT_NS,
    )
    _diagnostic(outcome, len(keywords), min_score)
    if outcome.lines:
        _record_injected(project_root, injected_file, outcome.emitted_ids)
        sys.stdout.write("\n".join(outcome.lines))
    return 0


def _record_injected(project_root: Path, injected_file: Path, entry_ids: Sequence[str]) -> None:
    """Append *entry_ids* to the per-session dedup file through the checkout writer, never via a symlink.

    The file sits in the checkout (``.trw/context``), which is not trusted content, so the append walks
    every component below the project root without following a link (``_checkout_write``). A file
    outside the root (a caller's own path) is protected at its leaf only. A refused or failed append
    costs the dedup record, not the recall: the lines are still printed.
    """
    from trw_mcp._checkout_write import UnsafeWriteError, append_checkout_file

    root = project_root if injected_file.is_relative_to(project_root) else injected_file.parent
    try:
        append_checkout_file(root, injected_file, "".join(f"{entry_id}\n" for entry_id in entry_ids))
    except (UnsafeWriteError, OSError) as exc:  # justified: fail-open, the dedup record is optional; logged below
        sys.stderr.write(f"auto_recall_dedup_write_refused: {type(exc).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
