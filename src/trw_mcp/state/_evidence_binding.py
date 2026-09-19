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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models._evidence_core import (
    ContentBinding,
    ContentEntry,
    EvidenceLimits,
    ReceiptState,
    RunOwnedScope,
    ScopeConfidence,
    compute_manifest_digest,
    compute_scope_digest,
)
from trw_mcp.state._evidence_fs import StableReadError as StableReadError
from trw_mcp.state._evidence_fs import read_entry
from trw_mcp.state._evidence_identity import (
    ProjectIdentityError,
    project_identity_is_current,
    resolve_project_identity,
)

logger = structlog.get_logger(__name__)

_STABLE_READ_RETRIES = 2  # FR01: retry twice; a third change -> unstable_read.
_READ_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class BindingOutcome:
    """Result of assembling a content binding for a scope."""

    binding: ContentBinding | None
    state: ReceiptState
    reason_code: str


def _normalize_scope_path(project_root: Path, raw: str) -> tuple[str | None, bool]:
    """Preserve lexical ownership; return (path, instrumentation_well_formed).

    Proven outside-root records are irrelevant, not incomplete instrumentation.
    Never resolve the changed entry: a link's name, not its target, was changed.
    """
    if not raw or any(character in raw for character in ("\\", ":", "\x00")):
        return None, False
    components = raw.split("/")
    if raw.startswith("/"):
        components = components[1:]
    if any(not part for part in components):
        return None, False
    try:
        prefixes = (project_root.absolute(), project_root.resolve())
        if ".." in components:
            # Classify only: traversal is NEVER normalized into owned scope.
            lexical = Path(os.path.abspath(raw if raw.startswith("/") else project_root / raw))
            outside = all(not lexical.is_relative_to(prefix) for prefix in prefixes)
            return None, outside
        normalized = "/".join(part for part in components if part != ".")
        if not normalized:
            return None, False
        normalized = "/" + normalized if raw.startswith("/") else normalized
        candidate = Path(normalized)
        if candidate.is_absolute():
            matching_prefix = next((prefix for prefix in prefixes if candidate.is_relative_to(prefix)), None)
            if matching_prefix is None:
                return None, True
            relative = candidate.relative_to(matching_prefix).as_posix()
        else:
            relative = normalized
        if relative == "." or len(relative.encode("utf-8")) > EvidenceLimits.MAX_PATH_BYTES:
            return None, False
    except (ValueError, OSError, RuntimeError, UnicodeError):
        return None, False
    return relative, True


def _relativize(project_root: Path, raw: str) -> str | None:
    """Return only a valid lexical in-root path for additive proposed scope."""
    return _normalize_scope_path(project_root, raw)[0]


def _read_file_digest(fd: int, size: int) -> str:
    """Stream raw bytes of an already-open fd and return their SHA-256 hex."""
    hasher = hashlib.sha256()
    total = 0
    while total <= size:
        chunk = os.read(fd, min(_READ_CHUNK, size + 1 - total))
        if not chunk:
            if total != size:
                raise StableReadError("unstable_read")
            return hasher.hexdigest()
        hasher.update(chunk)
        total += len(chunk)
    raise StableReadError("unstable_read")


def read_content_entry(
    project_root: Path,
    rel_path: str,
    *,
    remaining_bytes: int | None = None,
    charge_read: Callable[[int], None] | None = None,
) -> ContentEntry:
    """Read confined content; retry at most twice when an observation races.

    The remaining manifest budget is checked before each attempted payload read.
    Aggregate callers also reserve attempted bytes through charge_read, across
    retries and paths. Their read allowance is three times the manifest ceiling
    plus bounded EOF probes; it never enlarges the accepted manifest budget.
    """
    for attempt in range(_STABLE_READ_RETRIES + 1):
        try:
            return read_entry(
                project_root,
                rel_path,
                digest_reader=_read_file_digest,
                remaining_bytes=remaining_bytes,
                charge_read=charge_read,
            )
        except StableReadError as exc:
            if exc.reason_code != "unstable_read" or attempt == _STABLE_READ_RETRIES:
                raise
    raise StableReadError("unstable_read")  # unreachable; explicit total contract


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
    try:
        project_identity = resolve_project_identity(project_root)
    except ProjectIdentityError:
        project_identity = ""
    journal_paths, journal_ok = _read_journal_paths(run_path, project_root)
    operator_entries = [_normalize_scope_path(project_root, path) for path in operator_paths]
    required = tuple(sorted(set(journal_paths) | {rel for rel, _ in operator_entries if rel is not None}))
    instrumentation_ok = (
        bool(project_identity)
        and journal_ok
        and all(valid for _, valid in operator_entries)
        and project_identity_is_current(project_identity, project_root)[0] is ReceiptState.VALID
    )
    confidence = ScopeConfidence.VERIFIED if instrumentation_ok else ScopeConfidence.UNVERIFIABLE
    return RunOwnedScope(
        scope_id=scope_id,
        scope_digest=compute_scope_digest(scope_id, project_identity, required),
        project_identity=project_identity,
        required_paths=required,
        proposed_paths=tuple(sorted({rel for p in proposed_paths if (rel := _relativize(project_root, p))})),
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
    journal_ok = True
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                journal_ok = False
                continue
            if not isinstance(ev, dict):
                journal_ok = False
                continue
            if str(ev.get("event", "")) != "file_modified":
                continue
            data = ev.get("data")
            raw: object = data.get("file") if isinstance(data, dict) and "file" in data else ev.get("file")
            if not isinstance(raw, str) or not raw:
                journal_ok = False
                continue
            rel, valid = _normalize_scope_path(root, raw)
            journal_ok = journal_ok and valid
            if rel is not None:
                paths.add(rel)
    except (OSError, UnicodeError):
        return [], False
    return sorted(paths), journal_ok


def _read_entries_with_budget(project_root: Path, paths: Iterable[str]) -> tuple[ContentEntry, ...]:
    """Enforce aggregate accepted-byte bounds before each descriptor is hashed."""
    remaining = EvidenceLimits.MAX_TOTAL_BOUND_BYTES
    remaining_work = (_STABLE_READ_RETRIES + 1) * EvidenceLimits.MAX_TOTAL_BOUND_BYTES

    def charge_read(size: int) -> None:
        nonlocal remaining_work
        if size > remaining_work:
            raise StableReadError("bound_read_budget_exceeded")
        remaining_work -= size

    entries: list[ContentEntry] = []
    for path in paths:
        entry = read_content_entry(project_root, path, remaining_bytes=remaining, charge_read=charge_read)
        remaining -= entry.byte_size or 0
        entries.append(entry)
    return tuple(entries)


def build_content_binding(scope: RunOwnedScope, project_root: Path) -> BindingOutcome:
    """Assemble a :class:`ContentBinding` for a scope via stable reads (FR01).

    A ``scope_unverifiable`` scope yields no binding. An unstable/​escaping/​
    over-limit read yields the exact non-positive state and reason code.
    """
    if scope.confidence is ScopeConfidence.UNVERIFIABLE:
        return BindingOutcome(None, ReceiptState.SCOPE_UNVERIFIABLE, "scope_unverifiable")
    root = project_root
    identity_state, identity_reason = project_identity_is_current(scope.project_identity, root)
    if identity_state is not ReceiptState.VALID:
        return BindingOutcome(None, identity_state, identity_reason)
    try:
        entry_tuple = _read_entries_with_budget(root, scope.effective_paths)
    except StableReadError as exc:
        state = ReceiptState.UNSTABLE_READ if exc.reason_code == "unstable_read" else ReceiptState.INVALID
        return BindingOutcome(None, state, exc.reason_code)
    if project_identity_is_current(scope.project_identity, root)[0] is not ReceiptState.VALID:
        return BindingOutcome(None, ReceiptState.UNSTABLE_READ, "project_identity_changed")
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
    root = project_root
    identity_state, identity_reason = project_identity_is_current(binding.project_identity, root)
    if identity_state is not ReceiptState.VALID:
        return BindingOutcome(None, identity_state, identity_reason)
    try:
        current_entries = _read_entries_with_budget(root, (entry.path for entry in binding.entries))
    except StableReadError as exc:
        state = ReceiptState.UNSTABLE_READ if exc.reason_code == "unstable_read" else ReceiptState.INVALID
        return BindingOutcome(None, state, exc.reason_code)
    if project_identity_is_current(binding.project_identity, root)[0] is not ReceiptState.VALID:
        return BindingOutcome(None, ReceiptState.UNSTABLE_READ, "project_identity_changed")
    current_digest = compute_manifest_digest(current_entries)
    if current_digest != binding.manifest_digest:
        return BindingOutcome(None, ReceiptState.STALE_CONTENT, "bound_content_changed")
    return BindingOutcome(binding, ReceiptState.VALID, "ok")
