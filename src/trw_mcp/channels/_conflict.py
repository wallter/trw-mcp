"""SHA-256 conflict detection + atomic write pattern for channel files.

Implements the P0-08 fix from the adversarial audit: render-log append
precedes the os.rename so crash-recovery logic can detect orphaned temp
writes.

PRD-DIST-2400 FR06, FR07.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.channels._manifest_models import HumanEditDetection, MarkersConfig

log = structlog.get_logger(__name__)

__all__ = [
    "RenderLog",
    "RenderLogEntry",
    "detect_human_edit",
    "reconcile",
    "write_atomic",
]

# ---------------------------------------------------------------------------
# Render-log entry model
# ---------------------------------------------------------------------------


class RenderLogEntry:
    """One line in the render-log JSONL file."""

    __slots__ = ("bytes_written", "channel_id", "sha", "target_path", "ts")

    def __init__(
        self,
        *,
        channel_id: str,
        target_path: Path,
        sha: str,
        ts: str,
        bytes_written: int,
    ) -> None:
        self.channel_id = channel_id
        self.target_path = target_path
        self.sha = sha
        self.ts = ts
        self.bytes_written = bytes_written

    def to_dict(self) -> dict[str, str | int]:
        return {
            "channel_id": self.channel_id,
            "target_path": str(self.target_path),
            "sha": self.sha,
            "ts": self.ts,
            "bytes_written": self.bytes_written,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> RenderLogEntry:
        raw_bytes = d.get("bytes_written", 0)
        bw = int(raw_bytes) if isinstance(raw_bytes, (int, float, str)) else 0
        return cls(
            channel_id=str(d["channel_id"]),
            target_path=Path(str(d["target_path"])),
            sha=str(d["sha"]),
            ts=str(d["ts"]),
            bytes_written=bw,
        )


# ---------------------------------------------------------------------------
# RenderLog — append-only JSONL log
# ---------------------------------------------------------------------------

_DEFAULT_RENDER_LOG = Path(".trw/channels/render-log.jsonl")


class RenderLog:
    """Append-only JSONL log tracking SHA-256 hashes of rendered channel files."""

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path: Path = log_path or _DEFAULT_RENDER_LOG

    @property
    def log_path(self) -> Path:
        return self._log_path

    def append(self, entry: RenderLogEntry) -> None:
        """Append one JSON line to the render log. Fail-open."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(entry.to_dict(), default=str) + "\n"
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception as exc:
            log.debug(
                "render_log_append_failed",
                log_path=str(self._log_path),
                error=str(exc),
                outcome="render_log_write_failed",
            )

    def last_for(
        self, channel_id: str, target_path: Path
    ) -> RenderLogEntry | None:
        """Return the last log entry for *(channel_id, target_path)*.

        Scans the log file from end to start (reversed). Returns None if not
        found or on any I/O error.
        """
        try:
            if not self._log_path.exists():
                return None
            target_str = str(target_path)
            lines = self._log_path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    d: dict[str, object] = json.loads(line)
                    if d.get("channel_id") == channel_id and d.get(
                        "target_path"
                    ) == target_str:
                        return RenderLogEntry.from_dict(d)
                except (json.JSONDecodeError, KeyError):
                    continue
        except Exception as exc:
            log.debug(
                "render_log_read_failed",
                log_path=str(self._log_path),
                error=str(exc),
                outcome="render_log_read_failed",
            )
        return None


# ---------------------------------------------------------------------------
# Helper — SHA-256 of bytes or str
# ---------------------------------------------------------------------------


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# detect_human_edit
# ---------------------------------------------------------------------------


def detect_human_edit(
    *,
    mode: HumanEditDetection,
    target_path: Path,
    expected_sha: str | None,
    markers: MarkersConfig | None = None,
) -> bool:
    """Return True if the file at *target_path* appears to have been edited.

    Returns False immediately when *expected_sha* is None (no prior baseline).

    Modes:
    - NONE: Always returns False.
    - RENDER_LOG: Hash full file; compare to expected_sha.
    - SHA256_SEGMENT: Hash content between markers; compare to expected_sha.
    - MARKER_BOUNDARY: True if any content exists between markers.
    - KEY_NAMESPACE: Parse JSON; hash ``servers.trw`` subtree; compare.
    """
    # Normalize mode — YAML loading with use_enum_values=True yields str
    mode_val: str = mode.value if isinstance(mode, HumanEditDetection) else str(mode)

    if mode_val == HumanEditDetection.NONE.value:
        return False

    if expected_sha is None:
        return False

    if not target_path.exists():
        return False

    try:
        if mode_val == HumanEditDetection.RENDER_LOG.value:
            file_bytes = target_path.read_bytes()
            actual = _sha256(file_bytes)
            return actual != expected_sha

        if mode_val == HumanEditDetection.SHA256_SEGMENT.value:
            if markers is None:
                return False
            seg_content = target_path.read_text(encoding="utf-8")
            interior = _extract_interior(seg_content, markers.start, markers.end)
            if interior is None:
                return False
            actual = _sha256(interior)
            return actual != expected_sha

        if mode_val == HumanEditDetection.MARKER_BOUNDARY.value:
            if markers is None:
                return False
            mb_content = target_path.read_text(encoding="utf-8")
            mb_interior = _extract_interior(mb_content, markers.start, markers.end)
            if mb_interior is None:
                return False
            return bool(mb_interior.strip())

        if mode_val == HumanEditDetection.KEY_NAMESPACE.value:
            raw = json.loads(target_path.read_text(encoding="utf-8"))
            subtree = _get_nested(raw, ["servers", "trw"])
            actual = _sha256(json.dumps(subtree, sort_keys=True, default=str))
            return actual != expected_sha

    except Exception as exc:
        log.debug(
            "detect_human_edit_error",
            mode=str(mode),
            target_path=str(target_path),
            error=str(exc),
            outcome="detection_failed",
        )

    return False


def _extract_interior(content: str, start: str, end: str) -> str | None:
    """Return text between *start* and *end* markers, or None if not found."""
    start_idx = content.find(start)
    if start_idx == -1:
        return None
    end_idx = content.find(end, start_idx + len(start))
    if end_idx == -1:
        return None
    return content[start_idx + len(start) : end_idx]


def _get_nested(obj: object, keys: list[str]) -> object:
    """Traverse nested dict with *keys*. Returns {} when missing."""
    cur: object = obj
    for k in keys:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(k, {})
    return cur


# ---------------------------------------------------------------------------
# write_atomic — P0-08 atomic write pattern
# ---------------------------------------------------------------------------


def write_atomic(
    target_path: Path,
    content: str,
    *,
    channel_id: str,
    render_log: RenderLog | None = None,
    sidecar_sha: str | None = None,
) -> RenderLogEntry:
    """Write *content* to *target_path* using the atomic write pattern.

    Sequence (P0-08 fix):
    1. Compute SHA-256 of content.
    2. Append render-log entry (channel_id, target_path, sha, ts, bytes).
    3. Write content to temp file in target_path.parent.
    4. os.rename(temp, target_path).

    If the process crashes between step 2 and step 4, ``reconcile()`` will
    detect the SHA mismatch on the next run and reset the log entry.

    Returns:
        The ``RenderLogEntry`` that was appended to the log.
    """
    if render_log is None:
        render_log = RenderLog()

    encoded = content.encode("utf-8")
    sha = _sha256(encoded)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    entry = RenderLogEntry(
        channel_id=channel_id,
        target_path=target_path,
        sha=sha,
        ts=ts,
        bytes_written=len(encoded),
    )

    # Step 2: log append BEFORE rename (P0-08 order)
    render_log.append(entry)

    # Step 3 + 4: atomic write
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        dir=target_path.parent,
        prefix=f".{target_path.name}.tmp.",
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
        os.rename(tmp_str, target_path)
    except Exception:
        try:
            os.unlink(tmp_str)
        except OSError:
            pass
        raise

    log.debug(
        "channel_write_atomic",
        channel_id=channel_id,
        target_path=str(target_path),
        sha=sha,
        bytes_written=len(encoded),
        outcome="ok",
    )
    return entry


# ---------------------------------------------------------------------------
# reconcile — crash-recovery
# ---------------------------------------------------------------------------


def reconcile(
    *,
    channel_id: str,
    target_path: Path,
    render_log: RenderLog,
) -> None:
    """Validate log entry SHA against actual file SHA; reset if mismatched.

    Called after a suspected crash-between-log-append-and-rename event.
    If the file on disk does not match the most recent log entry, appends a
    ``reconcile`` event line recording the discrepancy so subsequent
    ``detect_human_edit`` calls find a matching baseline.

    Does NOT modify the target file.
    """
    last = render_log.last_for(channel_id, target_path)
    if last is None:
        return

    try:
        actual_sha = _sha256(target_path.read_bytes())
    except FileNotFoundError:
        actual_sha = ""
    except Exception as exc:
        log.debug(
            "reconcile_read_error",
            target_path=str(target_path),
            error=str(exc),
            outcome="reconcile_skipped",
        )
        return

    if actual_sha == last.sha:
        return

    # Append reconcile marker so detect_human_edit uses the actual SHA next time
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    reconcile_entry = RenderLogEntry(
        channel_id=channel_id,
        target_path=target_path,
        sha=actual_sha,
        ts=ts,
        bytes_written=0,
    )
    reconcile_entry_dict: dict[str, str | int] = reconcile_entry.to_dict()
    reconcile_entry_dict["event"] = "reconcile"
    reconcile_entry_dict["recorded_sha"] = last.sha
    reconcile_entry_dict["actual_sha"] = actual_sha

    try:
        render_log.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(reconcile_entry_dict, default=str) + "\n"
        with open(render_log.log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception as exc:
        log.debug(
            "reconcile_log_append_failed",
            error=str(exc),
            outcome="reconcile_log_failed",
        )
        return

    log.debug(
        "channel_reconcile",
        channel_id=channel_id,
        target_path=str(target_path),
        recorded_sha=last.sha,
        actual_sha=actual_sha,
        outcome="reconcile_reset",
    )
