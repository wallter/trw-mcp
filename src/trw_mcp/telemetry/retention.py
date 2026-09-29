"""Local telemetry JSONL retention and compression helpers.

PRD-CORE-181-FR04: ``rotate_and_compress`` is the single atomic rotation
policy — size- and age-based, closed segments compress atomically with
read-back verification, the active writer file is never compressed in place,
referenced segments are preserved, and corruption is reported rather than
collected.
"""

from __future__ import annotations

import gzip
import time
from pathlib import Path

import structlog

from trw_mcp._locking import _lock_ex, _lock_un

logger = structlog.get_logger(__name__)


def rotate_and_compress(
    path: Path,
    *,
    max_bytes: int,
    min_age_seconds: float = 0.0,
    referenced: tuple[str, ...] = (),
    now: float | None = None,
) -> dict[str, object]:
    """One atomic rotation policy for a structured log (PRD-CORE-181-FR04).

    - Size rotation closes the active file into a numbered segment; the
      active writer file itself is NEVER compressed in place.
    - Closed, unreferenced segments older than ``min_age_seconds`` compress
      atomically: gzip to a tmp sibling, read-back verify, rename, then unlink
      the original. A crash leaves either the original or both — never neither.
    - Referenced or too-young segments are skipped with a reason.
    - A segment whose bytes cannot be read back identically is reported as
      corrupt and left untouched (report, never collect).
    """
    reference_time = time.time() if now is None else now
    compressed: list[str] = []
    skipped: list[dict[str, str]] = []
    corrupt: list[str] = []

    # Size rotation into the NEXT FREE numbered segment — an existing closed segment is
    # never clobbered, so closed data stays complete and ordered.
    rotated = False
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if path.exists():
        # trw:intentional cross-process advisory lock — hold the SAME exclusive
        # flock that ``FileStateWriter.append_jsonl`` takes on the active writer
        # file, so a concurrent append can never interleave with the
        # size-check → rename → touch that closes the active segment. Without it
        # an append landing mid-rotation is written into the segment being
        # rotated away and lost (CORE-181-NFR02). One OS process per MCP client
        # means an in-process lock is insufficient.
        with path.open("r+", encoding="utf-8") as active:
            _lock_ex(active.fileno())
            try:
                if path.stat().st_size > max_bytes:
                    used = set()
                    for sibling in path.parent.glob(path.name + ".*"):
                        first = sibling.name[len(path.name) + 1 :].split(".", 1)[0]
                        if first.isdigit():
                            used.add(int(first))
                    next_index = max(used, default=0) + 1
                    path.rename(path.with_name(f"{path.name}.{next_index}"))
                    path.touch()
                    rotated = True
            finally:
                _lock_un(active.fileno())

    for segment in sorted(path.parent.glob(path.name + ".*")):
        if segment.name.endswith(".gz"):
            continue
        if segment.name.endswith(".gz.tmp"):
            # Crash leftover from an interrupted compression: the original
            # segment still exists (unlink happens only after rename), so the
            # tmp is safely discardable.
            segment.unlink(missing_ok=True)
            continue
        if segment.name in referenced or str(segment) in referenced:
            skipped.append({"segment": segment.name, "reason": "referenced"})
            continue
        try:
            st = segment.stat()
        except OSError:
            corrupt.append(segment.name)
            continue
        if reference_time - st.st_mtime < min_age_seconds:
            skipped.append({"segment": segment.name, "reason": "too_young"})
            continue
        original = segment.read_bytes()
        tmp = segment.with_name(segment.name + ".gz.tmp")
        try:
            with gzip.open(tmp, "wb") as dst:
                dst.write(original)
            with gzip.open(tmp, "rb") as check:
                verified = check.read()
        except OSError:
            tmp.unlink(missing_ok=True)
            corrupt.append(segment.name)
            continue
        if verified != original:
            tmp.unlink(missing_ok=True)
            corrupt.append(segment.name)
            continue
        tmp.replace(segment.with_name(segment.name + ".gz"))
        # A writer that still held this segment open after the rotation append can extend it
        # while we compress; the archive then lacks those bytes, so keep the original (the
        # next run recompresses it over the archive) instead of unlinking unseen data.
        try:
            after = segment.stat()
            unchanged = (after.st_size, after.st_mtime_ns) == (st.st_size, st.st_mtime_ns)
        except OSError:
            unchanged = False
        if not unchanged:
            skipped.append({"segment": segment.name, "reason": "changed_during_compress"})
            continue
        segment.unlink()
        compressed.append(segment.name + ".gz")

    return {
        "rotated": rotated,
        "active_path": str(path),
        "compressed": compressed,
        "skipped": skipped,
        "corrupt": corrupt,
    }
