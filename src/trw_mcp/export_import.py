"""Import half of ``trw_mcp.export`` (E2E-INC-076): read an export file and store each learning through the
``trw_learn`` write path. Belongs to the ``export.py`` facade, which re-exports its public names."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import ImportLearningsResult, LearnResultDict
from trw_mcp.state._helpers import load_project_config as _load_project_config
from trw_mcp.state._project_root_binding import project_bound
from trw_mcp.state._store_selection import StoreUnavailableError
from trw_mcp.state.analytics import resync_learning_index
from trw_mcp.state.persistence import FileStateWriter

logger = structlog.get_logger(__name__)


def _parse_import_source(
    source_file: Path,
) -> tuple[list[dict[str, object]] | None, str | None, str]:
    """Parse source JSON and extract learnings + project name.

    Returns ``(entries, project, "")``, or ``(None, None, reason)`` where the reason says which failure it was: the
    file is missing, unreadable, not JSON (a CSV export is export-only), or JSON of the wrong shape.
    """
    try:
        text = source_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None, f"Source file not found: {source_file}"
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, f"Source file cannot be read: {source_file} ({type(exc).__name__})"
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.debug("import_parse_error", error=str(exc))
        return (
            None,
            None,
            f"Source file is not valid JSON (a CSV export is not importable; export with --format json): {exc}",
        )

    source_entries: object = None
    source_project: str | None = None

    if isinstance(raw, dict) and "learnings" in raw:
        source_entries = raw["learnings"]
        source_project = str(raw.get("metadata", {}).get("project", "unknown"))
    elif isinstance(raw, list):
        source_entries = raw
        source_project = "unknown"

    if isinstance(source_entries, list) and source_project:
        return source_entries, source_project, ""
    return None, None, "Source file must be a JSON list or export with 'learnings' key"


_IMPORTED_ID = "imported-id:"


def _content_digest(summary: object, detail: object) -> str:
    # A structured encoding: joining the fields with a newline made ("a\nb", "c") and ("a", "b\nc") one digest.
    return hashlib.sha256(json.dumps([str(summary).strip(), str(detail).strip()]).encode()).hexdigest()


class _Known:
    """What the project already holds, for recognising a re-import (E2E-INC-118).

    A re-import is recognised by exported id AND content, never by summary -- two distinct learnings may share a
    headline (the summary-similarity check dropped one of them). An exported id taken by DIFFERENT content is not a
    duplicate: it imports under a new id (E2E-INC-076). Keys are ``(id, content digest)``, so a native row and an
    earlier import that happen to share an id never overwrite each other.
    """

    def __init__(self) -> None:
        self.destination: dict[tuple[str, str], str] = {}
        #: Rows an earlier import created (matched through their ``imported-id:`` tag). Only these are repaired on a
        #: re-import; the project's own rows are never rewritten from a file.
        self.imported_copies: set[str] = set()
        self.status: dict[str, str] = {}
        self.superseded: set[str] = set()

    def add(self, key: tuple[str, str], learning_id: str, *, imported_copy: bool) -> None:
        if imported_copy and key in self.destination:  # the project's own row with this id and content wins
            return
        self.destination[key] = learning_id
        if imported_copy:
            self.imported_copies.add(learning_id)


def _known(trw_dir: Path) -> _Known:
    """Index every learning this project holds by its own id and by any earlier import's ``imported-id:`` tag."""
    from trw_mcp.state._constants import DEFAULT_LIST_LIMIT
    from trw_mcp.state._memory_transforms import _memory_to_learning_dict
    from trw_mcp.state._store_selection import selected_store
    from trw_mcp.state._tier_routing import USER_NAMESPACE

    store, project_namespace = selected_store(trw_dir)
    rows = []
    for namespace in (project_namespace, USER_NAMESPACE):
        limit = DEFAULT_LIST_LIMIT
        while len(entries := store.list_entries(namespace, limit=limit)) == limit:
            limit *= 2
        rows += [(entry, _memory_to_learning_dict(entry)) for entry in entries]
    known = _Known()
    for entry, row in rows:  # the project's own ids first: they win over an import's alias of the same id
        learning_id = str(entry.id)
        known.status[learning_id] = str(row.get("status", "active"))
        if row.get("superseded"):
            known.superseded.add(learning_id)
        known.add(
            (learning_id, _content_digest(row.get("summary", ""), row.get("detail", ""))),
            learning_id,
            imported_copy=False,
        )
    for entry, row in rows:
        digest = _content_digest(row.get("summary", ""), row.get("detail", ""))
        for tag in entry.tags:
            if tag.startswith(_IMPORTED_ID):
                known.add((tag[len(_IMPORTED_ID) :], digest), str(entry.id), imported_copy=True)
    return known


def _check_entry_filters(
    entry: dict[str, object],
    min_impact: float,
    tags: list[str] | None,
) -> tuple[bool, str]:
    """Check if entry passes impact and tag filters.

    Returns (should_import, skip_reason).
    """
    impact = float(str(entry.get("impact", 0)))
    if impact < min_impact:
        return False, "filter"

    if tags:
        entry_tags = entry.get("tags", [])
        if not isinstance(entry_tags, list):
            entry_tags = []
        entry_tag_strs = {str(t) for t in entry_tags}
        if not entry_tag_strs.intersection(tags):
            return False, "filter"

    return True, ""


_VALID_SOURCE_TYPES = frozenset({"human", "agent", "tool", "consolidated"})
_LEARNING_ID = re.compile(r"L-[A-Za-z0-9_-]{1,64}")


def _provenance_tags(entry: dict[str, object]) -> list[str]:
    """Tags recording what the exported row claimed, so a re-keyed import stays traceable to its origin.

    Import never forces the exported id (a forced id goes through the store's replace-on-key path, so an id that
    is taken, hidden or claimed concurrently would overwrite another learning) and never adopts the exported
    ``source_type`` (an unsigned file could claim ``human`` and be routed to the user tier). The originals ride as
    ``imported-id:<id>`` and ``imported-source:<type>`` tags instead.
    """
    tags: list[str] = []
    raw_id, raw_source = entry.get("id"), entry.get("source_type")
    if isinstance(raw_id, str) and _LEARNING_ID.fullmatch(raw_id):
        tags.append(f"{_IMPORTED_ID}{raw_id}")
    if isinstance(raw_source, str) and raw_source in _VALID_SOURCE_TYPES:
        tags.append(f"imported-source:{raw_source}")
    return tags


#: What a learning states about itself that ``trw_learn`` takes at creation (E2E-INC-118: all of it was dropped,
#: so every type came back ``pattern``). ``protection_tier`` is not adopted: like ``source_type`` (E2E-INC-076), an
#: unsigned file must not be able to grant a learning protection.
_TYPED_TEXT = (
    "type",
    "confidence",  # as admitted by _admitted_confidence: the import loop replaces an unsubstantiated "verified"
    "evidence_level",
    "nudge_line",
    "expires",
    "task_type",
    "phase_origin",
    "team_origin",
)
_TYPED_LISTS = ("domain", "phase_affinity")


def _typed_fields(entry: dict[str, object]) -> dict[str, object]:
    fields: dict[str, object] = {
        key: value for key in _TYPED_TEXT if isinstance(value := entry.get(key), str) and value
    }
    for key in _TYPED_LISTS:
        value = entry.get(key)
        if isinstance(value, list):
            fields[key] = [str(item) for item in value if isinstance(item, str)]
    return fields


def _assertion_claims(entry: dict[str, object]) -> list[dict[str, str]]:
    """Each exported assertion's claim (type, pattern, target). Its recorded verification result is not adopted:
    the target project re-verifies the claim against its own code."""
    raw = entry.get("assertions")
    return [
        {"type": item["type"], "pattern": str(item.get("pattern") or ""), "target": item["target"]}
        for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, dict) and isinstance(item.get("type"), str) and isinstance(item.get("target"), str)
    ]


#: What ``verified`` needs from a file: Observed/Verified evidence and a substantiating artifact the import can carry
#: (an evidence string or an assertion; anchors are generated by the target, never imported).
_VERIFIED_EVIDENCE_LEVELS = frozenset({"observed", "verified"})


def _admitted_confidence(entry: dict[str, object]) -> tuple[str | None, str | None]:
    """The confidence to import with, and a note when the file's own claim cannot be kept.

    E2E-INC-118 r1: forwarding ``verified`` from a file that cannot substantiate it made the store refuse the whole
    learning. It is imported at ``high`` instead, and the result says so.
    """
    confidence = entry.get("confidence")
    if not isinstance(confidence, str) or not confidence:
        return None, None
    if confidence != "verified":
        return confidence, None
    evidence = entry.get("evidence")
    has_artifact = bool(_assertion_claims(entry)) or (
        isinstance(evidence, list) and any(isinstance(item, str) and item.strip() for item in evidence)
    )
    level = entry.get("evidence_level")
    if has_artifact and isinstance(level, str) and level in _VERIFIED_EVIDENCE_LEVELS:
        return confidence, None
    return "high", f"{entry.get('id')}: confidence verified imported as high (the file does not substantiate it)"


def _store_entry(
    entry: dict[str, object],
    trw_dir: Path,
    config: TRWConfig,
    source_project: str | None,
) -> LearnResultDict:
    """Store one imported learning through the same write path as ``trw_learn``.

    The store write (and the acceptance gates in front of it: noise filter, content policy, dedup) is
    ``execute_learn``, so an imported learning is recallable and gets the same protections as a live one. It gets a
    NEW id and ``source_type="agent"`` (non-human provenance; ``trw_learn``'s write path coerces a ``tool`` source to ``agent``); the exported id and source claim are kept as tags (:func:`_provenance_tags`).
    """
    from trw_mcp.tools._learn_impl import execute_learn

    tags_obj = entry.get("tags", [])
    evidence_obj = entry.get("evidence", [])
    tags = [str(t) for t in tags_obj] if isinstance(tags_obj, list) else []
    return execute_learn(
        str(entry.get("summary", "")),
        str(entry.get("detail", "")),
        trw_dir,
        config,
        tags=[*tags, *(t for t in _provenance_tags(entry) if t not in tags)],
        evidence=[str(e) for e in evidence_obj] if isinstance(evidence_obj, list) else [],
        impact=float(str(entry.get("impact", 0))),
        source_type="agent",
        source_identity=source_project or "unknown",
        client_profile=str(entry.get("client_profile", "")),
        model_id=str(entry.get("model_id", "")),
        assertions=_assertion_claims(entry) or None,
        **_typed_fields(entry),  # type: ignore[arg-type]
    )


#: Statuses a new learning does not start with (E2E-INC-118: an obsolete or resolved learning came back active and
#: resurfaced in default recall).
_RESTORED_STATUSES = frozenset({"resolved", "obsolete"})


def _restore_status(trw_dir: Path, entry: dict[str, object], learning_id: str) -> list[str]:
    """Give *learning_id* the exported status, right after its write, so an interrupted import leaves none behind."""
    from trw_mcp.state._memory_update import update_learning

    status = entry.get("status")
    if not isinstance(status, str) or status not in _RESTORED_STATUSES:
        return []
    done = update_learning(trw_dir, learning_id, status=status)
    return [f"{learning_id}: status {status} not restored: {done['error']}"] if "error" in done else []


def _restore_supersession(
    trw_dir: Path,
    matched: list[tuple[dict[str, object], str]],
    destinations: dict[str, set[str]],
    repairable: set[str],
    superseded: set[str],
) -> list[str]:
    """Link each superseded learning to its closer, both by their ids in THIS project; report what cannot be linked.

    Runs over every exported learning this import matched, new or already here, so re-running an interrupted import
    completes its links. A prior that is already closed is left alone. Each prior is the row its own export entry
    became; a closer is linked only when its exported id names exactly one learning of this import (a file may carry
    different learnings under one id). Neither end may be a learning the project wrote itself: the link is written
    through the closer, so a native closer is reported, never corrected from a file.
    """
    from trw_mcp.state._memory_update import update_learning

    problems: list[str] = []
    for entry, prior in matched:
        closer = entry.get("invalidated_by")
        if not entry.get("superseded") or prior not in repairable or prior in superseded:
            continue
        closers = destinations.get(closer, set()) if isinstance(closer, str) else set()
        if len(closers) != 1:
            why = (
                f"which names {len(closers)} learnings in this import"
                if closers
                else "which this import does not contain"
            )
            problems.append(f"{prior}: superseded by {closer}, {why}")
            continue
        (closer_row,) = closers
        if closer_row not in repairable:
            problems.append(
                f"{prior}: superseded by {closer}, which is this project's own learning ({closer_row}); not changed"
            )
            continue
        done = update_learning(trw_dir, closer_row, supersedes=prior)
        if "error" in done:
            problems.append(f"{prior}: supersession by {closer} not restored: {done['error']}")
        else:
            superseded.add(prior)
    return problems


def import_learnings(
    source_file: Path,
    target_dir: Path,
    *,
    min_impact: float = 0.0,
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> ImportLearningsResult:
    """Import learnings from an export file into a target project.

    Args:
        source_file: Path to exported JSON (standalone list or full export).
        target_dir: Target project directory.
        min_impact: Minimum impact threshold for import.
        tags: Optional tag filter — only import entries with at least one matching tag.
        dry_run: If True, report what would be imported without writing.

    Returns:
        Dict with import counts and status.
    """
    trw_dir = target_dir / ".trw"
    if not trw_dir.is_dir():
        return {"error": f"No .trw directory found at {target_dir}", "status": "failed"}

    config = _load_project_config(trw_dir)

    source_entries, source_project, parse_error = _parse_import_source(source_file)
    if source_entries is None or source_project is None:
        return {"error": parse_error, "status": "failed"}

    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    FileStateWriter().ensure_dir(entries_dir)

    imported = 0
    skipped_filter = 0
    skipped_duplicate = 0
    refused: list[str] = []
    imported_ids: list[str] = []
    not_restored: list[str] = []
    matched: list[tuple[dict[str, object], str]] = []  # (export entry, its id in this project): new, or recognised
    destinations: dict[str, set[str]] = {}  # exported id -> the ids it became here (more than one: ambiguous)

    with project_bound(target_dir):
        try:
            known = _known(trw_dir)
        except StoreUnavailableError as exc:  # no memory store for this project: stop before anything is written
            return {"error": str(exc), "status": "failed"}
        for entry in source_entries:
            if not isinstance(entry, dict):
                continue

            should_import, skip_reason = _check_entry_filters(entry, min_impact, tags)
            if not should_import:
                if skip_reason == "filter":
                    skipped_filter += 1
                continue

            summary = str(entry.get("summary", "")).strip()
            if not summary:  # a nameless entry cannot be recalled or told apart: refuse it, say so
                refused.append("empty summary")
                continue
            exported_id = entry.get("id")
            key = (str(exported_id), _content_digest(summary, entry.get("detail", "")))
            existing = known.destination.get(key) if isinstance(exported_id, str) else None
            if existing is not None:  # this very learning is already here
                skipped_duplicate += 1
                destinations.setdefault(key[0], set()).add(existing)
                matched.append((entry, existing))
                if not dry_run and existing in known.imported_copies and known.status.get(existing) == "active":
                    not_restored += _restore_status(trw_dir, entry, existing)  # an interrupted import's leftover
                continue
            confidence, confidence_note = _admitted_confidence(entry)
            entry = {**entry, "confidence": confidence} if confidence else entry

            if dry_run:
                imported += 1
                continue

            try:
                outcome = _store_entry(entry, trw_dir, config, source_project)
            except (
                StoreUnavailableError
            ) as exc:  # no memory store for this project: stop, nothing half-imported silently
                return {"error": f"{exc} ({imported} imported before this)", "status": "failed"}
            status = outcome.get("status")
            if status == "recorded":
                learning_id = str(outcome.get("learning_id", ""))
                if isinstance(exported_id, str):
                    known.add(key, learning_id, imported_copy=True)
                    destinations.setdefault(key[0], set()).add(learning_id)
                matched.append((entry, learning_id))
                imported_ids.append(learning_id)
                imported += 1
                not_restored += [confidence_note] if confidence_note else []
                not_restored += _restore_status(trw_dir, entry, learning_id)
            elif status in (
                "skipped",
                "merged",
            ):  # dedup: the content is already in the store (merged into its survivor)
                skipped_duplicate += 1
            else:
                refused.append(str(outcome.get("reason") or outcome.get("message") or "rejected"))

        if not dry_run:
            not_restored += _restore_supersession(
                trw_dir, matched, destinations, known.imported_copies, known.superseded
            )
        if not dry_run and imported > 0:
            resync_learning_index(trw_dir)

    return {
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "skipped_filter": skipped_filter,
        "refused": len(refused),
        "refused_reasons": sorted(set(refused)),
        "total_source": len(source_entries),
        "imported_ids": imported_ids,
        "not_restored": not_restored,
        "dry_run": dry_run,
        "source_project": source_project,
        "status": "ok",
    }


def format_import_summary(result: ImportLearningsResult) -> str:
    """The one line ``import-learnings`` prints for a real run and for ``--dry-run``."""
    verb = (
        "would import (candidates; the store's acceptance gates run only on a real import)"
        if result.get("dry_run")
        else "imported"
    )
    refused = int(result.get("refused", 0))
    reasons = f" ({'; '.join(result.get('refused_reasons', []))})" if refused else ""
    not_restored = result.get("not_restored", [])
    kept = f"; not kept as exported: {'; '.join(not_restored)}" if not_restored else ""
    return (
        f"{verb} {result.get('imported', 0)} of {result.get('total_source', 0)} learnings from "
        f"{result.get('source_project', 'unknown')}: {result.get('skipped_duplicate', 0)} duplicate, "
        f"{result.get('skipped_filter', 0)} filtered, {refused} refused{reasons}{kept}"
    )
