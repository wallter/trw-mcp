"""Whole-working-tree digest for evidence binding (E2E-INC-018).

The journal-derived content manifest only covers files a Write/Edit hook saw.
An edit made through a shell, another harness or by hand never enters it, so a
build receipt stayed "current" over changed code. This module computes a git
tree id of the *working tree content* that is independent of HEAD: committing
unchanged content between validation and delivery yields the same id.

How: a private temporary index is seeded from a copy of the real index (so git
reuses its stat cache and only re-hashes files whose stat changed),
``git add -A`` (which honours ``.gitignore`` and skips the TRW state paths)
folds the working tree in, and ``git write-tree`` names the result. TRW state
that git already tracks stays at its indexed version in that digest, so it is
excluded again at compare time: equal digests are current; otherwise
``git diff-tree`` with the same excludes lists what changed (none listed = current). The real
index is only ever read; no ref is written; the temp index is always removed.
``add`` and ``write-tree`` write loose blob/tree objects into the repository's
object store (unreachable, pruned by ``git gc``); that is what lets a stale
verdict name the changed paths later via ``git diff-tree``.

Anything that prevents a trustworthy digest (no git, a git error, the time
budget) is an explicit ``unbound_reason``, never a digest and never "current".
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Total wall-clock allowance for one digest. Over it -> tree_unbound_budget_exceeded.
TREE_BUDGET_SECS = 20.0

_TREE_SHA = re.compile(r"[0-9a-f]{40,64}\Z")

UNBOUND_NOT_GIT = "tree_unbound_not_git_repo"
UNBOUND_GIT_ERROR = "tree_unbound_git_error"
UNBOUND_GIT_MISSING = "tree_unbound_git_unavailable"
UNBOUND_BUDGET = "tree_unbound_budget_exceeded"
UNBOUND_LEGACY = "tree_unbound_legacy_receipt"
UNBOUND_RECHECK_FAILED = "tree_unbound_recheck_failed"
#: A submodule (gitlink) is covered: edits inside it never move this tree's digest (codex r1, W5).
UNBOUND_SUBMODULE = "tree_unbound_submodule"
#: An assume-unchanged entry, or a skip-worktree entry present on disk, hides edits from ``git add -A``.
UNBOUND_HIDDEN_ENTRIES = "tree_unbound_hidden_index_entries"
#: A nested repository (a ``.git`` below the root that git would add as a gitlink) hides its edits the same way.
UNBOUND_NESTED_REPO = "tree_unbound_nested_repo"


@dataclass(frozen=True)
class TreeSnapshot:
    """A digest, or the reason there is none. Exactly one of the two is set."""

    tree_sha: str | None
    unbound_reason: str = ""


class _GitFailure(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def relative_excludes(project_root: Path, paths: Iterable[Path | str]) -> tuple[str, ...]:
    """Repo-relative POSIX pathspecs for TRW-owned paths that live under the root."""
    roots = {project_root.absolute(), project_root.resolve()}
    out: set[str] = set()
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = project_root / candidate
        for root in roots:
            for probe in (Path(os.path.abspath(candidate)), candidate.resolve()):
                if probe != root and probe.is_relative_to(root):
                    out.add(probe.relative_to(root).as_posix())
    return tuple(sorted(out))


def _run(
    args: Sequence[str],
    root: Path,
    env: dict[str, str],
    deadline: float,
) -> subprocess.CompletedProcess[str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _GitFailure(UNBOUND_BUDGET)
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell; only repo-relative pathspecs are interpolated
            ["git", *args],  # noqa: S607 - git is resolved from PATH
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=remaining,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitFailure(UNBOUND_BUDGET) from exc
    except FileNotFoundError as exc:
        raise _GitFailure(UNBOUND_GIT_MISSING) from exc
    except OSError as exc:
        raise _GitFailure(UNBOUND_GIT_ERROR) from exc


#: Variables that point git at a different repository, work tree, index or object store than the one under the
#: cwd. An inherited one (a git hook, a wrapper, a worktree helper) would digest the wrong tree (E2E-INC-018 audit).
_REDIRECTING_GIT_VARS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_REPLACE_REF_BASE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_SHALLOW_FILE",
        "GIT_GRAFT_FILE",
    }
)


def _git_env(index_file: Path | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REDIRECTING_GIT_VARS}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    return env


def _checked(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode != 0:
        raise _GitFailure(UNBOUND_GIT_ERROR)
    return result.stdout


def snapshot_tree(
    project_root: Path,
    excludes: Sequence[str],
    *,
    budget_secs: float | None = None,
) -> TreeSnapshot:
    """Return the working-tree tree id under *project_root* minus *excludes*."""
    deadline = time.monotonic() + (TREE_BUDGET_SECS if budget_secs is None else budget_secs)
    try:
        located = _run(["rev-parse", "--git-path", "index"], project_root, _git_env(None), deadline)
        if located.returncode != 0:
            not_repo = "not a git repository" in located.stderr.lower()
            raise _GitFailure(UNBOUND_NOT_GIT if not_repo else UNBOUND_GIT_ERROR)
        real_index = Path(located.stdout.strip())
        if not real_index.is_absolute():
            real_index = project_root / real_index
        # A private system-temp dir holds the temp index; TemporaryDirectory removes it (no rmtree here).
        with tempfile.TemporaryDirectory(prefix="trw-tree-") as tmp_dir:
            return TreeSnapshot(tree_sha=_tree_sha(project_root, real_index, Path(tmp_dir), excludes, deadline))
    except _GitFailure as failure:
        logger.debug("tree_snapshot_unbound", reason=failure.reason, root=str(project_root))
        return TreeSnapshot(tree_sha=None, unbound_reason=failure.reason)
    except OSError:
        logger.debug("tree_snapshot_unbound", reason=UNBOUND_GIT_ERROR, root=str(project_root), exc_info=True)
        return TreeSnapshot(tree_sha=None, unbound_reason=UNBOUND_GIT_ERROR)


def _tree_sha(project_root: Path, real_index: Path, tmp_dir: Path, excludes: Sequence[str], deadline: float) -> str:
    """Fold the working tree into a temp index seeded from *real_index*; return ``write-tree``'s id."""
    if tmp_dir.resolve().is_relative_to(project_root.resolve()):
        # TMPDIR pointing into the checkout would make this a checkout write; refuse rather than write there.
        raise _GitFailure(UNBOUND_GIT_ERROR)
    temp_index = tmp_dir / "index"
    try:
        shutil.copyfile(real_index, temp_index)  # read-only toward the real index
    except FileNotFoundError:  # trw-fail-silent-allow: no index yet means a fresh repo with nothing staged; the temp index correctly starts empty
        pass
    except OSError as exc:
        raise _GitFailure(UNBOUND_GIT_ERROR) from exc
    env = _git_env(temp_index)
    blind = _blind_spot(project_root, env, deadline, excludes)
    if blind:
        raise _GitFailure(blind)
    _checked(
        _run(
            ["add", "-A", "--", ".", *(f":(exclude,literal){p}" for p in excludes)],
            project_root,
            env,
            deadline,
        )
    )
    nested = _nested_repos(project_root, env, deadline, excludes)
    if nested:
        raise _GitFailure(nested)
    sha = _checked(_run(["write-tree"], project_root, env, deadline)).strip()
    if _TREE_SHA.fullmatch(sha) is None:
        raise _GitFailure(UNBOUND_GIT_ERROR)
    return sha


def _blind_spot(project_root: Path, env: dict[str, str], deadline: float, excludes: Sequence[str]) -> str:
    """The UNBOUND reason when the digest cannot see every covered edit, else "".

    ``git add -A`` cannot see inside a submodule (a gitlink), nor past an assume-unchanged entry or a
    skip-worktree entry whose file is present on disk. Skip-worktree entries whose file is absent (the
    outside of a sparse checkout) hide nothing, so they do not unbind the tree.
    """
    listing = _checked(
        _run(
            ["ls-files", "-v", "-s", "-z", "--", ".", *(f":(exclude,literal){p}" for p in excludes)],
            project_root,
            env,
            deadline,
        )
    )
    submodules: list[str] = []
    hidden: list[str] = []
    for record in listing.split("\x00"):
        meta, _, path = record.partition("\t")
        if not path:
            continue
        tag, _, rest = meta.partition(" ")
        if rest.startswith("160000 "):
            submodules.append(path)
        elif tag.islower() or (tag == "S" and os.path.lexists(project_root / path)):
            hidden.append(path)
    if submodules:
        return f"{UNBOUND_SUBMODULE}:{','.join(submodules[:5])}"
    if hidden:
        return f"{UNBOUND_HIDDEN_ENTRIES}:{','.join(hidden[:5])}"
    return ""


def _nested_repos(project_root: Path, env: dict[str, str], deadline: float, excludes: Sequence[str]) -> str:
    """The UNBOUND reason when ``git add -A`` folded a nested repository in as a gitlink, else "".

    The pre-add blind-spot check only sees gitlinks already in the index. A repository nested below the root
    (a vendored clone, a stray ``git init``) is added as a gitlink by ``add -A``: the digest then names its
    commit, so an edit inside it never moves the tree. Any gitlink in the temp index at this point is such a
    repository (every existing one was refused before the add).
    """
    listing = _checked(
        _run(
            ["ls-files", "-s", "-z", "--", ".", *(f":(exclude,literal){p}" for p in excludes)],
            project_root,
            env,
            deadline,
        )
    )
    paths = [rec.partition("\t")[2] for rec in listing.split("\x00") if rec.startswith("160000 ")]
    return f"{UNBOUND_NESTED_REPO}:{','.join(paths[:5])}" if paths else ""


def differing_paths(
    project_root: Path,
    bound_tree: str,
    current_tree: str,
    excludes: Sequence[str],
) -> tuple[str, ...] | None:
    """Paths that differ between two tree ids, TRW state (*excludes*) not counted.

    ``None`` means the comparison itself failed (e.g. the bound tree's loose objects
    were pruned), which the caller must report as unbound, not as current.
    """
    deadline = time.monotonic() + 10.0
    try:
        out = _checked(
            _run(
                [
                    "diff-tree",
                    "-r",
                    "--name-only",
                    "--no-renames",
                    "-z",
                    bound_tree,
                    current_tree,
                    "--",
                    ".",
                    *(f":(exclude,literal){p}" for p in excludes),
                ],
                project_root,
                _git_env(None),
                deadline,
            )
        )
    except _GitFailure:  # trw-fail-silent-allow: comparison failure is returned as None; the caller reports the binding UNBOUND, never current
        return None
    return tuple(name for name in out.split("\x00") if name)
