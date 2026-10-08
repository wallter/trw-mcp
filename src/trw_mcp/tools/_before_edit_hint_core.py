"""Compute path of ``trw_code(mode="hint")``, kept free of server imports.

The PreToolUse hooks (claude_code, cursor, copilot) spawn a fresh interpreter
per edit and import this module inside a 2.5 s budget. ``fastmcp`` alone costs
0.76 s to import on macOS (measured 2026-09-17) and is only needed to register
the tool, so registration lives in ``tools/code.py`` and everything the hook
needs lives here.

IP boundary (trw-distill is PROPRIETARY; trw-mcp is PUBLIC): this module never
imports the proprietary package. The cross-package contract is the sidecar
envelope ``risk-report-sidecar/v0``, mirrored field by field in
:class:`BeforeYouEditHintPayload`. The learnings half always returns, with or
without a sidecar.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from trw_mcp.state._entitlements import DISTILL_SIDECAR_FEATURE

# c749 (PRD-DIST-2002): LearningSummary extracted to shared
# `_learnings_collector` module. Re-exported here for backward
# compatibility with callers that still import from this module.
from trw_mcp.tools import _sidecar_substrate
from trw_mcp.tools._learnings_collector import LearningSummary
from trw_mcp.tools._sidecar_substrate import CurrentSidecarResult, CurrentSidecarStatus

if TYPE_CHECKING:
    from trw_mcp.tools._sidecar_ancestry import GitReader

_logger = structlog.get_logger(__name__)

# Derived from the substrate, never re-spelled: a hand-copied constant is how
# the two drifted in the first place.
_SCHEMA_VERSION_ACCEPTED: str = _sidecar_substrate.SCHEMA_VERSION_ACCEPTED
_ARTIFACT_NAME_SINGLE: str = "before-edit-hint"
_ARTIFACT_NAME_BATCH: str = _sidecar_substrate.ANCESTOR_ARTIFACT
_TIER_FEATURE: str = DISTILL_SIDECAR_FEATURE

#: Statuses in which the substrate returned BEFORE consulting any artifact, so
#: a second lookup would repeat the same negative at the cost of two more git
#: subprocesses. Every other status means an artifact path was actually
#: examined, and the batch artifact is then worth examining too.
_NO_ARTIFACT_CONSULTED: frozenset[str] = frozenset({"tier_required", "no_repo_root", "no_git_sha"})

#: This tool adds exactly one status to the shared vocabulary: the sidecar
#: loaded cleanly but describes a DIFFERENT file. Everything else is the
#: substrate's closed set, so a new shared status arrives here automatically.
BeforeEditHintStatus = CurrentSidecarStatus | Literal["target_not_in_sidecar"]

#: Statuses that carry a sidecar hint and record as tier T2. The edit hooks
#: import this rather than re-spelling it.
T2_STATUSES: frozenset[str] = frozenset({"hint_available", "hint_available_stale"})

#: The single-file statuses a batch refusal may replace: only "nothing here",
#: never a more specific finding about an artifact that does exist.
_BATCH_REFUSALS: frozenset[str] = frozenset({"sidecar_too_far_behind", "sidecar_diff_failed", "sidecar_malformed"})

#: Statuses in which the entitlement gate never ran, so "was this edit
#: eligible?" has no answer. ``tier_required`` means checked-and-denied;
#: ``no_repo_root`` means the substrate returned before reaching the gate.
#: Emitting ``eligible: True`` for either would write a claim into durable
#: telemetry that no check ever made — the same defect as the ``stale_sha``
#: mislabel this module was migrated to fix.
_ELIGIBILITY_UNDETERMINED: frozenset[str] = frozenset({"tier_required", "no_repo_root"})


#: PRD-DIST-2482 FR02 mirror. ``ok``: read, 1+ cited; ``none_cited``: read, 0
#: cited; ``daemon_unavailable`` and ``page_cap_reached`` mean the lesson check
#: could not finish, so an empty ``lessons`` list proves nothing.
LessonsStatus = Literal["ok", "none_cited", "daemon_unavailable", "page_cap_reached"]


#: HINT-RECALL-BUDGET: whether the T1 learnings half of the hint (``_collect_learnings``)
#: finished inside ``hint_recall_deadline_ms``. ``recall_timeout`` means the recall was
#: abandoned and ``learnings`` is empty by construction, not because nothing matched;
#: the T2 sidecar half of the hint is unaffected either way.
LearningsRecallStatus = Literal["ok", "recall_timeout"]


class EditLessonPayload(BaseModel):
    """Mirror of trw-distill ``EditLesson``: a distilled lesson whose evidence cites the file.

    ``extra="ignore"`` (not ``"forbid"``): this is the READ side of the
    cross-package contract, and trw-distill is versioned independently of
    trw-mcp. A newer trw-distill adding an additive field to this shape must
    not make an older trw-mcp report ``sidecar_malformed`` and drop the whole
    hint over a field it never needed — see the parent docstring's IP-boundary
    note. Known fields still validate strictly (a wrong type on a KNOWN field
    is still malformed); only unrecognized keys are dropped, and the caller
    logs their names once per parse via ``_log_ignored_fields`` so drift stays
    visible without failing the read.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")

    id: str = Field(min_length=1)
    sha: str
    summary: str = Field(max_length=160)


class BeforeYouEditHintPayload(BaseModel):
    """Cross-package shape pin against c734 BeforeYouEditHint.

    Field-by-field mirror of the trw-distill model. We CANNOT import
    the source class (IP boundary), so this is hand-maintained against
    the envelope contract. If trw-distill bumps the envelope
    schema_version, the tool returns ``schema_mismatch`` until this
    mirror is updated.

    ``extra="ignore"``: see :class:`EditLessonPayload` — same read-side
    forward-compatibility rationale applies at the top level.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")

    # Constraints mirror the trw-distill source; parity-checked by
    # scripts/check-schema-mirror-parity.py (PRD-INFRA-134 FR-05).
    target_path: str = Field(min_length=1)
    target_exists_in_map: bool
    importers: list[str] = Field(default_factory=list)
    inferred_tests: list[str] = Field(default_factory=list)
    doc_references: list[str] = Field(default_factory=list)
    co_change_neighbors: list[str] = Field(default_factory=list)
    hotspot_warnings: list[str] = Field(default_factory=list)
    risk_score: float | None = None
    lessons: list[EditLessonPayload] = Field(default_factory=list, max_length=2)
    lessons_status: LessonsStatus | None = None


class SidecarAsOf(BaseModel):
    """Provenance of a ``hint_available_stale`` hint: the ancestor sidecar it came from.

    ``target_changed`` records whether the target differs from that snapshot;
    when it does, the content-dependent fields were dropped before validation.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    sidecar_sha: str
    commits_behind: int = Field(ge=0)
    target_changed: bool


class BeforeEditHintResult(BaseModel):
    """Two-source hint result.

    Both halves are independent: ``distill_hint`` may be None (tier
    ungated or sidecar missing) while ``learnings`` is populated, or
    vice versa.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    file_path: str
    tier: str
    distill_hint: BeforeYouEditHintPayload | None = None
    distill_status: BeforeEditHintStatus = "sidecar_missing"
    distill_action: str | None = None
    distill_sidecar_path: str | None = None
    distill_sidecar_sha: str | None = None
    learnings: list[LearningSummary] = Field(default_factory=list)
    learnings_count: int = 0
    learnings_status: LearningsRecallStatus = "ok"
    distill_as_of: SidecarAsOf | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_as_of(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Leave ``distill_as_of`` and an ``ok`` ``learnings_status`` out, so every other result dumps as before.

        Both are advisory: each response key is paid on every hint, so a field appears only when it
        carries signal (a stale hint's as-of, a recall that timed out).
        """
        data: dict[str, Any] = handler(self)
        absent = {"distill_as_of": self.distill_as_of is None, "learnings_status": self.learnings_status == "ok"}
        return {key: value for key, value in data.items() if not absent.get(key, False)}


def _cli_remediation(file_path: str) -> str:
    """Exact command that regenerates this file's single-file sidecar."""
    return f"cd <repo> && trw-distill self-improve before-edit --repo . --file {file_path} --persist-sidecar"


def _batch_miss_action(file_path: str) -> str:
    """Remediation when a batch artifact exists but does not cover *file_path*.

    Names the batch artifact explicitly. "Regenerate the sidecar" is not
    actionable when the reader cannot tell which of two artifacts was read. The
    batch holds one hint per artifact of the map at the commit it was built from
    (trw-distill ``compute_whole_map_batch``), so a file absent from it is new
    since that commit or outside the map -- never "a file the last commit did not
    touch" (E2E-INC-056).
    """
    return (
        f"Batch sidecar does not cover {file_path!r} (it covers every file in the map at the commit it was"
        f" built from, so this file is new since then or outside the map) — run: {_cli_remediation(file_path)}"
    )


def _log_ignored_fields(payload: dict[str, Any], known_fields: frozenset[str], *, shape: str) -> None:
    """Log (once per parse) any top-level keys ``model_validate`` will silently drop.

    ``extra="ignore"`` on the mirror models makes forward-compat additive
    fields harmless, but silent drift is still drift — this keeps it visible
    in the logs without failing the read. One info line per call, naming every
    ignored field, rather than one line per field.
    """
    extra = sorted(set(payload) - known_fields)
    if extra:
        _logger.info("before_edit_hint.sidecar_extra_fields_ignored", shape=shape, fields=extra)


def _log_lesson_extra_fields(payload: dict[str, Any]) -> None:
    """Same as :func:`_log_ignored_fields` but for each entry of ``payload["lessons"]``."""
    lessons = payload.get("lessons")
    if not isinstance(lessons, list):
        return
    known = frozenset(EditLessonPayload.model_fields)
    for entry in lessons:
        if isinstance(entry, dict):
            _log_ignored_fields(entry, known, shape="lesson")


def _select_distill_hint(
    payload: Any,
    file_path: str,
) -> tuple[
    BeforeYouEditHintPayload | None,
    Literal["hint_available", "sidecar_malformed", "target_not_in_sidecar"],
    str | None,
]:
    """Validate an already-loaded sidecar payload against the requested file.

    The substrate has already proved the envelope exists, carries the accepted
    schema_version, and matches HEAD. What is left is this tool's own contract:
    the payload must describe ``file_path`` and must satisfy the mirror model.

    NEVER raises — every failure path returns (None, status, action).
    """
    if not isinstance(payload, dict):
        return (
            None,
            "sidecar_malformed",
            "Sidecar payload is not a dict; re-run --persist-sidecar to regenerate",
        )
    payload_target = payload.get("target_path")
    if payload_target != file_path:
        return (
            None,
            "target_not_in_sidecar",
            f"Sidecar for target_path={payload_target!r}; requested "
            f"{file_path!r} — run: trw-distill self-improve before-edit "
            f"--repo . --file {file_path} --persist-sidecar",
        )
    _log_ignored_fields(payload, frozenset(BeforeYouEditHintPayload.model_fields), shape="hint")
    _log_lesson_extra_fields(payload)
    try:
        hint = BeforeYouEditHintPayload.model_validate(payload)
    except Exception:
        return (
            None,
            "sidecar_malformed",
            "Sidecar payload does not match BeforeYouEditHintPayload schema; check trw-distill version compatibility",
        )
    return (hint, "hint_available", None)


def _find_batch_entry(
    payload: Any, keys: tuple[str, ...]
) -> dict[str, Any] | Literal["sidecar_malformed", "target_not_in_sidecar"]:
    """This file's entry in an already-loaded ``before-edit-batch`` payload, or why there is none.

    NEVER raises. The two negative statuses are kept apart deliberately: "the
    batch does not mention this file" and "the batch mentions it but the entry
    does not parse" are different operator problems, and collapsing the second
    into the first would report a schema break as an absent target.
    """
    if not isinstance(payload, dict):
        return "sidecar_malformed"
    hints = payload.get("hints")
    if not isinstance(hints, list):
        return "sidecar_malformed"
    for entry in hints:
        if isinstance(entry, dict) and entry.get("target_path") in keys:
            return entry
    return "target_not_in_sidecar"


_BatchPick = tuple[BeforeYouEditHintPayload | None, BeforeEditHintStatus, "SidecarAsOf | None", str | None]


def _select_from_batch(
    batch: CurrentSidecarResult,
    keys: tuple[str, ...],
    git_reader: GitReader | None,
) -> _BatchPick:
    """Pick this file's hint out of a loaded batch: ``(hint, status, as_of, action)``.

    A stale entry is reduced per field first. Its target counts as changed
    when the diff since the sidecar names it, when the builder recorded it in
    the envelope's ``dirty_paths``, or when ``git status`` shows it
    staged, unstaged or untracked (asked every time, never cached); a git
    failure there is ``sidecar_diff_failed``, and no hint is shown.
    """
    from trw_mcp.tools import _sidecar_ancestry as ancestry

    found = _find_batch_entry(batch.payload, keys)
    if isinstance(found, str):
        return (None, found, None, None)
    ancestor = batch.ancestor
    as_of: SidecarAsOf | None = None
    if ancestor is not None and batch.repo_root is not None:
        target = str(found["target_path"])
        git = git_reader or ancestry.SubprocessGitReader(batch.repo_root, deadline=batch.deadline)
        try:
            changed = target in ancestor.changed or target in ancestor.dirty_paths or git.worktree_changed(target)
        except ancestry.GitReadError as err:
            action = (
                f"Could not check {target!r} against the working tree ({err}); learnings only ({ancestry.FLAG_DISABLE})"
            )
            return (None, "sidecar_diff_failed", None, action)
        found = ancestry.filter_stale_entry(found, changed=ancestor.changed, target_changed=changed)
        if ancestor.commits_behind or changed:
            as_of = SidecarAsOf(
                sidecar_sha=ancestor.sha, commits_behind=ancestor.commits_behind, target_changed=changed
            )
    _log_ignored_fields(found, frozenset(BeforeYouEditHintPayload.model_fields), shape="hint")
    _log_lesson_extra_fields(found)
    try:
        hint = BeforeYouEditHintPayload.model_validate(found)
    except Exception:
        return (None, "sidecar_malformed", None, None)
    return (hint, batch.status, as_of, None)


def _collect_learnings(file_path: str, repo_root: str | None) -> tuple[list[LearningSummary], LearningsRecallStatus]:
    """The shared collector over ``[path, basename]``, lessons anchored to the file first.

    PRD-CORE-332 FR06/FR08: the anchored recall uses the file's repo-relative
    path; a path outside the root or through a symlink gets the text queries only.

    HINT-RECALL-BUDGET: runs single-paged (``single_page=True`` — no
    ``take_hits`` growth loop) on a worker thread bounded by
    ``hint_recall_deadline_ms``. A recall over the daemon socket can run well
    past a hook's PreToolUse budget (measured: 4.76s of a 5.98s hint against a
    2522-entry store); past the deadline this returns ``([], "recall_timeout")``
    without waiting on the worker, so the T2 sidecar half of the hint is never
    held hostage by a slow T1 recall. The abandoned worker is not joined or
    cancelled out from under the daemon's own event loop — it runs to
    completion on its own and its result is simply discarded, so nothing hangs
    and no extra daemon connection is opened either way (raise
    ``hint_recall_deadline_ms`` in ``.trw/config.yaml`` to wait longer).
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._learnings_collector import (
        build_file_queries,
        collect_learnings,
    )

    queries = build_file_queries(file_path)
    anchor_file = _anchor_key(file_path, repo_root)
    deadline_s = get_config().hint_recall_deadline_ms / 1000.0

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="hint-recall-budget")
    future = executor.submit(collect_learnings, queries, anchor_file=anchor_file, single_page=True, must_name=file_path)
    try:
        learnings = future.result(timeout=deadline_s)
    except concurrent.futures.TimeoutError:
        future.cancel()  # best-effort; the recall already in flight is abandoned, not force-stopped
        _logger.info(
            "before_edit_hint.recall_timeout",
            file_path=file_path,
            deadline_ms=get_config().hint_recall_deadline_ms,
            disable="raise hint_recall_deadline_ms in .trw/config.yaml",
        )
        return [], "recall_timeout"
    finally:
        executor.shutdown(wait=False)
    return learnings, "ok"


def _under_root(file_path: str, repo_root: str | None) -> tuple[Path, str] | None:
    """The root *file_path* lies under and its POSIX path relative to it, or None outside it.

    Lexical (``normpath``) rather than ``resolve()``: the file may not exist
    yet. Only the root is also tried resolved, so /var vs /private/var still
    matches. *repo_root* wins over the ambient project root when given.
    """
    import os

    from trw_mcp.state._paths import resolve_project_root

    root = Path(repo_root) if repo_root else resolve_project_root()
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    normalized = Path(os.path.normpath(candidate))
    for base in (root, root.resolve()):
        if normalized.is_relative_to(base):
            relative = normalized.relative_to(base).as_posix()
            return (base, relative) if relative not in ("", ".") else None
    return None


def _repo_relative_path(file_path: str) -> str | None:
    """POSIX path of *file_path* relative to the project root, or None outside it.

    A symlink inside the repo is still the path the agent edits, so exposure
    rows keep it; only the anchor key (:func:`_anchor_key`) refuses one.
    """
    found = _under_root(file_path, None)
    return found[1] if found else None


def _anchor_key(file_path: str, repo_root: str | None) -> str | None:
    """The repo-relative path anchors are matched on, or None when no anchored lookup may run (FR08).

    None outside the root, and when the path or any existing parent below the
    root is a symlink: an anchor names the path it was written against, and a
    link would match a lesson about a different file. One ``lstat`` per component.
    """
    import os

    found = _under_root(file_path, repo_root)
    if found is None:
        _logger.debug("anchor_lookup_skipped", reason="outside_root")
        return None
    base, relative = found
    parts = relative.split("/")
    if any(os.path.islink(base.joinpath(*parts[:depth])) for depth in range(1, len(parts) + 1)):
        _logger.debug("anchor_lookup_skipped", reason="symlink")
        return None
    return relative


def _record_exposure(file_path: str, learnings: list[LearningSummary]) -> None:
    """PRD-FIX-144 FR02: record which learnings this hint showed, for which file.

    One receipt and one surface row per learning; nothing when no learning was
    shown. Rows hold ids and the repo-relative path only (NFR04). Fail-open: a
    write failure never changes the hint.

    The caller skips this under the reviewer role: PRD-CORE-300-FR12 makes the
    reviewer's hint write nothing, superseding PRD-SEC-015 NFR03's acknowledged
    residual telemetry write.
    """
    if not learnings:
        return
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state._session_id import resolve_effective_session_id
        from trw_mcp.state.recall_tracking import append_receipts
        from trw_mcp.state.surface_tracking import log_surface_event

        relative = _repo_relative_path(file_path)
        if relative is None:
            _logger.debug("before_edit_exposure_path_outside_root")
        files_context = [relative] if relative else []
        trw_dir = resolve_trw_dir()
        ids = [item.id for item in learnings]
        append_receipts(ids, relative or "", surface="before_edit_hint", files_context=files_context, trw_dir=trw_dir)
        session_id = resolve_effective_session_id(trw_dir)
        for learning_id in ids:
            log_surface_event(
                trw_dir,
                learning_id=learning_id,
                surface_type="before_edit_hint",
                files_context=files_context,
                session_id=session_id,
            )
    except Exception:  # justified: fail-open, exposure telemetry must not change the hint
        _logger.debug("before_edit_exposure_record_failed", exc_info=True)


def compute_before_edit_hint(
    *,
    file_path: str,
    repo_root: str | None = None,
    cache_dir: str | None = None,
    git_reader: GitReader | None = None,
) -> BeforeEditHintResult:
    """Pure-Python entry point for ``trw_code(mode="hint")``, the edit hooks and tests.

    Under the reviewer role it writes nothing: no delivery telemetry and no
    exposure rows (PRD-CORE-300-FR12). With ``hint_sidecar_ancestor_enabled``
    on, a missing exact-HEAD batch sidecar falls back to the nearest proven
    ancestor (``hint_available_stale`` plus ``distill_as_of``). ``git_reader``
    is the test seam for that path's git questions. This path never requests a build.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._surface_role import reviewer_role_active

    reviewer = reviewer_role_active()
    learnings, learnings_status = _collect_learnings(file_path, repo_root)

    # Repo root, entitlement gate, HEAD sha, envelope + schema + sha checks all
    # come from the shared substrate. Its status vocabulary distinguishes the
    # three "we never got as far as comparing" cases — `no_repo_root`,
    # `tier_required`, `no_git_sha` — from `stale_sha`, which is a real
    # comparison that disagreed. The tier the substrate reports already folds in
    # the trw-distill-installed unlock (tier="proprietary" without a sentinel).
    sidecar = _sidecar_substrate.resolve_current_sidecar(
        repo_root=repo_root,
        cache_dir=cache_dir,
        feature=_TIER_FEATURE,
        artifact_name=_ARTIFACT_NAME_SINGLE,
        cli_remediation=_cli_remediation(file_path),
    )

    distill_hint: BeforeYouEditHintPayload | None = None
    # No remediation nag on the tier-gated path: when trw-distill is not
    # installed the sidecar feature is simply unavailable, and emitting a
    # paid-tier remediation on every edit would burn caller tokens for a feature
    # not opted into. `resolve_current_sidecar` already returns action=None
    # there. The learnings half below always returns, preserving value at any
    # tier.
    distill_status: BeforeEditHintStatus = sidecar.status
    distill_action: str | None = sidecar.action
    distill_sidecar_path: str | None = sidecar.sidecar_path
    distill_sidecar_sha: str | None = sidecar.sidecar_sha
    distill_as_of: SidecarAsOf | None = None
    batch: CurrentSidecarResult | None = None
    if sidecar.status == "hint_available":
        distill_hint, distill_status, distill_action = _select_distill_hint(sidecar.payload, file_path)
        if distill_hint is not None:
            distill_hint, distill_status, distill_as_of, distill_action = _select_from_batch(
                replace(sidecar, payload={"hints": [sidecar.payload]}), (file_path,), git_reader
            )

    # The single-file artifact holds ONE hint for the whole repo at a given sha
    # — `before-edit-hint-<sha>.json` carries no per-file discriminator — so on
    # any commit touching more than one file at most one target can be served
    # from it. The post-commit refresh therefore emits the BATCH artifact, which
    # holds one hint per target, and this is where it gets consumed. The module
    # docstring listed batch consumption as deferred v1 scope; without it the
    # producer fix would have written artifacts nothing reads.
    if distill_status != "hint_available" and sidecar.status not in _NO_ARTIFACT_CONSULTED:
        config = get_config()
        ancestor_on = config.hint_sidecar_ancestor_enabled
        batch = _sidecar_substrate.resolve_current_sidecar(
            repo_root=repo_root,
            cache_dir=cache_dir,
            feature=_TIER_FEATURE,
            artifact_name=_ARTIFACT_NAME_BATCH,
            cli_remediation=_cli_remediation(file_path),
            ancestor_bound=config.hint_sidecar_max_commits_behind if ancestor_on else None,
            git_reader=git_reader,
            persist_ancestry=not reviewer,
        )
        if batch.status in T2_STATUSES:
            # Batch entries are keyed repo-relative; the edit hooks pass the
            # absolute path. Matching both is part of the flagged read path.
            relative = _under_root(file_path, str(batch.repo_root)) if ancestor_on and batch.repo_root else None
            keys = (file_path, relative[1]) if relative else (file_path,)
            batch_hint, batch_status, batch_as_of, batch_action = _select_from_batch(batch, keys, git_reader)
            # Only ADOPT the batch outcome — never let a batch miss overwrite a
            # more specific single-file finding with a vaguer one. A batch that
            # cannot answer leaves the single-file status exactly as it was.
            if batch_hint is not None or distill_status == "sidecar_missing":
                distill_hint = batch_hint
                distill_status = batch_status
                distill_action = batch_action or (None if batch_hint is not None else _batch_miss_action(file_path))
                distill_sidecar_path = batch.sidecar_path
                distill_as_of = batch_as_of
                if batch_hint is not None:
                    distill_sidecar_sha = batch.sidecar_sha
        elif ancestor_on and batch.status in _BATCH_REFUSALS and distill_status == "sidecar_missing":
            # A more specific "why no hint" than the single-file miss it
            # replaces. Flag off never adopts one, so its output stays as it was.
            distill_status = batch.status
            distill_action = batch.action
            distill_sidecar_path = batch.sidecar_path

    # PRD-CORE-231-FR01: record every ELIGIBLE edit in durable telemetry.
    # "Eligible" == the entitlement gate ran AND allowed the feature. Misses are
    # recorded too — a gate computed only from hits would be a survivorship
    # statistic — but a status in `_ELIGIBILITY_UNDETERMINED` means the gate
    # never produced an answer, and `eligible: True` there would be a fabricated
    # one. This is the single emission point shared by the CC-03 hook subprocess
    # and the direct MCP-tool path.
    if distill_status not in _ELIGIBILITY_UNDETERMINED and not reviewer:
        from trw_mcp.channels._distill_telemetry import emit_hint_delivered

        staleness: dict[str, Any] = {}
        if distill_as_of is not None:
            staleness = {
                "sidecar_commits_behind": distill_as_of.commits_behind,
                "target_changed_since_sidecar": distill_as_of.target_changed,
            }
        emit_hint_delivered(
            tier="T2" if distill_status in T2_STATUSES else sidecar.tier,
            distill_status=distill_status,
            file_path=file_path,
            **staleness,
        )

    if not reviewer:
        _record_exposure(file_path, learnings)

    return BeforeEditHintResult(
        file_path=file_path,
        tier=sidecar.tier,
        distill_hint=distill_hint,
        distill_status=distill_status,
        distill_action=distill_action,
        # Which artifact answered is visible here — the filename carries
        # `before-edit-hint-` or `before-edit-batch-`. That is the only way an
        # operator can tell a single-file hit from a batch hit, and it costs no
        # extra response field.
        distill_sidecar_path=distill_sidecar_path,
        distill_sidecar_sha=distill_sidecar_sha,
        learnings=learnings,
        learnings_count=len(learnings),
        learnings_status=learnings_status,
        distill_as_of=distill_as_of,
    )


__all__ = [
    "T2_STATUSES",
    "BeforeEditHintResult",
    "BeforeEditHintStatus",
    "BeforeYouEditHintPayload",
    "CurrentSidecarStatus",
    "LearningSummary",
    "LearningsRecallStatus",
    "SidecarAsOf",
    "compute_before_edit_hint",
]
