"""Local outbox for feedback submissions (FEEDBACK-LOCAL-OUTBOX).

Every attempted send is first written, already redacted and without the contact address, to
``.trw/feedback/outbox/<utc>-<sha8>.sending`` (mode 0600, through the containment check). A 200 moves it to
``sent/`` with the submission id; a failure renames it to ``.json`` with its attempt count so
``trw-mcp feedback flush`` can retry it. ``.sending`` is the claim: a flush takes a record only by renaming
``.json`` to ``.sending``, so two senders never hold the same record; a claim older than ``STALE_CLAIM``
(a crashed sender) goes back to pending at the next flush. ``feedback/`` carries its own ``.gitignore``
(``*``), written at the first enqueue, so no record is ever tracked even before init or update-project.

One submit is one send: nothing here resends on its own. Storage is best-effort — a refused or failed write is
logged and the send still happens, because losing the local copy must never block the report itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from trw_mcp._checkout_access import open_under
from trw_mcp.state._containment import trw_write_contained
from trw_mcp.state.persistence import write_text_atomic

logger = structlog.get_logger(__name__)

DUPLICATE_WINDOW = timedelta(days=7)
SENT_KEEP = 200
STALE_CLAIM = timedelta(minutes=15)
PENDING, CLAIMED = ".json", ".sending"
# A 200 whose id could not be parsed was still delivered: it is recorded as done, never "not yet delivered".
UNKNOWN_ID = "unknown-id"


def _dir(trw_dir: Path, sub: str) -> Path:
    return trw_dir / "feedback" / sub


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(path: Path, record: dict[str, Any]) -> bool:
    if not trw_write_contained(path):
        return False
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps(record, indent=2, sort_keys=True), mode=0o600)
    except OSError as exc:
        logger.warning("feedback_outbox_write_failed", error_type=type(exc).__name__, outcome="not_stored")
        return False
    return True


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:  # trw-fail-silent-allow: vanished or unstatable -- not a record; the caller skips it
        return False


def stem(path: Path) -> str:
    """The record id a path carries, whatever its state (``<id>.json`` or ``<id>.<pid>-<nonce>.sending``)."""
    return path.name.split(".", 1)[0]


def _claim_path(folder: Path, record_id: str) -> Path:
    # Per-claim nonce: a settle finishes only its own claim, never one another sender re-took.
    return folder / f"{record_id}.{os.getpid()}-{secrets.token_hex(4)}{CLAIMED}"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        # open_under: no component below .trw may be a symlink (a leaf or a folder swapped in after listing is
        # refused), and it opens O_NONBLOCK so a FIFO never hangs.
        anchor = path.parents[2]
        fd = open_under(anchor, str(path.relative_to(anchor)))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError(f"{path.name} is not a regular file")
            record = json.loads(handle.read())
    except (OSError, ValueError) as exc:  # trw-fail-silent-allow: an unreadable record is skipped, warned, left on disk
        logger.warning("feedback_outbox_unreadable", file=path.name, error_type=type(exc).__name__)
        return None
    # A record without an object payload is malformed: skipped here, so no reader has to guess its shape.
    if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
        logger.warning("feedback_outbox_malformed", file=path.name, outcome="skipped")
        return None
    return record


def _files(trw_dir: Path, sub: str) -> list[Path]:
    folder = _dir(trw_dir, sub)
    # A folder outside the project (a planted symlink) is neither read nor, later, written or deleted through.
    if not folder.is_dir() or not trw_write_contained(folder):
        return []
    # lstat first: a symlink, FIFO or device at a record path is never opened.
    return sorted(p for p in folder.iterdir() if p.suffix in (PENDING, CLAIMED) and _is_regular(p))


def records(trw_dir: Path, sub: str) -> list[tuple[Path, dict[str, Any]]]:
    """Readable records under ``feedback/<sub>`` (pending and claimed), oldest first (names start with the stamp)."""
    found = ((path, _read(path)) for path in _files(trw_dir, sub))
    return [(path, record) for path, record in found if record is not None]


def unreadable(trw_dir: Path, sub: str) -> list[str]:
    """Names of record files that could not be read, so a listing shows them instead of hiding them."""
    return [path.name for path in _files(trw_dir, sub) if _read(path) is None]


def _ensure_ignored(trw_dir: Path) -> None:
    ignore = trw_dir / "feedback" / ".gitignore"
    if ignore.exists() or not trw_write_contained(ignore):
        return
    try:
        ignore.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_text_atomic(ignore, "*\n", mode=0o644)
    except OSError as exc:
        logger.warning("feedback_outbox_gitignore_failed", error_type=type(exc).__name__)


def enqueue(trw_dir: Path, payload: dict[str, Any], *, contact_dropped: bool) -> Path | None:
    """Store the (already redacted) *payload*, claimed by this send; ``None`` when it could not be stored."""
    _ensure_ignored(trw_dir)
    now = _now()
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]
    record_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{digest}"
    record = {"id": record_id, "created_at": now.isoformat(), "attempts": 0, "last_error": "", "payload": payload}
    if contact_dropped:
        record["contact_email_dropped"] = True
    path = _claim_path(_dir(trw_dir, "outbox"), record_id)
    return path if _write(path, record) else None


def claim(path: Path) -> tuple[Path, dict[str, Any]] | None:
    """Take a pending record for one send by renaming it to ``.sending``; ``None`` when it is not to be sent."""
    claimed = _claim_path(path.parent, stem(path))
    try:
        os.rename(path, claimed)
        os.utime(claimed)  # the claim's age starts now
    except OSError:  # trw-fail-silent-allow: already claimed or gone -- another sender owns it
        return None
    record = _read(claimed)
    if record is not None and record.get("submission_id"):
        # Delivered after the caller listed it (checked again under the claim): hand it back, never resend.
        _rename(claimed, path)
        return None
    return None if record is None else (claimed, record)


def release_stale(trw_dir: Path) -> None:
    """Return claims older than ``STALE_CLAIM`` (a sender that died mid-send) to pending."""
    cutoff = time.time() - STALE_CLAIM.total_seconds()
    for path in _files(trw_dir, "outbox"):  # every claim, readable or not: a corrupt one is listed, not stuck
        try:
            if path.suffix == CLAIMED and os.lstat(path).st_mtime < cutoff:
                os.rename(path, path.parent / f"{stem(path)}{PENDING}")
        except OSError as exc:  # trw-fail-silent-allow: a racing sender moved it; nothing to release
            logger.warning("feedback_outbox_release_failed", file=path.name, error_type=type(exc).__name__)


def settle(path: Path, *, success: bool, submission_id: str, error: str, status_code: int) -> None:
    """Move a delivered claim to ``sent/``; otherwise count the attempt and return it to pending."""
    record = _read(path)
    if record is None:
        return
    attempts = record.get("attempts")
    record["attempts"] = (attempts if isinstance(attempts, int) else 0) + 1
    pending = path.parent / f"{stem(path)}{PENDING}"
    if not success:
        # The error's class only: a transport message can carry the URL, a server message the caller's text.
        record["last_error"] = f"HTTP {status_code}" if status_code else error.split(":", 1)[0]
        if _write(path, record):
            _rename(path, pending)
        return
    record.update(submission_id=submission_id or UNKNOWN_ID, sent_at=_now().isoformat(), last_error="")
    sent = _dir(path.parents[2], "sent") / f"{stem(path)}{PENDING}"
    if _write(sent, record):
        path.unlink(missing_ok=True)
        _prune_sent(sent.parent)
    elif _write(path, record):
        _rename(path, pending)  # flush skips a record that carries its submission_id
    elif trw_write_contained(path):
        # Delivered, but neither copy could record it: drop the claim so flush can never resend it.
        # Only inside the project: a refused (uncontained) path is never deleted through.
        logger.warning("feedback_outbox_delivered_unrecorded", file=path.name, outcome="pending_removed")
        path.unlink(missing_ok=True)


def _rename(src: Path, dst: Path) -> None:
    try:
        os.rename(src, dst)
    except OSError as exc:  # trw-fail-silent-allow: the claim stays; release_stale returns it to pending
        logger.warning("feedback_outbox_rename_failed", file=src.name, error_type=type(exc).__name__)


def _prune_sent(folder: Path) -> None:
    for old in sorted(folder.glob(f"*{PENDING}"))[:-SENT_KEEP]:
        old.unlink(missing_ok=True)


def find_duplicate(trw_dir: Path, category: str, subject: str) -> dict[str, str] | None:
    """The earlier report (pending or sent within the window) with this category and case-folded subject."""
    key = subject.strip().casefold()
    cutoff = _now() - DUPLICATE_WINDOW
    for sub in ("outbox", "sent"):
        for _path, record in records(trw_dir, sub):
            payload = record["payload"]
            if payload.get("category") != category or str(payload.get("subject", "")).strip().casefold() != key:
                continue
            try:
                created = datetime.fromisoformat(str(record.get("created_at")))
            except ValueError:
                # An unparseable stamp cannot be placed in the window: it is not a duplicate, and says so.
                logger.warning("feedback_outbox_bad_timestamp", file=_path.name, outcome="not_compared")
                continue
            if created.tzinfo is None:  # a naive stamp cannot be compared with the UTC window
                logger.warning("feedback_outbox_bad_timestamp", file=_path.name, outcome="not_compared")
                continue
            if created >= cutoff:
                prior = {"id": str(record.get("id", "")), "subject": str(payload.get("subject", ""))}
                # Anything in sent/ was delivered, whatever its id says.
                if record.get("submission_id") or sub == "sent":
                    prior["submission_id"] = str(record.get("submission_id") or UNKNOWN_ID)
                return prior
    return None


def summary(record: dict[str, Any]) -> dict[str, Any]:
    """The listing view of a record: never the message body."""
    payload = record.get("payload") or {}
    keys = ("id", "created_at", "attempts", "last_error", "submission_id", "sent_at")
    return {"category": payload.get("category", ""), "subject": payload.get("subject", ""),
            **{k: record[k] for k in keys if k in record}}  # fmt: skip


__all__ = ["DUPLICATE_WINDOW", "SENT_KEEP", "STALE_CLAIM", "UNKNOWN_ID", "claim", "enqueue", "find_duplicate",
           "records", "release_stale", "settle", "stem", "summary", "unreadable"]  # fmt: skip
