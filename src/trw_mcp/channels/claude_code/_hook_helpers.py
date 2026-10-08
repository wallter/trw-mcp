"""CC-03/CC-04 hook helper utilities for the Claude Code channel.

Belongs to the ``channels/claude_code`` package (PRD-DIST-2405 FR06, FR29).

Provides:
- ``_CEREMONY_MODE_FIELD``: authoritative config field name (FR06).
- ``read_cc03_config()``: reads CC-03 opt-in flag and skip extensions.
- ``format_t1_hint()``, ``format_t2_hint()``:
  tier-appropriate hook output formatters.
- ``write_hint_file()``: writes per-hint context JSON keyed on tool_use_id (FR29).
- ``prune_hint_files()``: removes hint files older than TTL (FR35).

Authoritative field name (P1-02 fix):
  ``python -c "from trw_mcp.models.config._fields_ceremony import _CeremonyFields;
  print(list(_CeremonyFields.model_fields))"`` confirms ``ceremony_mode`` exists
  (values: ``"full"`` | ``"light"``). The CC-02 gate uses ``ceremony_mode``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import structlog

from trw_mcp._checkout_write import write_checkout_file

log = structlog.get_logger(__name__)

__all__ = [
    "CC03_HINTS_DIR",
    "DEFAULT_SKIP_EXTENSIONS",
    "SHARED_MODULE_MIN_IMPORTERS",
    "_CEREMONY_MODE_FIELD",
    "HintAsOf",
    "as_of_line",
    "format_t1_hint",
    "format_t2_hint",
    "mark_sidecar_remedy_delivered",
    "prune_hint_files",
    "read_cc03_config",
    "sidecar_remedy_marker",
    "sidecar_remedy_once",
    "write_hint_file",
]

# --- P1-02 resolution: authoritative field name ---
# Verified by inspecting trw_mcp.models.config._fields_ceremony.model_fields:
#   ceremony_mode: Literal["full", "light"] = "full"
# This constant is referenced by tests and the CC-02 segment gate.
_CEREMONY_MODE_FIELD: str = "ceremony_mode"

# Directory for per-hint context files (P1-04: tool_use_id-keyed)
CC03_HINTS_DIR: str = ".trw/context/cc03-hints"

# P0-10 fix: safe-to-skip extensions ALLOWLIST (extensions that never get hint)
DEFAULT_SKIP_EXTENSIONS: frozenset[str] = frozenset({".md", ".txt", ".rst", ".lock", ".log", ".gitignore"})

# Hint file TTL for pruning
_HINT_FILE_TTL_SECONDS: int = 86400  # 24 hours


def _distill_importable() -> bool:
    """Whether trw-distill is importable by THIS interpreter, without importing it.

    Delegates to :func:`trw_mcp.tools._sidecar_substrate.distill_installed` --
    the ONE ``find_spec`` probe every other distill-presence check in this
    package already uses (the ``distill``/``hint_delivery`` doctor rows, the
    sidecar tier gate) -- rather than a second ``find_spec`` call. Reusing the
    exact attribute path also means this default is covered by the test
    suite's existing ``_default_distill_absent`` autouse fixture, which pins
    that function for hermetic, environment-independent tests; a second probe
    would silently bypass that pin and make this default flip on any dev venv
    that happens to have trw-distill installed, EVEN in a test asserting the
    opposite (module import, not the object, so a monkeypatch of the target
    attribute is honoured).
    """
    import trw_mcp.tools._sidecar_substrate as _substrate

    return _substrate.distill_installed()


def read_cc03_config(repo_root: Path) -> dict[str, Any]:
    """Read CC-03 configuration from ``.trw/config.yaml``.

    Returns a dict with:
      - ``cc03_hook_enabled``: bool. An explicit ``true``/``false`` (top-level,
        ``channels.cc03_hook_enabled``, or ``channels.cc03.enabled``) always
        wins. With no explicit value anywhere, the effective default is
        ``"auto"``: on when trw-distill is importable by this interpreter
        (:func:`_distill_importable`), off otherwise -- so a licensed install
        gets pre-edit hints without an undocumented flag, and an unlicensed one
        is unchanged (PRD-FIX release-window fix, 2026-09-27; disable with
        ``cc03_hook_enabled: false`` in ``.trw/config.yaml``).
      - ``skip_extensions``: set of extensions to skip
      - ``cc03_t0_silent``: bool (default False)
      - ``debounce_seconds``: int (default 180)

    Fail-open: any read/parse error returns safe defaults.
    """
    config_path = repo_root / ".trw" / "config.yaml"
    defaults: dict[str, Any] = {
        "cc03_hook_enabled": _distill_importable(),
        "skip_extensions": set(DEFAULT_SKIP_EXTENSIONS),
        "cc03_t0_silent": False,
        "debounce_seconds": 180,
    }

    if not config_path.exists():
        return defaults

    try:
        # Use ruamel or simple yaml parse
        try:
            from ruamel.yaml import YAML

            yaml_parser = YAML(typ="safe")
            raw = yaml_parser.load(config_path.read_text(encoding="utf-8"))
        except ImportError:
            import yaml

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        if not isinstance(raw, dict):
            return defaults

        # Top-level cc03 key or channels.cc03 nesting. `explicit` stays None
        # (never a bool) until an actual key is found, so an absent key never
        # masquerades as an explicit `false` and overrides the auto default above.
        explicit: bool | None = None
        channels_cfg = raw.get("channels", {})
        if isinstance(channels_cfg, dict):
            cc03_cfg = channels_cfg.get("cc03", {})
            if isinstance(cc03_cfg, dict):
                if "cc03_hook_enabled" in channels_cfg:
                    explicit = bool(channels_cfg["cc03_hook_enabled"])
                elif "enabled" in cc03_cfg:
                    explicit = bool(cc03_cfg["enabled"])
                custom_exts = cc03_cfg.get("skip_extensions")
                if isinstance(custom_exts, list):
                    defaults["skip_extensions"] = set(custom_exts)
                defaults["cc03_t0_silent"] = bool(cc03_cfg.get("t0_silent", False))
                defaults["debounce_seconds"] = int(cc03_cfg.get("debounce_seconds", 180))
            elif "cc03_hook_enabled" in channels_cfg:
                explicit = bool(channels_cfg["cc03_hook_enabled"])

        # Top-level cc03_hook_enabled (FR09) is the highest-priority override.
        if "cc03_hook_enabled" in raw:
            explicit = bool(raw["cc03_hook_enabled"])

        if explicit is not None:
            defaults["cc03_hook_enabled"] = explicit

    except Exception as exc:
        log.debug("cc03_config_read_failed", config_path=str(config_path), error=str(exc))

    return defaults


def format_t1_hint(learnings: list[dict[str, Any]]) -> str:
    """Format T1 hint output from learnings (≤ 60 tokens).

    Args:
        learnings: List of learning dicts with ``summary`` and optionally ``detail``.

    Returns:
        Formatted hint string ≤ 60 tokens.
    """
    if not learnings:
        return '[TRW] No learnings found. Call trw_code(mode="hint") for more context.'

    lines: list[str] = ["[TRW Distill Hint — T1]"]
    for learning in learnings[:2]:
        summary = str(learning.get("summary", ""))
        if summary:
            lines.append(f"  - {summary[:80]}")
    lines.append('  Call trw_code(mode="hint") for full context.')

    return "\n".join(lines)


class HintLesson(Protocol):
    """The three lesson fields T2 renders (``EditLessonPayload`` satisfies it)."""

    @property
    def id(self) -> str: ...
    @property
    def sha(self) -> str: ...
    @property
    def summary(self) -> str: ...


#: T2 without lessons keeps its original budget (FR32: ~320 chars ≈ 80 tokens).
_T2_BASE_MAX_CHARS: int = 320
#: PRD-DIST-2482 NFR01: the lesson block alone is <= 120 tokens (ceil(chars/4)),
#: the budget trw-distill's ``fit_budget`` already applies to the sidecar.
_T2_LESSON_MAX_CHARS: int = 480
#: A T2 hint used to drop the T1 recall memory entirely once distill lessons
#: took the ``lessons`` slot above -- an edit with 5 matching learnings and a
#: T2 result showed the agent none of them (measured on a real-hook e2e).
#: The recall block gets no budget of its own: it takes whatever room the
#: distill content leaves inside ``_T2_MAX_CHARS`` (the hint fires on every
#: edit, so the total is a design constraint), at most 2 summaries of
#: ``_T2_RECALL_LESSON_MAX_CHARS`` each, same bullet style as ``format_t1_hint``.
_T2_RECALL_MAX_LESSONS: int = 2
#: Per-lesson cap before the remaining room is even considered (codex review
#: e72d76ac2 r1): recall summaries are stored content, not distill's own
#: citations, so nothing bounded their length before this.
_T2_RECALL_LESSON_MAX_CHARS: int = 120
#: Whole T2 output: 320 + 480 = 800 chars = 200 tokens (PRD-DIST-2482 NFR01).
#: Raised from 320 only by the lesson block; recall memory fits in what is left.
_T2_MAX_CHARS: int = _T2_BASE_MAX_CHARS + _T2_LESSON_MAX_CHARS

#: SUB-BLAST-RADIUS (2026-10-02): a file with MORE importers than this is a shared module, and its T2
#: hint adds one line naming the dependents and the impact-selected test command. 25 sits above
#: distill's own "non-trivial fan-in" floor (10, a WARN only) so the line marks real hubs: the
#: incident file, trw-mcp/tests/_layout.py, had 173 importers; a typical src module has under 10.
SHARED_MODULE_MIN_IMPORTERS: int = 25
#: The importer count as trw-distill's hotspot warnings print it ("non-trivial fan-in (N importers)",
#: "high fan-in hotspot (N importers) ..."): the hint already carries the count, so it is read here
#: rather than recomputed (the ``importers`` list itself is capped at 20).
_IMPORTERS_COUNT = re.compile(r"\((\d+) importers\)")
#: The one-command dependents check (runs the impact-selected tests), where the project has it (TRW's own
#: repo). Elsewhere the line names distill's importer query, so the hint never points at a missing script.
_DEPENDENTS_SCRIPT = "scripts/check_dependents.py"
#: The shared-module line's own cap (codex r1): past it the path is replaced by a placeholder, so a deep
#: path cannot push the whole hint past ``_T2_MAX_CHARS`` (the line's room comes from the lesson budget).
_SHARED_LINE_MAX_CHARS = 200

#: Lesson statuses meaning the check did not finish. An empty list under these
#: proves nothing, so T2 says so rather than implying "no lessons".
# trw:intentional an unfinished lesson check must never render as "no lessons"
_LESSONS_UNCHECKED: dict[str, str] = {
    "daemon_unavailable": "  LESSONS: not checked (memory daemon unavailable)",
    "page_cap_reached": "  LESSONS: scan incomplete (page cap), more may exist",
}


#: C0, DEL, C1 and the Unicode line/paragraph separators. Lesson text is stored
#: memory content injected into the agent's context verbatim, so a newline or an
#: escape sequence inside it could forge further hint lines or instructions.
_CONTROL_CHARS = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_WHITESPACE_RUN = re.compile(r"\s+")
#: Lesson ids and commit shas are identifiers; anything else is not rendered.
_LESSON_TOKEN = re.compile(r"[A-Za-z0-9_-]+")


def _one_line(text: str) -> str:
    """*text* with every control character replaced by a space and whitespace collapsed."""
    return _WHITESPACE_RUN.sub(" ", _CONTROL_CHARS.sub(" ", text)).strip()


def _render_lesson(lesson: HintLesson) -> str | None:
    """One sanitized ``LESSON`` line, or None when the id or sha is not an identifier."""
    lesson_id, sha = _one_line(lesson.id), _one_line(lesson.sha)
    if not (_LESSON_TOKEN.fullmatch(lesson_id) and _LESSON_TOKEN.fullmatch(sha)):
        return None
    return f"  LESSON {sha} {lesson_id}: {_one_line(lesson.summary)}"


def _lesson_lines(lessons: Sequence[HintLesson], lessons_status: str | None, reserved: int = 0) -> list[str]:
    """``LESSON <sha> <id>: <summary>`` lines in the lesson budget; lesson 2 goes before lesson 1 is cut.

    *reserved* is what a shared-module line already took from the lesson budget (the total stays 800).
    """
    budget = _T2_LESSON_MAX_CHARS - 1 - reserved  # the newline that joins the block to the base
    # Sanitize BEFORE the budget, so the budget measures what is actually printed.
    rendered = [line for line in map(_render_lesson, lessons[:2]) if line is not None]
    note = [_LESSONS_UNCHECKED[lessons_status]] if lessons_status in _LESSONS_UNCHECKED else []
    if len(rendered) > 1 and len("\n".join(rendered + note)) > budget:
        rendered = rendered[:1]
    if rendered:
        room = budget - len("\n".join(note)) - (1 if note else 0)
        if room < len("  LESSON ") + 3:
            rendered = []
        elif len(rendered[0]) > room:
            rendered = [rendered[0][: room - 3] + "..."]
    return rendered + note


def shared_module_line(file_path: str, hotspot_warnings: Sequence[str]) -> str | None:
    """The blast-radius line for a file with more than ``SHARED_MODULE_MIN_IMPORTERS`` importers, else None."""
    counts = [int(m.group(1)) for warn in hotspot_warnings if (m := _IMPORTERS_COUNT.search(warn))]
    if not counts or max(counts) <= SHARED_MODULE_MIN_IMPORTERS:
        return None
    root = os.environ.get("TRW_REPO_ROOT") or os.environ.get("TRW_PROJECT_ROOT")
    if root and (Path(root) / _DEPENDENTS_SCRIPT).is_file():
        verb = f"run: python {_DEPENDENTS_SCRIPT}"
    else:
        verb = "find them: trw-distill query importers"
    head = f"  shared module: {max(counts)} dependents; prefer fixing the caller or moving the artifact; {verb}"
    # Quoted (codex r1): the path is model-controlled and the line is a command meant to be pasted.
    line = f"{head} {shlex.quote(_one_line(file_path))}"
    return line if len(line) <= _SHARED_LINE_MAX_CHARS else f"{head} <this file>"


def _recall_lesson_lines(recall_learnings: Sequence[dict[str, Any]], room: int) -> list[str]:
    """Up to ``_T2_RECALL_MAX_LESSONS`` T1-style bullet lines from memory recall.

    Reuses ``format_t1_hint``'s own bullet formatting (``- summary``) so a T2
    hint that reports distill risk still surfaces recall memory the same way a
    T1-only hint would, instead of silently dropping it (PRD-DIST-2482
    follow-up). Returns ``[]`` when there is nothing to show — no header for
    an empty block.

    Recall summaries are stored memory content, not distill's own citations, so
    two things a distill lesson gets for free do not hold for them (codex
    review e72d76ac2 r1):

    - Sanitized with ``_one_line`` before rendering, same as ``_render_lesson``:
      unsanitized, a summary containing a newline could forge additional
      ``[TRW ...]``/``LESSON``/``MEMORY:`` lines the agent would read as part
      of the hint's own structure rather than as quoted recall content.
    - Bounded to ``_T2_RECALL_LESSON_MAX_CHARS`` per lesson AND to *room*, the
      characters the rest of the hint leaves inside ``_T2_MAX_CHARS`` (header
      included): lessons are dropped from the end, not just truncated, so the
      whole hint never exceeds its budget however many long summaries arrive.
    """
    budget = room - 1  # the newline that joins the block to the rest
    rendered: list[str] = []
    for learning in recall_learnings[:_T2_RECALL_MAX_LESSONS]:
        summary = _one_line(str(learning.get("summary", "")))
        if not summary:
            continue
        if len(summary) > _T2_RECALL_LESSON_MAX_CHARS:
            summary = summary[: _T2_RECALL_LESSON_MAX_CHARS - 3] + "..."
        rendered.append(f"  - {summary}")
    while rendered and len("\n".join(["  MEMORY:", *rendered])) > budget:
        rendered.pop()
    if not rendered:
        return []
    return ["  MEMORY:", *rendered]


class HintAsOf(Protocol):
    """Where a stale T2 hint came from (``SidecarAsOf`` in the hint core)."""

    @property
    def sidecar_sha(self) -> str: ...

    @property
    def commits_behind(self) -> int: ...


def as_of_line(as_of: HintAsOf) -> str:
    """The AS-OF label of a stale T2 hint; it names the flag that produced it."""
    unit = "commit" if as_of.commits_behind == 1 else "commits"
    return (
        f"  AS-OF: {as_of.sidecar_sha[:9]}, {as_of.commits_behind} {unit} behind HEAD; "
        "historical, not current (hint_sidecar_ancestor_enabled)"
    )


def format_t2_hint(
    *,
    file_path: str,
    risk_score: float | None,
    hotspot_warnings: list[str],
    co_change_neighbors: list[str],
    inferred_tests: list[str],
    lessons: Sequence[HintLesson] = (),
    lessons_status: str | None = None,
    as_of: HintAsOf | None = None,
    recall_learnings: Sequence[dict[str, Any]] = (),
) -> str:
    """Format T2 hint output (<= 200 tokens / 800 chars; <= 80 tokens without lessons).

    Args:
        file_path: The file being edited.
        risk_score: Distill risk score (0.0-1.0) or None.
        hotspot_warnings: List of hotspot warning strings.
        co_change_neighbors: Related files that co-change.
        inferred_tests: Inferred test file paths.
        lessons: Up to 2 distilled lessons citing the file (PRD-DIST-2482).
        lessons_status: The sidecar's lesson-read status. An unfinished check
            is stated as such; ``none_cited`` and ``None`` print nothing.
        as_of: Set for a ``hint_available_stale`` hint: an AS-OF line follows
            the header, which itself stays byte-identical (the hook matches it).
        recall_learnings: T1 memory-recall learnings (``{"summary": ...}`` dicts,
            same shape ``format_t1_hint`` takes). A T2 result used to show
            distill's own ``lessons`` and silently drop these even when recall
            found matches; up to ``_T2_RECALL_MAX_LESSONS`` are appended in the room left.

    Returns:
        Formatted hint string.
    """
    lines: list[str] = ["[TRW Distill Hint — T2]"]
    if as_of is not None:
        lines.append(as_of_line(as_of))

    if risk_score is not None:
        lines.append(f"  RISK: {risk_score:.2f}")

    lines.extend(f"  WARN: {warn[:60]}" for warn in hotspot_warnings[:3])

    if co_change_neighbors:
        neighbors_str = ", ".join(co_change_neighbors[:2])
        lines.append(f"  CO-CHANGE: {neighbors_str}")

    if inferred_tests:
        lines.append(f"  TESTS: {inferred_tests[0]}")

    content = "\n".join(lines)

    # Hard cap on the lesson-free part (FR32: ~320 chars ≈ 80 tokens).
    if len(content) > _T2_BASE_MAX_CHARS:
        content = content[: _T2_BASE_MAX_CHARS - 3] + "..."

    # The shared-module line follows the base cap, so the cap never cuts its command; it takes its
    # room from the lesson budget. Lessons go last, after the base cap, so that cap never cuts one
    # mid-line; recall memory follows the distill lessons, in its own separate budget.
    shared = shared_module_line(file_path, hotspot_warnings)
    if shared is not None:
        content = f"{content}\n{shared}"
    reserved = len(shared) + 1 if shared is not None else 0
    head = "\n".join([content, *_lesson_lines(lessons, lessons_status, reserved)])
    return "\n".join([head, *_recall_lesson_lines(recall_learnings, _T2_MAX_CHARS - len(head))])


def write_hint_file(
    *,
    hints_dir: Path,
    tool_use_id: str,
    file_path: str,
    tier: str,
    hint_emitted: bool,
    tokens_emitted: int,
    distill_status: str,
    duration_ms: float | None = None,
    sidecar_commits_behind: int | None = None,
    target_changed_since_sidecar: bool | None = None,
) -> None:
    """Write a per-hint context file keyed on *tool_use_id* (P1-04 fix).

    Args:
        hints_dir: Directory to write hint files into (created if absent). It is the
            checkout context directory: symlinked parents and leaves are refused.
        tool_use_id: The PreToolUse tool_use_id from Claude Code stdin.
        file_path: Absolute path of the file being hinted.
        tier: Tier string ("T0", "T1", "T2").
        hint_emitted: Whether a hint was actually emitted.
        tokens_emitted: Estimated token count of the hint.
        distill_status: Distill status string from BeforeEditHintResult.
        duration_ms: In-process wall time of the hint computation, measured by
            the caller (8.2 S3). ``None`` when the computation never ran or
            never finished (e.g. the caller wrote a provisional/timeout record).
        sidecar_commits_behind: ``BeforeEditHintResult.distill_as_of.commits_behind``,
            or ``None`` when the answer was not an ancestor ("as of") hint.
        target_changed_since_sidecar: ``BeforeEditHintResult.distill_as_of.target_changed``,
            or ``None`` for the same reason.
    """
    hint_file = hints_dir / f"{tool_use_id}.json"
    record = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "file_path": file_path,
        "tier": tier,
        "hint_emitted": hint_emitted,
        "tokens_emitted": tokens_emitted,
        "distill_status": distill_status,
        "tool_use_id": tool_use_id,
        # 8.2 S3: measured in-process, never guessed. `None` (-> JSON null) is
        # "not applicable"/"not measured", distinct from a fabricated 0.
        "duration_ms": duration_ms,
        "sidecar_commits_behind": sidecar_commits_behind,
        "target_changed_since_sidecar": target_changed_since_sidecar,
        # PRD-DIST-2460 FR-1 (Phase 3 instrumentation): outcome vocabulary for a FUTURE
        # consumer-outcome feedback loop. Defaulted/unknown at PreToolUse write time; a later
        # PostToolUse correlator (DEFERRED FR-2..6, a separate proprietary trw-distill PRD gated on
        # an N>=30 corpus) would populate them. Additive + backward-compatible: pre-existing hint
        # files lacking these keys still parse, and no current consumer regresses.
        "outcome_captured": False,
        "was_edited": None,
        "edit_survived": None,
        "test_outcome": "unknown",
        "hint_acknowledged": None,
    }
    root = hints_dir.parents[2] if hints_dir.parts[-3:] == (".trw", "context", "cc03-hints") else hints_dir.parent
    write_checkout_file(root, hint_file, json.dumps(record), mode=0o600)


def prune_hint_files(hints_dir: Path, ttl_seconds: int = _HINT_FILE_TTL_SECONDS) -> int:
    """Remove hint files older than *ttl_seconds* from *hints_dir*.

    Args:
        hints_dir: Directory containing per-hint context files.
        ttl_seconds: Maximum age in seconds before pruning.

    Returns:
        Number of files removed.
    """
    if not hints_dir.exists():
        return 0

    now = time.time()
    removed = 0
    for hint_file in hints_dir.glob("*.json"):
        try:
            if (now - hint_file.stat().st_mtime) > ttl_seconds:
                hint_file.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass

    return removed


def sidecar_remedy_marker(repo: Path, session_id: str) -> Path:
    """The receipt the shell writes only after successful output delivery."""
    key = hashlib.sha256(session_id.encode()).hexdigest()
    return repo / CC03_HINTS_DIR / f"sidecar-{key}.seen"


def mark_sidecar_remedy_delivered(repo: Path, session_id: str) -> None:
    """Record successful delivery and prune receipts older than seven days."""
    from trw_mcp.state._surface_role import reviewer_role_active

    if reviewer_role_active() or not session_id:
        return
    marker = sidecar_remedy_marker(repo, session_id)
    write_checkout_file(repo, marker, "delivered\n", mode=0o600)
    cutoff = time.time() - 7 * 86400
    for old in marker.parent.glob("sidecar-*.seen"):
        try:
            if not old.is_symlink() and old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        except OSError:
            log.debug("sidecar_receipt_prune_failed", exc_info=True)


def sidecar_remedy_once(repo: Path, session_id: str, status: str, action: str | None) -> str:
    """Format an undelivered session notice without writing any state.

    The shell owns the delivery receipt; a timed-out computation must not consume it.
    """
    states = {
        "sidecar_missing": "missing",
        "sidecar_too_far_behind": "too far behind HEAD",
        "target_not_in_sidecar": "does not cover this file",
        "sidecar_malformed": "unreadable",
        "schema_mismatch": "built by an older version",
        "stale_sha": "out of date",
    }
    # The common case first: a usable sidecar pays for no import and no role lookup on the edit path.
    if not session_id or status not in states:
        return ""
    from trw_mcp.state._surface_role import reviewer_role_active

    if reviewer_role_active():
        return ""
    if sidecar_remedy_marker(repo, session_id).exists():
        return ""
    match = re.search(r"nearest is (\d+) commits behind", action or "")
    state = f"{match[1]} commits behind" if match else states[status]
    from trw_mcp.tools._sidecar_ancestry import shared_cache_dir

    root = repo.resolve()
    cache = shared_cache_dir(root, ".trw/distill/map-cache").absolute()
    upgrade = "upgrade trw-distill, then " if status == "schema_mismatch" else ""
    return (
        f"[TRW] Sidecar {state}; {upgrade}run: trw-distill self-improve refresh-sidecars "
        f"--repo {shlex.quote(str(root))} --cache-dir {shlex.quote(str(cache))}"
    )
