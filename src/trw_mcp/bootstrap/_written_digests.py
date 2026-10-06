"""What TRW itself last wrote to each transaction-surface file, so its own untracked output is not a user edit.

``update-project`` keeps a file git reports uncommitted unless it can prove the bytes are TRW's (PRD-INFRA-190
FR04). Hooks, skills and agents carry that proof in ``content_hashes``; ``.trw/config.yaml``, the channel manifest,
``.cursor/rules/*.mdc`` and ``.codex/hooks.json`` carried none. In a repository with no commits every file is
untracked, so every update "kept" TRW's own output, warned about it on every run, and left those files stale
(three doctor FAILs in one project; ``target_platforms`` never recorded in another).

The record holds a file only when its final bytes are exactly the bytes this run wrote (the run's write ledger,
:func:`trw_mcp._checkout_write.recording_writes`). A file a run kept for the user, or merged around, is not
recorded unless TRW produced those exact bytes; any later edit changes the bytes and the match fails, so a real
user edit stays protected (HB-2). An unreadable or malformed record proves nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, run_writes, write_checkout_file

logger = structlog.get_logger(__name__)

RECORD_RELPATH = ".trw/runtime/written-digests.json"


def _sha(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # trw-fail-silent-allow: no digest proves nothing, so the file stays protected
        return None


def load_written_digests(root: Path) -> dict[str, str]:
    """``{repo-relative path: sha256}`` TRW last wrote; empty when the record is absent, unreadable or malformed."""
    try:
        data = json.loads((root / RECORD_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # trw-fail-silent-allow: an absent or broken record proves nothing (fail closed)
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    return {str(rel): str(digest) for rel, digest in files.items() if isinstance(digest, str)}


def trw_wrote_these_bytes(root: Path, rel: str, path: Path) -> bool:
    """True when *path* holds exactly the bytes TRW last recorded writing to *rel*."""
    recorded = load_written_digests(root).get(rel)
    return recorded is not None and _sha(path) == recorded


def record_written_digests(root: Path, result: dict[str, list[str]]) -> None:
    """Record every surface file whose final bytes are this run's own write; keep older entries still matching.

    Reads the open write ledger (``recording_writes``). A run that already failed records nothing: its rollback
    puts the previous bytes back, which the previous record still describes. Fail-open: a record that cannot be
    written is a warning, and the next run simply keeps those files as before.
    """
    from ._update_phases import _RUN_RECORDS
    from ._update_transaction import _is_surface_path

    if result.get("errors"):
        return

    real_root = os.path.realpath(root)
    files = {rel: digest for rel, digest in load_written_digests(root).items() if _sha(root / rel) == digest}
    for real_path, digest in run_writes().items():
        rel = os.path.relpath(real_path, real_root)
        if rel.startswith("..") or os.path.isabs(rel):
            continue
        rel = Path(rel).as_posix()
        # A run record (installer-meta.yaml) is restored after this when nothing else changed; it has its own proof.
        if _is_surface_path(rel) and rel not in _RUN_RECORDS and _sha(root / rel) == digest:
            files[rel] = digest
    if files == load_written_digests(root) and (root / RECORD_RELPATH).is_file():
        return  # nothing new: a no-op update leaves the record's bytes alone too
    payload = json.dumps({"version": 1, "files": dict(sorted(files.items()))}, indent=1) + "\n"
    try:
        write_checkout_file(root, root / RECORD_RELPATH, payload)
    except (OSError, UnsafeWriteError, ValueError) as exc:  # trw-fail-silent-allow: reported as a warning
        logger.warning("written_digests_unrecorded", error=str(exc))
        result.setdefault("warnings", []).append(f"{RECORD_RELPATH}: not written ({type(exc).__name__}: {exc})")
