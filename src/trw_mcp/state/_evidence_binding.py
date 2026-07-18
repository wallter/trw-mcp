"""Stable reads, scope minting, and content-binding construction — CORE-205 FR01.

State-layer persistence/validation primitive. Re-exported through the
``tools/_evidence_receipts.py`` facade (and a thin ``tools/_evidence_binding.py``
back-compat shim) for the tool layer, and imported directly by
``state/_trust_receipts.py`` so the state layer never imports from ``tools/``
(PRD-FIX-061-FR07 layer boundary).

This is the I/O layer beneath the pure models. It performs repository-confined
*stable reads* (detecting concurrent byte/type/symlink changes), mints the
authoritative :class:`RunOwnedScope` from the run's durable file-change journal,
and assembles a :class:`ContentBinding`. It uses no network, subprocess, or
repository-wide history scan (NFR03) and confines every path beneath the
server-resolved project root (NFR02).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models._evidence_core import (
    ContentBinding,
    ContentEntry,
    EntryState,
    EvidenceLimits,
    ReceiptState,
    RunOwnedScope,
    ScopeConfidence,
    compute_manifest_digest,
    compute_scope_digest,
)

logger = structlog.get_logger(__name__)

_STABLE_READ_RETRIES = 2  # FR01: retry twice; a third change -> unstable_read.
_READ_CHUNK = 1024 * 1024


class StableReadError(RuntimeError):
    """A path could not be stably/​safely read; carries a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class BindingOutcome:
    """Result of assembling a content binding for a scope."""

    binding: ContentBinding | None
    state: ReceiptState
    reason_code: str


def _relativize(project_root: Path, raw: str) -> str | None:
    """Return a normalized repository-relative path, or None when it escapes root."""
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (project_root / candidate).resolve()
        rel = resolved.relative_to(project_root.resolve())
    except (ValueError, OSError):
        return None
    normalized = rel.as_posix()
    if not normalized or normalized == "." or normalized.startswith(".."):
        return None
    return normalized


def _read_file_digest(fd: int, size: int) -> str:
    """Stream raw bytes of an already-open fd and return their SHA-256 hex."""
    hasher = hashlib.sha256()
    remaining = size
    with os.fdopen(os.dup(fd), "rb", closefd=True) as handle:
        handle.seek(0)
        while remaining > 0:
            chunk = handle.read(min(_READ_CHUNK, remaining))
            if not chunk:
                break
            hasher.update(chunk)
            remaining -= len(chunk)
    return hasher.hexdigest()


def _stat_signature(st: os.stat_result) -> tuple[int, int, int, int]:
    """Type/size/dev/inode + nanosecond mtime signature for stability comparison."""
    return (st.st_size, getattr(st, "st_dev", 0), getattr(st, "st_ino", 0), st.st_mtime_ns)


def _stable_read_file(abs_path: Path) -> ContentEntry:
    """Read a regular file's raw bytes under the FR01 stable-read protocol.

    lstat -> open (no-follow where supported) -> fstat before -> stream bytes ->
    fstat after -> lstat again, requiring an unchanged type/size/dev/inode/mtime.
    """
    open_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    fd = os.open(str(abs_path), open_flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise StableReadError("path_type_raced")
        if before.st_size > EvidenceLimits.MAX_BOUND_FILE_BYTES:
            raise StableReadError("bound_file_too_large")
        digest = _read_file_digest(fd, before.st_size)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    post = os.lstat(str(abs_path))
    if _stat_signature(before) != _stat_signature(after) or not stat.S_ISREG(post.st_mode):
        raise StableReadError("unstable_read")
    return ContentEntry(
        path="__placeholder__",  # replaced by caller with the repo-relative path
        state=EntryState.FILE,
        byte_digest=digest,
        byte_size=before.st_size,
    ).model_copy(update={})


def _stable_read_symlink(project_root: Path, abs_path: Path) -> ContentEntry:
    """Bind a symlink's raw target; reject broken/cyclic/escaping/re-targeted links."""
    target = os.readlink(str(abs_path))
    try:
        resolved = abs_path.resolve(strict=True)
    except (OSError, RuntimeError):
        # Broken (missing target) or cyclic symlink -> fail.
        raise StableReadError("symlink_broken_or_cyclic") from None
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError:
        raise StableReadError("symlink_escapes_root") from None
    # Re-target race: the link target must be unchanged after resolution.
    if os.readlink(str(abs_path)) != target:
        raise StableReadError("symlink_retargeted")
    return ContentEntry(path="__placeholder__", state=EntryState.SYMLINK, link_target=target)


def read_content_entry(project_root: Path, rel_path: str) -> ContentEntry:
    """Stable-read one repository-relative path into a :class:`ContentEntry`.

    Retries twice on an unstable read; a third instability raises
    ``StableReadError('unstable_read')`` and never returns a digest.
    """
    root = project_root.resolve()
    abs_path = (root / rel_path).resolve() if not Path(rel_path).is_absolute() else Path(rel_path)
    # Confinement: the resolved target must live beneath the project root.
    try:
        abs_path.relative_to(root)
    except ValueError:
        raise StableReadError("path_escapes_root") from None

    lst = _lstat_or_none(root / rel_path)
    if lst is None:
        return ContentEntry(path=rel_path, state=EntryState.DELETED)
    if stat.S_ISLNK(lst.st_mode):
        return _stable_read_symlink(root, root / rel_path).model_copy(update={"path": rel_path})

    last_error = "unstable_read"
    for _ in range(_STABLE_READ_RETRIES + 1):
        try:
            entry = _stable_read_file(root / rel_path)
            return entry.model_copy(update={"path": rel_path})
        except StableReadError as exc:
            last_error = exc.reason_code
            if exc.reason_code != "unstable_read":
                raise
        except FileNotFoundError:
            return ContentEntry(path=rel_path, state=EntryState.DELETED)
    raise StableReadError(last_error)


def _lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(str(path))
    except OSError:
        return None


def mint_run_owned_scope(
    run_path: Path | None,
    project_root: Path,
    *,
    scope_id: str,
    operator_paths: tuple[str, ...] = (),
    proposed_paths: tuple[str, ...] = (),
) -> RunOwnedScope:
    """Mint the authoritative scope from the run's file-change journal (FR01).

    Required paths come from ``file_modified`` events (``{"file": ...}``) in the
    run's ``events.jsonl`` plus explicit operator ownership. When no journal is
    readable the scope is returned with ``ScopeConfidence.UNVERIFIABLE`` and an
    empty required set — the caller must NOT mint positive evidence from it and
    must never substitute a whole-tree scan.
    """
    root = project_root.resolve()
    journal_paths, journal_ok = _read_journal_paths(run_path, root)
    required = tuple(sorted(set(journal_paths) | {p for p in operator_paths if _relativize(root, p)}))
    confidence = ScopeConfidence.VERIFIED if journal_ok else ScopeConfidence.UNVERIFIABLE
    project_identity = root.name
    return RunOwnedScope(
        scope_id=scope_id,
        scope_digest=compute_scope_digest(scope_id, project_identity, required),
        project_identity=project_identity,
        required_paths=required,
        proposed_paths=tuple(sorted({rel for p in proposed_paths if (rel := _relativize(root, p))})),
        provenance="run_journal" if not operator_paths else "mixed",
        confidence=confidence,
    )


def _read_journal_paths(run_path: Path | None, root: Path) -> tuple[list[str], bool]:
    """Return (repo-relative changed paths, journal_readable). Missing journal -> unverifiable."""
    if run_path is None:
        return [], False
    events_path = run_path / "meta" / "events.jsonl"
    if not events_path.exists():
        return [], False
    paths: set[str] = set()
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict) or str(ev.get("event", "")) != "file_modified":
                continue
            data = ev.get("data")
            raw: object = data.get("file") if isinstance(data, dict) and "file" in data else ev.get("file")
            if isinstance(raw, str) and raw:
                rel = _relativize(root, raw)
                if rel is not None:
                    paths.add(rel)
    except OSError:
        return [], False
    return sorted(paths), True


def build_content_binding(scope: RunOwnedScope, project_root: Path) -> BindingOutcome:
    """Assemble a :class:`ContentBinding` for a scope via stable reads (FR01).

    A ``scope_unverifiable`` scope yields no binding. An unstable/​escaping/​
    over-limit read yields the exact non-positive state and reason code.
    """
    if scope.confidence is ScopeConfidence.UNVERIFIABLE:
        return BindingOutcome(None, ReceiptState.SCOPE_UNVERIFIABLE, "scope_unverifiable")
    root = project_root.resolve()
    try:
        entry_tuple = tuple(read_content_entry(root, rel) for rel in scope.effective_paths)
    except StableReadError as exc:
        state = ReceiptState.UNSTABLE_READ if exc.reason_code == "unstable_read" else ReceiptState.INVALID
        return BindingOutcome(None, state, exc.reason_code)
    try:
        binding = ContentBinding(
            scope_id=scope.scope_id,
            scope_digest=scope.scope_digest,
            project_identity=scope.project_identity,
            entries=entry_tuple,
            manifest_digest=compute_manifest_digest(entry_tuple),
        )
    except ValueError as exc:
        return BindingOutcome(None, ReceiptState.INVALID, str(exc))
    return BindingOutcome(binding, ReceiptState.VALID, "ok")


def content_binding_is_current(binding: ContentBinding, project_root: Path) -> BindingOutcome:
    """Re-read the bound scope and compare against the recorded manifest (FR05).

    Returns ``VALID`` when current bytes match, ``STALE_CONTENT`` when a bound
    entry changed, and the exact non-positive state for an unstable/​unsafe read.
    Unrelated out-of-scope changes are never read, so they cannot invalidate.
    """
    root = project_root.resolve()
    try:
        current_entries = tuple(read_content_entry(root, entry.path) for entry in binding.entries)
    except StableReadError as exc:
        state = ReceiptState.UNSTABLE_READ if exc.reason_code == "unstable_read" else ReceiptState.INVALID
        return BindingOutcome(None, state, exc.reason_code)
    current_digest = compute_manifest_digest(current_entries)
    if current_digest != binding.manifest_digest:
        return BindingOutcome(None, ReceiptState.STALE_CONTENT, "bound_content_changed")
    return BindingOutcome(binding, ReceiptState.VALID, "ok")
