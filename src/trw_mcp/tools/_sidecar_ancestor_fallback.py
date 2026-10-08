"""Resolve and validate the nearest usable ancestor within one shared time budget."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from trw_mcp.tools import _sidecar_ancestry as ancestry
from trw_mcp.tools._sidecar_ancestry import AncestorSidecar, GitReader
from trw_mcp.tools._sidecar_envelope import _NO_PRODUCER_ACTION, DEFAULT_CACHE_DIR_REL, load_sidecar_with_sha_check
from trw_mcp.tools._sidecar_substrate import CurrentSidecarResult, CurrentSidecarStatus


def _resolve_ancestor(
    *,
    repo_root: Path,
    search_dir: Path | None,
    head: str,
    tier: str,
    bound: int,
    git_reader: GitReader | None,
    cli_remediation: str | None,
    persist: bool,
    deadline: float | None = None,
) -> CurrentSidecarResult:
    """No exact-HEAD artifact: answer from the nearest proven-ancestor batch sidecar, or say why not."""
    cache_dir = search_dir or ancestry.shared_cache_dir(repo_root, DEFAULT_CACHE_DIR_REL)
    git = git_reader or ancestry.SubprocessGitReader(repo_root)
    run = f"run: {cli_remediation}" if cli_remediation else _NO_PRODUCER_ACTION
    base = CurrentSidecarResult(
        tier=tier, payload=None, status="sidecar_missing", sidecar_sha=head, repo_root=repo_root, deadline=deadline
    )
    excluded: set[Path] = set()
    rejected: CurrentSidecarResult | None = None
    deadline = time.monotonic() + 1.0 if deadline is None else deadline  # the lookup's one shared second
    while True:
        try:
            outcome = ancestry.find_ancestor_sidecar(
                cache_dir,
                head,
                git=git,
                max_commits_behind=bound,
                persist=persist,
                excluded=frozenset(excluded),
                deadline=deadline,
            )
        except ancestry.GitReadError as err:
            outcome = ancestry.AncestryOutcome(status="git_failed", reason=str(err))
        if outcome.ancestor is None:
            break
        loaded = _load_ancestor(base, outcome.ancestor, cli_remediation)
        if loaded.payload is not None:
            return loaded
        rejected = rejected or loaded
        excluded.add(outcome.ancestor.path)
    if rejected is not None:
        return rejected
    if outcome.status == "no_candidates":
        return replace(base, action=f"Run: {cli_remediation}" if cli_remediation else _NO_PRODUCER_ACTION)
    if outcome.status == "git_failed":
        return replace(
            base,
            status="sidecar_diff_failed",
            action=f"Could not compare cached sidecars with HEAD ({outcome.reason}); learnings only ({ancestry.FLAG_DISABLE})",
        )
    if outcome.status == "too_far_behind" or outcome.ancestor is None:
        nearest = outcome.nearest_commits_behind
        where = (
            "no cached sidecar is an ancestor of HEAD"
            if nearest is None
            else f"the nearest is {nearest} commits behind"
        )
        return replace(
            base,
            status="sidecar_too_far_behind",
            action=f"No sidecar within hint_sidecar_max_commits_behind={bound} ({where}); {run} ({ancestry.FLAG_DISABLE})",
        )
    return _load_ancestor(base, outcome.ancestor, cli_remediation)


def _load_ancestor(
    base: CurrentSidecarResult, ancestor: AncestorSidecar, cli_remediation: str | None
) -> CurrentSidecarResult:
    """Validate the chosen ancestor's envelope; a corrupt file is ``sidecar_malformed``, never "missing"."""
    from trw_mcp.tools._sidecar_ancestry import parse_dirty_paths

    load = load_sidecar_with_sha_check(ancestor.path, expected_sha=ancestor.sha, cli_remediation=cli_remediation)
    located = replace(base, sidecar_path=str(ancestor.path), sidecar_existed=True)
    if load.status == "ok":
        dirty = parse_dirty_paths(load.dirty_paths)
        if dirty is None:
            action = f"Sidecar dirty_paths is not a list of paths; rebuild {ancestor.path.name}"
            return replace(located, status="sidecar_malformed", action=action)
        fresh = ancestor.commits_behind == 0
        return replace(
            located,
            payload=load.payload,
            status="hint_available" if fresh else "hint_available_stale",
            sidecar_sha=ancestor.sha,
            ancestor=replace(ancestor, dirty_paths=dirty),
        )
    status: CurrentSidecarStatus = "sidecar_malformed" if load.status == "sidecar_missing" else load.status
    return replace(located, status=status, action=load.action)
