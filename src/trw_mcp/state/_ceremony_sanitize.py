# Parent facade: state/ceremony_feedback.py
"""Ceremony feedback sanitization and migration helpers.

Extracted from ``ceremony_feedback.py`` to keep the facade focused on
scoring, recording, and status reporting.  All public names are
re-exported from ``ceremony_feedback.py`` so existing import paths are
preserved.

FIX-050-FR07: Remove test-polluted entries from ceremony-feedback.yaml.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state.persistence import FileStateReader, FileStateWriter

logger = structlog.get_logger(__name__)


def _sanitize_flag_path(trw_dir: Path) -> Path:
    """Return the path to the sanitization idempotency flag file."""
    return trw_dir / "context" / ".sanitized_ceremony_v1"


def sanitize_ceremony_feedback(trw_dir: Path) -> dict[str, object]:
    """Remove test-polluted entries from ceremony-feedback.yaml (FIX-050-FR07).

    Removes entries where run_path contains '/tmp/' or 'pytest', or where
    session_id is one of the known test sentinel values.

    Uses a flag file to run only once (idempotent). The flag file is written
    to .trw/context/.sanitized_ceremony_v1 -- NOT a field in the YAML.

    Returns a dict with removed_count and skipped (if already run).
    """
    from trw_mcp.state.ceremony_feedback import _feedback_path

    flag_path = _sanitize_flag_path(trw_dir)
    if flag_path.exists():
        return {"skipped": True, "reason": "already_sanitized"}

    feedback_path = _feedback_path(trw_dir)
    if not feedback_path.exists():
        # Write flag so we don't check again
        writer = FileStateWriter()
        writer.ensure_dir(flag_path.parent)
        flag_path.touch()
        return {"removed_count": 0}

    reader = FileStateReader()
    writer = FileStateWriter()
    data = reader.read_yaml(feedback_path)

    if not isinstance(data, dict):
        flag_path.touch()
        return {"removed_count": 0}

    _TEST_SESSION_IDS = {"test", "gate-test", "advisory-test"}
    removed_count = 0

    task_classes = data.get("task_classes", {})
    if isinstance(task_classes, dict):
        for class_data in task_classes.values():
            if not isinstance(class_data, dict):
                continue
            sessions = class_data.get("sessions", [])
            if not isinstance(sessions, list):
                continue
            cleaned: list[dict[str, object]] = []
            for entry in sessions:
                if not isinstance(entry, dict):
                    cleaned.append(entry)
                    continue
                run_path = str(entry.get("run_path", ""))
                session_id = str(entry.get("session_id", ""))
                if "/tmp/" in run_path or "pytest" in run_path or session_id in _TEST_SESSION_IDS:  # noqa: S108 — string comparison to detect test-generated entries, not a file system path
                    removed_count += 1
                else:
                    cleaned.append(entry)
            class_data["sessions"] = cleaned

    writer.ensure_dir(feedback_path.parent)
    writer.write_yaml(feedback_path, data)

    # Write idempotency flag
    writer.ensure_dir(flag_path.parent)
    flag_path.touch()

    logger.info(
        "ceremony_feedback_sanitized",
        removed_count=removed_count,
        trw_dir=str(trw_dir),
    )
    return {"removed_count": removed_count}
