"""The anomaly detector's persisted state: the shadow clock and the write-failure policy.

Belongs to the ``anomaly_detector.py`` facade, which re-exports these names. Split out at
the persisted-state seam (2026-09-26, AGY-SANDBOX-WRITE-FAILS) to keep the facade under the
350 effective-LOC gate while adding the best-effort write policy.
"""

from __future__ import annotations

import errno
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
import yaml

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file

logger = structlog.get_logger(__name__)

SHADOW_WINDOW_DAYS = 21


def state_persistable(root: Path | None, persist_state: bool) -> bool:
    """Whether the detector may touch its state files at all (CORE-337-D).

    Only beneath a project root, and only where every read and write is descriptor-anchored and no-follow
    (``trw_memory.safe_fs``'s POSIX branch). Elsewhere -- no root, or the by-path Windows fallback, which is not
    race-safe -- the detector keeps its state in memory.
    """
    from trw_memory.safe_fs import anchored_removal_supported

    return persist_state and root is not None and anchored_removal_supported()


def read_state_file(root: Path, path: Path) -> str | None:
    """*path* (beneath *root*) read without following a link below *root*; ``None`` when it is absent.

    Opened through ``_checkout_access.open_under`` (every component ``O_NOFOLLOW`` off its parent's descriptor).
    Raises ``OSError`` for a symlinked directory or leaf, or a non-regular leaf (a FIFO is opened non-blocking,
    then refused), and ``UnicodeDecodeError`` for bytes that are not UTF-8.
    """
    from trw_mcp._checkout_access import open_under

    try:
        fd = open_under(root, path.relative_to(root).as_posix())
    except FileNotFoundError:  # trw-fail-silent-allow: absent is this reader's documented None, not an error
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        chunks: list[bytes] = []
        while chunk := os.read(fd, 1 << 16):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(fd)


def _ensure_shadow_clock(path: Path, *, root: Path, now: datetime | None = None) -> dict[str, str]:
    """Idempotent shadow-clock bootstrap at ``path`` beneath *root* (Deliverable #7).

    Writes ``{started_at, phase: "shadow", threshold_review_at}`` on first
    invocation; subsequent invocations return the existing contents.
    """
    now = now or datetime.now(tz=timezone.utc)
    try:
        text = read_state_file(root, path)
    except (
        OSError,
        ValueError,
        UnsafeWriteError,
    ):  # justified: boundary, a linked or unreadable clock is rewritten below (and that write refuses a link)
        text = ""
    if text is not None:
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError:  # justified: boundary, re-bootstrap on corrupt state rather than crash
            logger.warning(
                "mcp_shadow_clock_corrupt_rebootstrapping",
                path=str(path),
                outcome="rewriting",
            )
            raw = {}
        if isinstance(raw, dict) and "started_at" in raw:
            return {str(k): str(v) for k, v in raw.items()}

    payload = {
        "started_at": now.isoformat(),
        "phase": "shadow",
        "threshold_review_at": (now + timedelta(days=SHADOW_WINDOW_DAYS)).isoformat(),
    }
    try:
        # Beneath the project root (CORE-337-D): a symlinked .trw or .trw/security is refused too, not only the leaf;
        # the adapter creates missing directories without following a link.
        write_checkout_file(root, path, yaml.safe_dump(payload, sort_keys=True))
    except UnsafeWriteError:  # trw-fail-silent-allow: a planted symlink must not block every tool call through this middleware; the clock stays in memory, and the refusal is logged below and by safe_fs (PRD-CORE-337 FR08)
        logger.warning("mcp_shadow_clock_write_refused", path=str(path), outcome="in_memory_only")
        return payload
    logger.info(
        "mcp_shadow_clock_started",
        path=str(path),
        started_at=payload["started_at"],
        threshold_review_at=payload["threshold_review_at"],
        outcome="initialized",
    )
    return payload


def _state_unwritable(path: Path | None, exc: Exception) -> None:
    """The detector's own state is best-effort (AGY-SANDBOX-WRITE-FAILS): detection keeps running in memory."""
    logger.warning("mcp_anomaly_state_unwritable", path=str(path), error=str(exc), outcome="in_memory")
