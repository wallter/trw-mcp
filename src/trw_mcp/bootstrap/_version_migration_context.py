"""`.trw/context/` cleanup policy for update-project.

Belongs to the ``_version_migration.py`` facade. Re-exported there for
back-compat with `_update_project.py`, `bootstrap/__init__.py`, and tests.

Owns one question: **which files in `.trw/context/` may an upgrade delete?**
The answer is "only ones we recognise as retired" — see `_TRANSIENT_PATTERNS`
for why that is the inverse of what originally shipped.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from contextlib import ExitStack
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "_CONTEXT_ALLOWLIST",
    "_SUPPORTS_PINNED_CONTEXT_CLEANUP",
    "_TRANSIENT_PATTERNS",
    "_cleanup_context_transients",
    "_is_transient_context_artifact",
]

_SUPPORTS_PINNED_CONTEXT_CLEANUP = (
    bool(getattr(os, "O_DIRECTORY", 0))
    and bool(getattr(os, "O_NOFOLLOW", 0))
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.listdir in os.supports_fd
)

# Files in .trw/context/ that are always preserved during cleanup.
#
# PRD-FIX-120: this set is no longer what keeps a file alive — preservation is
# now the default and only `_TRANSIENT_PATTERNS` removes anything. It is kept as
# defence in depth so a durable name survives even if some future pattern grows
# greedy enough to match it (see test_allowlist_beats_a_pattern_match).
_CONTEXT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "analytics.yaml",
        "behavioral_protocol.md",
        "behavioral_protocol.yaml",
        "build-status.yaml",
        "ceremony-feedback.yaml",
        "ceremony-state.json",
        "injected_learning_ids.txt",
        "last_ups_phase",
        "messages.yaml",
        "pre_compact_state.json",
        "hooks-reference.yaml",
    }
)

# Names and globs of artifacts retired by older TRW versions. ONLY these are
# deleted from .trw/context/ during update-project.
#
# PRD-FIX-120 — why this list exists at all. PRD-FIX-031 asked for two things
# and shipped one. Its Goals required an allowlist "covering all active TRW
# context files referenced by the codebase"; the shipped set covered 11 of ~27.
# Its FR03 named three transient globs but wrote the predicate as an OR — "not
# in _CONTEXT_ALLOWLIST by name, OR matches one of the transient glob patterns"
# — so clause (a) deleted every unlisted file on its own and the three patterns
# never changed an outcome. They could have been deleted from the source with
# every test still green.
#
# The consequence was not cosmetic. update-project is the standard upgrade path
# for every installed project, and it unlinked `trust-registry.yaml` (MCP trust
# decisions), `deliver-override-audit.jsonl` (the record of every
# truthfulness-gate override), and the `events-*.jsonl` security stream that
# `trw_mcp_security_status` and the anomaly detector read — none of which are
# reconstructable.
#
# So the predicate is inverted to match what FIX-031's own user story asked for
# ("my analytics history, build cache, and session state are never lost"):
# delete what is KNOWN transient, preserve everything else. An unrecognised file
# is now left alone — it may be a user's own note, or state written by a TRW
# version newer than the installer performing the update. Stale junk from an old
# version is a cosmetic cost; an unlinked audit trail is unrecoverable.
_TRANSIENT_PATTERNS: tuple[str, ...] = (
    # FR03's three, now actually load-bearing.
    "tc_block_*",
    "idle_block_*",
    "*-findings.yaml",
    # Retired exact names, kept as literal patterns so there is one list to read.
    "velocity.yaml",
    "tool-telemetry.jsonl",
    # PRD-FIX-031 Non-Goals is explicit that this one is a deliberate one-time
    # purge on update, not an oversight: rotation for it already lives in
    # lib-trw.sh. Under deny-by-default it was swept for being unlisted, so the
    # inversion has to name it or the intent would be silently dropped.
    "hook-executions.log",
)


def _is_transient_context_artifact(name: str) -> bool:
    """Whether `name` is a retired artifact the cleanup may delete.

    Allowlisted names always win, so adding a pattern can never silently start
    eating durable state.
    """
    if name in _CONTEXT_ALLOWLIST:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in _TRANSIENT_PATTERNS)


def _cleanup_context_transients(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool = False,
) -> None:
    """Remove retired artifacts from .trw/context/ during update-project.

    Deletes only regular files matching `_TRANSIENT_PATTERNS` (not directories,
    not symlinks). Everything else — including files this version has never
    heard of — is left in place.

    Args:
        target_dir: Root of the target git repository.
        result: Mutable result dict -- cleaned paths appended to ``result["cleaned"]``.
        dry_run: When ``True``, report what would be removed without deleting.
    """
    context_dir = target_dir / ".trw" / "context"
    if not _SUPPORTS_PINNED_CONTEXT_CLEANUP:
        result.setdefault("warnings", []).append(
            f"Skipped context cleanup because this platform cannot safely pin {context_dir}"
        )
        return

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    cleaned: list[str] = []
    with ExitStack() as opened_dirs:
        try:
            root_fd = os.open(target_dir, directory_flags)
            opened_dirs.callback(os.close, root_fd)
            trw_fd = os.open(".trw", directory_flags, dir_fd=root_fd)
            opened_dirs.callback(os.close, trw_fd)
            context_fd = os.open("context", directory_flags, dir_fd=trw_fd)
            opened_dirs.callback(os.close, context_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            result.setdefault("warnings", []).append(f"Skipped unsafe context cleanup for {context_dir}: {exc}")
            return

        try:
            entry_names = sorted(os.listdir(context_fd))
        except OSError as exc:
            result.setdefault("warnings", []).append(f"Skipped unreadable context cleanup for {context_dir}: {exc}")
            return

        for name in entry_names:
            if not _is_transient_context_artifact(name):
                continue
            try:
                entry_stat = os.stat(name, dir_fd=context_fd, follow_symlinks=False)
            except OSError as exc:
                result["errors"].append(f"Failed to inspect {context_dir / name}: {exc}")
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                continue
            path = context_dir / name
            if dry_run:
                result["cleaned"].append(f"would remove: {path}")
                continue
            try:
                os.unlink(name, dir_fd=context_fd)
                result["cleaned"].append(str(path))
                cleaned.append(name)
            except OSError as exc:
                result["errors"].append(f"Failed to remove {path}: {exc}")

    logger.info(
        "context_cleanup",
        target=str(target_dir),
        cleaned_count=len(cleaned),
        dry_run=dry_run,
    )
