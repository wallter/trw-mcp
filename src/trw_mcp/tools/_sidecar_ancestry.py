"""Ancestor-sidecar selection for the pre-edit hint (T2 read path).

Responsibility: when no exact-HEAD ``before-edit-batch-<sha>.json`` exists,
find the cached batch sidecar whose sha is a PROVEN ancestor of HEAD at the
SMALLEST commit distance within a bound, and report the files a later commit
changed, so the caller can drop claims those commits could have made false.

Interface (everything else is private):

- :class:`GitReader` — the port; :class:`SubprocessGitReader` is the production
  adapter and tests inject a fake.
- :func:`find_ancestor_sidecar` → :class:`AncestryOutcome`.
- :func:`filter_stale_entry` — per-field filtering of one hint entry.
- :func:`shared_cache_dir` — the main checkout's cache dir, seen from a linked worktree.
- :class:`GitReadError` — the one failure class a git read raises.

Invariants:

- A sha reaches git argv only after matching ``[0-9a-fA-F]{7,64}``, and every
  argv carries ``--end-of-options`` before the first revision or path.
- A candidate is served only when git proved it an ancestor of HEAD
  (``rev-list --left-right --count``: nothing reachable from it is missing
  from HEAD, the ``merge-base --is-ancestor`` question, answered in the same
  spawn that counts the distance).
- The ancestry cache ``ancestry-<head>.json`` is keyed by (sidecar sha, HEAD),
  schema-validated on every read, recomputed when corrupt, and written
  atomically (temp file, then rename). It never caches working-tree state.
- At most :data:`_MAX_CANDIDATES` sidecar names and :data:`_MAX_CACHE_FILES`
  cache files are kept in play, so the work per edit is bounded.

Knobs (``TRWConfig``): ``hint_sidecar_ancestor_enabled`` gates the caller;
``hint_sidecar_max_commits_behind`` arrives here as ``max_commits_behind``.

IP boundary: trw-mcp is PUBLIC; this module never imports trw-distill. The
sidecar file name and envelope are the whole contract.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trw_mcp.tools._sidecar_substrate import ANCESTOR_ARTIFACT as _BATCH_ARTIFACT

_logger = structlog.get_logger(__name__)

#: Named in every log event and refusal this module emits (operator rule:
#: a flagged feature names its off-switch).
FLAG_DISABLE = "disable with hint_sidecar_ancestor_enabled: false in .trw/config.yaml"

_SHA_RE = re.compile(r"[0-9a-fA-F]{7,64}")
_BATCH_NAME_RE = re.compile(rf"{_BATCH_ARTIFACT}-([0-9a-fA-F]{{7,64}})\.json")
_CACHE_SCHEMA: Literal["trw-sidecar-ancestry/v1"] = "trw-sidecar-ancestry/v1"

#: Sidecar names examined per lookup. The builder keeps the newest two maps
#: plus any leftovers from failed builds; 16 covers that with room, and caps
#: the git work on a cold cache at 1 subprocess per candidate plus 1 diff.
_MAX_CANDIDATES = 16
#: ``ancestry-<head>.json`` files kept; older ones are pruned on write. Several
#: worktrees at different HEADs share one cache dir, so this is more than one.
_MAX_CACHE_FILES = 8
#: Per git subprocess. Below the hook's 2.4 s in-process deadline, so a hung
#: git fails this read as ``sidecar_diff_failed`` instead of killing the hook.
_GIT_TIMEOUT_S = 2.0

#: Git statuses under which a path named in a sidecar no longer exists there.
_GONE_STATUSES = frozenset({"D", "R"})
#: Fields whose value depends on the target file's own content (P0 table).
_CONTENT_FIELDS = ("risk_score", "hotspot_warnings", "importers", "inferred_tests", "doc_references")
#: Fields that name other files; a deleted or renamed one is dropped.
_PARTNER_FIELDS = ("importers", "inferred_tests", "doc_references", "co_change_neighbors")


class GitReadError(RuntimeError):
    """A git read could not answer; the message names the command and why."""


def valid_sha(sha: str) -> str:
    """Return *sha* when it is 7-64 hex characters, else raise ``GitReadError``.

    The guard runs before any git argv is built, so a crafted file name can
    never become an option or a revision expression.
    """
    if not _SHA_RE.fullmatch(sha):
        raise GitReadError(f"refusing to pass {sha[:80]!r} to git: not a 7-64 character hex sha")
    return sha


class GitReader(Protocol):
    """The git questions the ancestor read path asks. Every method raises ``GitReadError``."""

    def ancestor_distance(self, sha: str, head: str) -> int | None:
        """Commits *head* is ahead of *sha* when *sha* is a proven ancestor, else None."""
        ...

    def changed_paths(self, sha: str, head: str) -> dict[str, str]: ...

    def worktree_changed(self, path: str) -> bool:
        """True when *path* is staged, unstaged or untracked. Never cached."""
        ...


@dataclass(frozen=True)
class SubprocessGitReader:
    """``GitReader`` over the ``git`` CLI, run in *repo_root* with a bounded timeout."""

    repo_root: Path
    timeout_s: float = _GIT_TIMEOUT_S

    def _run(
        self, args: tuple[str, ...], ok_codes: frozenset[int] = frozenset({0})
    ) -> subprocess.CompletedProcess[bytes]:
        label = f"git {' '.join(args[:2])}"
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, validated shas, no shell
                ["git", *args],  # noqa: S607 - git on PATH, as every other trw-mcp git read
                cwd=self.repo_root,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as err:
            raise GitReadError(f"`{label}` could not run in {self.repo_root}: {err}") from err
        if proc.returncode not in ok_codes:
            stderr = proc.stderr.decode("utf-8", "replace").strip()[:200]
            raise GitReadError(f"`{label}` exited {proc.returncode}: {stderr or 'no stderr'}")
        return proc

    def ancestor_distance(self, sha: str, head: str) -> int | None:
        """One ``git rev-list --left-right --count <sha>...<head>``: ``L R`` counts.

        ``L`` counts commits reachable from *sha* but not *head*, so ``L == 0``
        exactly when *sha* is an ancestor of *head* (the ``merge-base
        --is-ancestor`` question), and ``R`` is then the distance. One spawn
        answers both. Exit 128 (a sha this clone lacks, e.g. a cache copied
        from elsewhere) is "not proven", never an ancestor.
        """
        args = ("rev-list", "--left-right", "--count", "--end-of-options", f"{valid_sha(sha)}...{valid_sha(head)}")
        proc = self._run(args, frozenset({0, 128}))
        if proc.returncode == 128:
            return None
        counts = proc.stdout.decode("ascii", "replace").split()
        if len(counts) != 2 or not all(count.isdigit() for count in counts):
            raise GitReadError(f"`git rev-list --left-right --count` printed {counts!r:.60}, not two counts")
        return int(counts[1]) if counts[0] == "0" else None

    def changed_paths(self, sha: str, head: str) -> dict[str, str]:
        args = (
            "diff",
            "--name-status",
            "--no-renames",
            "-z",
            "--end-of-options",
            valid_sha(sha),
            valid_sha(head),
            "--",
        )
        return parse_name_status_z(self._run(args).stdout)

    def worktree_changed(self, path: str) -> bool:
        """``git status`` for one path; ``--literal-pathspecs`` keeps a ``:(...)`` name from acting as magic."""
        args = (
            "--literal-pathspecs",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--end-of-options",
            "--",
            path,
        )
        return bool(self._run(args).stdout.strip(b"\0"))


def parse_name_status_z(raw: bytes) -> dict[str, str]:
    """``{path: status letter}`` from ``git diff --name-status -z``.

    A rename or copy (``R``/``C`` with a score) names two paths: the old one is
    recorded under its letter and the new one as ``A``.
    """
    tokens = raw.decode("utf-8", "surrogateescape").split("\0")
    changed: dict[str, str] = {}
    index = 0
    while index + 1 < len(tokens) and tokens[index]:
        letter = tokens[index][0]
        if letter in ("R", "C") and index + 2 < len(tokens):
            changed[tokens[index + 1]] = letter
            changed[tokens[index + 2]] = "A"
            index += 3
            continue
        changed[tokens[index + 1]] = letter
        index += 2
    return changed


class _CacheEntry(BaseModel):
    """What one (sidecar sha, HEAD) pair proved. ``changed`` is filled only for a served ancestor."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    is_ancestor: bool
    commits_behind: int | None = Field(default=None, ge=0)
    changed: dict[str, str] | None = None

    @model_validator(mode="after")
    def _ancestor_has_distance(self) -> _CacheEntry:
        if self.is_ancestor and self.commits_behind is None:
            raise ValueError("an ancestor entry must carry commits_behind")
        return self


class _AncestryCache(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["trw-sidecar-ancestry/v1"]
    head: str
    entries: dict[str, _CacheEntry]


@dataclass(frozen=True)
class AncestorSidecar:
    """The batch sidecar chosen for HEAD: its sha, file, distance and the files changed since."""

    sha: str
    path: Path
    commits_behind: int
    changed: Mapping[str, str] = field(default_factory=dict)
    #: Files the builder saw dirty when it wrote the sidecar (envelope ``dirty_paths``).
    dirty_paths: frozenset[str] = frozenset()


AncestryStatus = Literal["found", "no_candidates", "too_far_behind", "git_failed"]


@dataclass(frozen=True)
class AncestryOutcome:
    """Result of :func:`find_ancestor_sidecar`. ``ancestor`` is set only when ``status == "found"``."""

    status: AncestryStatus
    ancestor: AncestorSidecar | None = None
    nearest_commits_behind: int | None = None
    reason: str = ""


def shared_cache_dir(repo_root: Path, cache_rel: str) -> Path:
    """*cache_rel* under the main checkout when *repo_root* is a linked worktree without its own.

    Resolved from the ``.git`` file and ``commondir`` alone (no subprocess), so
    every worktree of a repository reads the one cache the main checkout owns.
    Falls back to ``repo_root / cache_rel`` whenever that layout does not hold.
    """
    local = repo_root / cache_rel
    dotgit = repo_root / ".git"
    if local.is_dir() or not dotgit.is_file():
        return local
    try:
        text = dotgit.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return local
        gitdir = Path(text.removeprefix("gitdir:").strip())
        gitdir = gitdir if gitdir.is_absolute() else repo_root / gitdir
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return local
    return common.parent / cache_rel if common.name == ".git" else local


def _index(cache_dir: Path) -> list[tuple[str, Path]]:
    """Up to ``_MAX_CANDIDATES`` ``(sha, path)`` batch sidecars, newest file first."""
    found: list[tuple[float, str, Path]] = []
    for path in cache_dir.glob(f"{_BATCH_ARTIFACT}-*.json"):
        match = _BATCH_NAME_RE.fullmatch(path.name)
        if match is None:
            continue
        try:
            found.append((path.stat().st_mtime, match.group(1), path))
        except OSError as err:
            # A sidecar removed between glob and stat is simply not a candidate.
            _logger.debug("sidecar_ancestry_candidate_vanished", path=str(path), error=str(err), outcome="skipped")
    found.sort(key=lambda item: item[0], reverse=True)
    return [(sha, path) for _mtime, sha, path in found[:_MAX_CANDIDATES]]


def _cache_path(cache_dir: Path, head: str) -> Path:
    return cache_dir / f"ancestry-{head}.json"


def _read_cache(cache_dir: Path, head: str) -> dict[str, _CacheEntry]:
    """Validated entries for *head*; empty when absent, and empty (logged) when corrupt."""
    path = _cache_path(cache_dir, head)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:  # trw-fail-silent-allow: no cache yet means "not computed"; every entry is recomputed
        return {}
    except OSError as err:
        _logger.info(
            "sidecar_ancestry_cache_unreadable",
            path=str(path),
            error=str(err),
            outcome="recompute",
            disable=FLAG_DISABLE,
        )
        return {}
    try:
        cache = _AncestryCache.model_validate_json(raw)
        if cache.head != head or not all(_SHA_RE.fullmatch(sha) for sha in cache.entries):
            raise ValueError("head or sha keys do not match the file name")
    except (ValidationError, ValueError) as err:
        _logger.info(
            "sidecar_ancestry_cache_corrupt",
            path=str(path),
            error=str(err)[:200],
            outcome="recompute",
            disable=FLAG_DISABLE,
        )
        return {}
    return dict(cache.entries)


def _write_cache(cache_dir: Path, head: str, entries: dict[str, _CacheEntry]) -> None:
    """Atomically replace ``ancestry-<head>.json``, then prune the oldest cache files.

    A failed write only costs the next edit a recompute, so it is logged, not raised.
    """
    body = _AncestryCache(schema_version=_CACHE_SCHEMA, head=head, entries=entries).model_dump_json()
    target = _cache_path(cache_dir, head)
    try:
        fd, tmp = tempfile.mkstemp(prefix=".ancestry-", suffix=".tmp", dir=cache_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp, target)
    except OSError as err:
        _logger.info(
            "sidecar_ancestry_cache_write_failed",
            path=str(target),
            error=str(err),
            outcome="next_edit_recomputes",
            disable=FLAG_DISABLE,
        )
        return
    caches = sorted(cache_dir.glob("ancestry-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in caches[_MAX_CACHE_FILES:]:
        stale.unlink(missing_ok=True)


def find_ancestor_sidecar(
    cache_dir: Path,
    head: str,
    *,
    git: GitReader,
    max_commits_behind: int,
    persist: bool = True,
) -> AncestryOutcome:
    """Pick the proven ancestor batch sidecar nearest to *head*, within *max_commits_behind*.

    ``no_candidates``: no batch sidecar is cached. ``too_far_behind``: none is a
    proven ancestor within the bound. ``git_failed``: every candidate's check
    failed, or the diff of the chosen one did. A candidate whose check fails is
    skipped, never served. ``persist=False`` (the reviewer role, which writes
    nothing) uses a valid cache but never writes or prunes one.
    """
    candidates = _index(cache_dir)
    if not candidates:
        return AncestryOutcome(status="no_candidates")
    valid_sha(head)
    entries = _read_cache(cache_dir, head)
    dirty = False
    failures: list[str] = []
    best: tuple[int, str, Path] | None = None
    nearest: int | None = None
    for sha, path in candidates:
        entry = entries.get(sha)
        if entry is None:
            try:
                distance = git.ancestor_distance(sha, head)
                entry = _CacheEntry(is_ancestor=distance is not None, commits_behind=distance)
            except GitReadError as err:
                failures.append(str(err))
                continue
            entries[sha] = entry
            dirty = True
        if not entry.is_ancestor or entry.commits_behind is None:
            continue
        nearest = entry.commits_behind if nearest is None else min(nearest, entry.commits_behind)
        if entry.commits_behind <= max_commits_behind and (best is None or entry.commits_behind < best[0]):
            best = (entry.commits_behind, sha, path)
    outcome, diffed = _finish(head, entries, best, git=git)
    if persist and (dirty or diffed):
        _write_cache(cache_dir, head, entries)
    if outcome.status != "too_far_behind":
        return outcome
    if len(failures) == len(candidates):
        return AncestryOutcome(status="git_failed", reason=failures[0])
    return AncestryOutcome(status="too_far_behind", nearest_commits_behind=nearest)


def _finish(
    head: str,
    entries: dict[str, _CacheEntry],
    best: tuple[int, str, Path] | None,
    *,
    git: GitReader,
) -> tuple[AncestryOutcome, bool]:
    """Attach the changed-file map to the chosen candidate; True when it had to be computed."""
    if best is None:
        return AncestryOutcome(status="too_far_behind"), False
    behind, sha, path = best
    entry = entries[sha]
    changed = entry.changed
    computed = changed is None
    if changed is None:
        try:
            changed = git.changed_paths(sha, head) if behind else {}
        except GitReadError as err:
            _logger.info("sidecar_ancestry_diff_failed", sha=sha, error=str(err), disable=FLAG_DISABLE)
            return AncestryOutcome(status="git_failed", reason=str(err)), False
        entries[sha] = _CacheEntry(is_ancestor=True, commits_behind=behind, changed=changed)
    _logger.debug("sidecar_ancestor_selected", sha=sha, commits_behind=behind, disable=FLAG_DISABLE)
    ancestor = AncestorSidecar(sha=sha, path=path, commits_behind=behind, changed=changed)
    return AncestryOutcome(status="found", ancestor=ancestor), computed


def _is_partner_path(item: object) -> bool:
    """A partner entry renders only as a non-empty, single-line, printable path string."""
    return isinstance(item, str) and bool(item.strip()) and item.isprintable()


def parse_dirty_paths(raw: object) -> frozenset[str] | None:
    """The envelope's ``dirty_paths``: empty when absent, None when it is not a list of paths."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or not all(_is_partner_path(item) for item in raw):
        return None
    return frozenset(item for item in raw if isinstance(item, str))


def filter_stale_entry(
    entry: Mapping[str, object],
    *,
    changed: Mapping[str, str],
    target_changed: bool,
) -> dict[str, object]:
    """One batch hint entry, reduced to what still holds at HEAD.

    Every partner list keeps only well-formed path strings (the rest are
    dropped and counted in ``sidecar_partner_entries_dropped``), minus partners
    deleted or renamed since the sidecar. A changed target also loses every
    content-dependent field (risk, warnings, importers, inferred tests, doc
    references); co-change history is kept as "as of" either way. Non-list
    values are left for the schema to reject.
    """
    gone = {path for path, status in changed.items() if status in _GONE_STATUSES}
    reduced = dict(entry)
    malformed = 0
    for name in _PARTNER_FIELDS:
        value = reduced.get(name)
        if isinstance(value, list):
            wellformed = [item for item in value if _is_partner_path(item)]
            malformed += len(value) - len(wellformed)
            reduced[name] = [item for item in wellformed if item not in gone]
    if malformed:
        _logger.info(
            "sidecar_partner_entries_dropped",
            count=malformed,
            reason="not a non-empty single-line path string",
            outcome="dropped",
            disable=FLAG_DISABLE,
        )
    if target_changed:
        for name in _CONTENT_FIELDS:
            reduced[name] = None if name == "risk_score" else []
    return reduced


__all__ = [
    "FLAG_DISABLE",
    "AncestorSidecar",
    "AncestryOutcome",
    "GitReadError",
    "GitReader",
    "SubprocessGitReader",
    "filter_stale_entry",
    "find_ancestor_sidecar",
    "parse_dirty_paths",
    "parse_name_status_z",
    "shared_cache_dir",
    "valid_sha",
]
