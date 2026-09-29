"""A PRD's requirement baseline, read from git by PRD ID (PRD-CORE-321 FR01).

:func:`resolve_requirement_baseline` finds the first commit on the first-parent
line whose version of the PRD entered the approved family, and returns that
version's verification mappings and declared call chains. It writes nothing:
every input is a committed object read by sha, plus the working-tree file's
current status.

Runtime caller: ``trw_mcp.tools._deliver_requirement_drift.compute_requirement_drift``
-> :func:`resolve_requirement_baseline`, reached from ``trw_deliver`` through
``evaluate_delivery_gates``. :func:`requirements_from_text` is the same parse
applied to the working-tree text, called by
``trw_mcp.state.validation.requirement_drift.detect_requirement_drift`` so the
current side of the FR02 comparison cannot drift from the baseline side.

Soundness scope: proves which criteria, evidence artifacts and call chains the
earliest approved-or-later version recorded on the first-parent line (the first
approval commit) carried, and that renaming the file, returning the working tree
to draft, or setting a non-enum status does not remove that baseline. Does not
prove: (a) the approval was legitimate: an agent that commits
``status: approved`` itself creates a valid approval commit; (b) the recorded
version is the text a human approved: where history was squashed, the squash
commit is the earliest recorded version, and edits made before the squash are
invisible; (c) the approval commit still exists as committed: a rebase,
force-push or ``git commit --amend`` that rewrites it replaces the baseline
undetected; (d) continuity across an ID change: a file renamed to a different
PRD ID starts a new history. An unparseable historical version can only add a
``baseline_reapproved`` finding, never remove one.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from trw_mcp.state.prd_utils import parse_frontmatter
from trw_mcp.state.validation.chain_declarations import ChainDeclaration, declared_chains

BaselineStatus = Literal["resolved", "baseline_unresolvable", "not_applicable"]
UnresolvableReason = Literal[
    "no_git",
    "untracked",
    "shallow_clone",
    "approval_uncommitted",
    "ambiguous_id",
    "current_unparseable",
    "version_unreadable",
]
FindingKind = Literal["baseline_reapproved", "status_regressed", "shallow_clone"]

#: Implemented-family aliases the transition gate accepts, plus ``approved``.
APPROVED_FAMILY: frozenset[str] = frozenset({"approved", "implemented", "done", "delivered", "complete"})
_IMPLEMENTED_FAMILY: frozenset[str] = APPROVED_FAMILY - {"approved"}

_GIT_TIMEOUT_S = 10.0  # the NFR01 budget for one whole resolution
#: Variables that would point a ``git -C <root>`` call at another repository or index
#: (set, for example, while a git hook runs the test suite).
_GIT_LOCATION_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
)
_SHORT_KEY_RE = re.compile(r"^N?FR\d+$")
_FULL_KEY_RE = re.compile(r"^PRD-.+-N?FR\d+$")


@dataclass(frozen=True)
class BaselineRequirement:
    """One ``verification.mappings`` entry of the baseline version, with its call chain."""

    requirement_id: str
    acceptance_criteria: tuple[str, ...]
    evidence_artifact: str
    call_chain: ChainDeclaration = field(default_factory=ChainDeclaration)


@dataclass(frozen=True)
class BaselineFinding:
    """A PRD-level fact about the baseline: ``baseline_reapproved`` names the first and
    newest approval shas, ``status_regressed`` names the current status, and
    ``shallow_clone`` names the oldest listed sha (the real approval may predate it)."""

    kind: FindingKind
    first_sha: str = ""
    newest_sha: str = ""
    current_status: str = ""


@dataclass(frozen=True)
class BaselineResolution:
    """The outcome of :func:`resolve_requirement_baseline`.

    ``status`` is ``resolved`` (``baseline_sha`` and ``requirements`` are set),
    ``baseline_unresolvable`` (``reason`` names why) or ``not_applicable`` (history
    was read, holds no approval commit, and the current status is outside the family).
    ``approval_shas`` is oldest first; ``baseline_sha`` is always its first element.
    ``approval_dates`` holds each approval commit's committer date (``%cs``), in
    the same order: FR04 dates a PRD-id amendment row against its last element.
    """

    prd_id: str
    status: BaselineStatus
    reason: UnresolvableReason | None = None
    current_status: str | None = None
    repo_root: Path | None = None
    baseline_sha: str | None = None
    baseline_date: str | None = None
    baseline_path: str | None = None
    approval_shas: tuple[str, ...] = ()
    approval_dates: tuple[str, ...] = ()
    requirements: tuple[BaselineRequirement, ...] = ()
    chains: Mapping[str, ChainDeclaration] = field(default_factory=dict)
    findings: tuple[BaselineFinding, ...] = ()
    safety_critical_in_history: bool = False
    implemented_in_history: bool = False


@dataclass(frozen=True)
class _Version:
    sha: str
    date: str
    path: str
    status: str | None
    frontmatter: dict[str, object]
    text: str


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git -C <root> <args>`` from an argument list, never a shell string."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_ENV}
    env["GIT_OPTIONAL_LOCKS"] = "0"  # read-only: never refresh the index as a side effect
    command = ["git", "-C", str(root), "-c", "core.quotepath=off", "-c", "log.follow=false", *args]
    return subprocess.run(  # noqa: S603 — shell=False; an argument list with a `--` separator before any path
        command,
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",  # a non-UTF-8 path byte round-trips back into the next argv unchanged
        check=False,
        timeout=_GIT_TIMEOUT_S,
        env=env,
    )


def _status_of(frontmatter: Mapping[str, object]) -> str | None:
    value = frontmatter.get("status")
    return value.strip().lower() if isinstance(value, str) else None


def _repo_root(prd_path: Path) -> Path | None:
    try:
        result = _git(prd_path.parent, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        # trw-fail-silent-allow: no git binary or a hung git is reported as the named no_git reason
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip())


def _tree_matches(root: Path, sha: str, prds_dir: str, boundary: re.Pattern[str]) -> list[str]:
    """Every FILE (blob) of *prds_dir* in *sha*'s tree whose basename carries the PRD ID.

    A directory such as ``PRD-X-001-assets/`` carries the ID too but is not a PRD file, so
    entries are read with their object type (``<mode> <type> <object>\\t<path>``) and only
    blobs are kept.
    """
    result = _git(root, "ls-tree", "-z", sha, "--", *([f"{prds_dir}/"] if prds_dir else []))
    if result.returncode != 0:
        raise RuntimeError(f"git ls-tree failed for {sha}: {result.stderr.strip()}")
    entries = (entry.partition("\t") for entry in result.stdout.split("\x00") if entry)
    return [
        path
        for meta, _, path in entries
        if meta.split(" ")[1:2] == ["blob"] and boundary.match(path.rsplit("/", 1)[-1])
    ]


def _history(root: Path, prds_dir: str, prd_id: str) -> list[tuple[str, str, str]] | None:
    """``(sha, date, version path)`` newest first for each commit that touched a file carrying *prd_id*.

    The version path comes from the commit's whole tree, not its diff, so a second file
    with the same ID added in a later commit is seen. ``None`` (``ambiguous_id``) when any
    such tree holds two or more matching files; a commit whose tree holds none (a
    deletion) is skipped. ``-z`` keeps every path literal: no C-quoting to undo.
    ``--no-renames`` makes a rename a delete plus an add whatever ``diff.renames`` is.
    """
    pathspec = f"{prds_dir}/{prd_id}*" if prds_dir else f"{prd_id}*"
    result = _git(
        root, "log", "-z", "--first-parent", "-m", "--no-renames", "--no-show-signature",
        "--format=%x01%H%x09%cs", "--name-status", "--", pathspec,
    )  # fmt: skip
    if result.returncode != 0:
        if _git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0:
            return []  # unborn branch: no commits yet, so no history
        raise RuntimeError(f"git log failed for {prd_id}: {result.stderr.strip()}")
    boundary = re.compile(rf"^{re.escape(prd_id)}(?!\d)")
    touched: list[tuple[str, str]] = []  # (sha, date) of commits whose diff names a path carrying the ID
    sha, date = "", ""
    tokens = result.stdout.split("\x00")
    index = 0
    while index < len(tokens):
        token = tokens[index].lstrip("\n")
        index += 1
        if not token:
            continue
        if token.startswith("\x01"):
            sha, _, date = token[1:].partition("\t")
            continue
        paths = tokens[index : index + (2 if token[:1] in {"R", "C"} else 1)]
        index += len(paths)
        hit = any(boundary.match(path.rsplit("/", 1)[-1]) for path in paths)
        if hit and sha and (not touched or touched[-1][0] != sha):
            touched.append((sha, date))  # a commit touching only a longer ID (PRD-X-0010) never lands here
    commits: list[tuple[str, str, str]] = []
    for sha, date in touched:
        matches = _tree_matches(root, sha, prds_dir, boundary)
        if len(matches) >= 2:
            return None
        if matches:
            commits.append((sha, date, matches[0]))
    return commits


def _read_version(root: Path, sha: str, date: str, path: str) -> _Version | None:
    """The version at *sha*, or ``None`` when git cannot produce the blob (``version_unreadable``).

    A blob that reads but does not parse is a version that is not approved (FR01 step 4);
    a blob git cannot read is not a version at all, so it is never treated as empty text.
    """
    result = _git(root, "show", f"{sha}:{path}")
    if result.returncode != 0:
        return None
    frontmatter = parse_frontmatter(result.stdout)
    return _Version(sha, date, path, _status_of(frontmatter), frontmatter, result.stdout)


def _full_chain_id(prd_id: str, key: str) -> str | None:
    if _SHORT_KEY_RE.match(key):
        return f"{prd_id}-{key}"
    return key if _FULL_KEY_RE.match(key) else None


def _requirements(
    prd_id: str, frontmatter: Mapping[str, object], text: str
) -> tuple[dict[str, ChainDeclaration], tuple[BaselineRequirement, ...]]:
    chains: dict[str, ChainDeclaration] = {}
    for key, declaration in declared_chains(text).items():
        full_id = _full_chain_id(prd_id, key)
        if full_id is not None:
            chains[full_id] = declaration
    verification = frontmatter.get("verification")
    raw = verification.get("mappings") if isinstance(verification, dict) else None
    requirements: list[BaselineRequirement] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("requirement_id"), str):
            continue
        criteria = entry.get("acceptance_criteria")
        if isinstance(criteria, list):
            criteria_items = tuple(str(item) for item in criteria)
        else:
            criteria_items = (criteria,) if isinstance(criteria, str) else ()
        artifact = entry.get("evidence_artifact")
        req_id = entry["requirement_id"]
        requirements.append(
            BaselineRequirement(
                requirement_id=req_id,
                acceptance_criteria=criteria_items,
                evidence_artifact=artifact if isinstance(artifact, str) else "",
                call_chain=chains.get(req_id, ChainDeclaration()),
            )
        )
    return chains, tuple(requirements)


def requirements_from_text(prd_id: str, text: str) -> tuple[BaselineRequirement, ...]:
    """*text*'s mappings and call chains, parsed exactly as a baseline version's are."""
    return _requirements(prd_id, parse_frontmatter(text), text)[1]


def _is_shallow(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"


def _no_approval_reason(root: Path, rel_path: str, has_history: bool) -> UnresolvableReason:
    """The FR01 step 8 reason when the history holds no approval commit."""
    if not has_history and _git(root, "ls-files", "--error-unmatch", "--", rel_path).returncode != 0:
        return "untracked"
    return "shallow_clone" if _is_shallow(root) else "approval_uncommitted"


def resolve_requirement_baseline(prd_id: str, prd_path: Path) -> BaselineResolution:
    """Resolve *prd_id*'s baseline from the first-parent history of its PRD directory.

    *prd_path* is the working-tree file the scope resolver returned; its parent
    directory is where the ID's history is looked up. Raises only when ``git log``
    itself fails in a repository that has commits; the caller reports that as
    ``not_evaluated``.
    """
    prd_path = prd_path.resolve()
    root = _repo_root(prd_path)
    if root is None:
        return BaselineResolution(prd_id=prd_id, status="baseline_unresolvable", reason="no_git")
    try:
        current_text = prd_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # trw-fail-silent-allow: an unreadable current file is the named current_unparseable reason
        current_text = ""
    current = _status_of(parse_frontmatter(current_text))
    if current is None:
        return BaselineResolution(prd_id, "baseline_unresolvable", "current_unparseable", repo_root=root)

    rel_path = prd_path.relative_to(root.resolve()).as_posix()
    prds_dir = rel_path.rpartition("/")[0]
    history = _history(root, prds_dir, prd_id)
    boundary = re.compile(rf"^{re.escape(prd_id)}(?!\d)")
    if history is None or sum(1 for p in prd_path.parent.iterdir() if p.is_file() and boundary.match(p.name)) >= 2:
        return BaselineResolution(prd_id, "baseline_unresolvable", "ambiguous_id", current, root)

    read = [_read_version(root, sha, date, path) for sha, date, path in reversed(history)]
    versions = [version for version in read if version is not None]
    if len(versions) != len(read):
        return BaselineResolution(prd_id, "baseline_unresolvable", "version_unreadable", current, root)
    approvals: list[_Version] = []
    for index, version in enumerate(versions):
        approved = version.status in APPROVED_FAMILY
        if approved and (index == 0 or versions[index - 1].status not in APPROVED_FAMILY):
            approvals.append(version)
    safety = any(v.status in APPROVED_FAMILY and v.frontmatter.get("safety_critical") is True for v in versions)
    implemented = any(v.status in _IMPLEMENTED_FAMILY for v in versions)

    if not approvals:
        if current not in APPROVED_FAMILY:
            return BaselineResolution(prd_id, "not_applicable", None, current, root)
        reason = _no_approval_reason(root, rel_path, bool(history))
        return BaselineResolution(prd_id, "baseline_unresolvable", reason, current, root)

    baseline = approvals[0]
    findings: list[BaselineFinding] = []
    if len(approvals) >= 2:
        findings.append(BaselineFinding("baseline_reapproved", baseline.sha, approvals[-1].sha))
    if current not in APPROVED_FAMILY:
        findings.append(BaselineFinding("status_regressed", current_status=current))
    if versions[0].status in APPROVED_FAMILY and _is_shallow(root):
        findings.append(BaselineFinding("shallow_clone", first_sha=versions[0].sha))
    chains, requirements = _requirements(prd_id, baseline.frontmatter, baseline.text)
    return BaselineResolution(
        prd_id=prd_id,
        status="resolved",
        current_status=current,
        repo_root=root,
        baseline_sha=baseline.sha,
        baseline_date=baseline.date,
        baseline_path=baseline.path,
        approval_shas=tuple(v.sha for v in approvals),
        approval_dates=tuple(v.date for v in approvals),
        requirements=requirements,
        chains=chains,
        findings=tuple(findings),
        safety_critical_in_history=safety,
        implemented_in_history=implemented,
    )
