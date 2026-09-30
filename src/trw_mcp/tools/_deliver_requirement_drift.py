"""PRD-CORE-321 FR04, FR05: the requirement-drift report and gate for one ``trw_deliver`` call.

:func:`compute_requirement_drift` runs once per deliver, from
``evaluate_delivery_gates`` immediately after the decision-set receipts are
persisted and before any gate can return, so the report reaches the response
even when an earlier gate blocks. It reads the run's scope union
(``declared_scope_union``, shared with the safety-critical gate), resolves each
entry with ``_resolve_prd_scope``, resolves the git baseline (FR01), detects
drift and orphans (FR02, FR03), matches each finding against the PRD's
``## Requirement Amendments`` rows (FR04) and sets each entry's effective mode
(FR05). It writes nothing and has no journal step.

:func:`drift_blocks_task` reads whether ``deliver_gate_mode`` blocks the run's
task type; :func:`drift_warning` names every block-eligible item that will not
block; :func:`apply_requirement_drift_gate`, called after ``evaluate_formation``,
turns the items of block-mode PRDs into a STRUCTURED hard block whose only
override is the PRD-CORE-191 acceptable-failure record.

Runtime caller: ``trw_mcp.tools._deliver_gate_dispatch.evaluate_delivery_gates``
-> :func:`compute_requirement_drift`, :func:`drift_blocks_task`,
:func:`drift_warning` and :func:`apply_requirement_drift_gate`, reached from
``trw_deliver`` through ``run_trw_deliver``.

Soundness scope: proves each entry of the run's scope union produces exactly one
entry with a named ``baseline_status`` (NFR02), that a checker fault on one PRD
is reported ``not_evaluated`` for that PRD rather than dropping it, and that the
block decision follows the effective mode and ``deliver_gate_mode``. Does not
prove the declared scope names every PRD the work touched: a PRD omitted from
both ``prd_scope`` and every review receipt is not checked.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import (
        DeliverResultDict,
        RequirementDriftEntry,
        RequirementDriftFinding,
        RequirementDriftReport,
    )
    from trw_mcp.state.validation.requirement_baseline import BaselineFinding, BaselineResolution

logger = structlog.get_logger(__name__)

EntryStatus = Literal["resolved", "baseline_unresolvable", "not_evaluated", "not_applicable"]
Mode = Literal["warn", "block"]

#: Entry statuses with no findings of their own that still leave the PRD unchecked.
_UNCHECKED_STATUSES: frozenset[str] = frozenset({"baseline_unresolvable", "not_evaluated"})


def _current_safety(prd_file: Path | None) -> bool | None:
    """The current version's ``safety_critical: true``; ``None`` when the flag cannot be read.

    Unreadable means no file, an unreadable one, or no parseable frontmatter
    (``parse_frontmatter`` returns ``{}`` for a missing block or a YAML error).
    A PRD resolved ``current_unparseable`` is passed here as ``None`` by its caller.
    """
    from trw_mcp.state.prd_utils import parse_frontmatter

    if prd_file is None:
        return None
    try:
        frontmatter = parse_frontmatter(prd_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):  # trw-fail-silent-allow: None forces block in _effective_mode (fail-closed)
        return None
    if not frontmatter:
        return None
    return frontmatter.get("safety_critical") is True


def _effective_mode(configured: Mode | None, prd_file: Path | None, safety_in_history: bool = False) -> Mode:
    """FR05: *configured* (``requirement_drift_gate``) when set, else block for a safety-critical PRD.

    Safety-critical means the current version declares it, any approved-or-later
    version in history did (so a re-approval dropping the flag does not
    downgrade the gate), or the flag cannot be read (fail-closed, matching the
    safety-critical gate's unreadable-PRD branch).
    """
    if configured is not None:
        return configured
    current = _current_safety(prd_file)
    return "block" if current is None or current or safety_in_history else "warn"


def _entry(
    status: EntryStatus, reason: str | None, mode: Mode, resolution: BaselineResolution | None = None
) -> RequirementDriftEntry:
    return {
        "baseline_status": status,
        "reason": reason,
        "baseline_sha": resolution.baseline_sha if resolution else None,
        "approval_date": resolution.baseline_date if resolution else None,
        "effective_mode": mode,
        "orphan_check": "not_applicable",
        "findings": [],
    }


def _baseline_finding(prd_id: str, finding: BaselineFinding) -> RequirementDriftFinding:
    reasons = {
        "baseline_reapproved": f"first approval {finding.first_sha}, newest approval {finding.newest_sha}",
        "status_regressed": f"current status {finding.current_status}",
        "shallow_clone": f"shallow clone: the oldest listed version {finding.first_sha} is already approved",
    }
    return {
        "requirement_id": prd_id,
        "kind": finding.kind,
        "changed_fields": [],
        "reason": reasons[finding.kind],
        "recorded": False,
        "record_reason": None,
    }


def _record(findings: list[RequirementDriftFinding], text: str, resolution: BaselineResolution, mode: Mode) -> None:
    """FR04: mark each recordable finding recorded or not, from the Amendments rows in *text*.

    A requirement-level finding is dated against the first approval and matched
    by its requirement id; ``baseline_reapproved`` and ``status_regressed`` are
    dated against the newest approval and matched by the PRD id. Every other
    kind is never recordable and keeps ``recorded=False``.
    """
    from trw_mcp.state.validation.requirement_drift import (
        PRD_ROW_KINDS,
        REQUIREMENT_ROW_KINDS,
        amendment_rows,
        match_amendment,
    )

    rows = amendment_rows(text)
    if not rows or resolution.baseline_date is None:
        return
    today = datetime.now(timezone.utc).date()
    first, newest = date.fromisoformat(resolution.baseline_date), date.fromisoformat(resolution.approval_dates[-1])
    for finding in findings:
        if finding["kind"] in REQUIREMENT_ROW_KINDS:
            match = match_amendment(finding["requirement_id"], rows, first, mode, today)
        elif finding["kind"] in PRD_ROW_KINDS:
            match = match_amendment(resolution.prd_id, rows, newest, mode, today)
        else:
            continue
        finding["recorded"] = match.recorded
        finding["record_reason"] = match.row.reason if match.row is not None else match.reason


def _resolved_entry(
    prd_file: Path, resolution: BaselineResolution, run: Path, configured: Mode | None
) -> RequirementDriftEntry:
    from trw_mcp.state.validation.requirement_drift import detect_requirement_drift, orphan_check_applies
    from trw_mcp.tools._deliver_capability_integration import _hook_evidence, _source_root

    if resolution.repo_root is None:  # a resolved baseline always carries its git root
        raise RuntimeError(f"resolved baseline for {resolution.prd_id} has no repo root")
    text = prd_file.read_text(encoding="utf-8")
    drift = detect_requirement_drift(
        resolution,
        text,
        current_status=resolution.current_status,
        repo_root=resolution.repo_root,
        source_root=_source_root(),  # the running package, never the git root (FR03(b))
        hook_evidence=_hook_evidence(run),
    )
    mode = _effective_mode(configured, prd_file, resolution.safety_critical_in_history)
    entry = _entry("resolved", None, mode, resolution)
    if orphan_check_applies(resolution, resolution.current_status):
        entry["orphan_check"] = "applied"
    entry["findings"] = [_baseline_finding(resolution.prd_id, finding) for finding in resolution.findings]
    entry["findings"] += [
        {
            "requirement_id": finding.requirement_id,
            "kind": finding.kind,
            "changed_fields": list(finding.changed_fields),
            "reason": finding.reason,
            "recorded": False,
            "record_reason": None,
        }
        for finding in drift
    ]
    _record(entry["findings"], text, resolution, mode)
    return entry


def _prd_entry(prd_id: str, scope_entry: str, run: Path, configured: Mode | None) -> RequirementDriftEntry:
    """One scope entry's report entry; any exception is the named ``not_evaluated`` status."""
    from trw_mcp.state.validation.requirement_baseline import resolve_requirement_baseline
    from trw_mcp.tools._plan_acceptance_gate import _resolve_prd_scope

    prd_file: Path | None = None
    resolution: BaselineResolution | None = None
    try:
        prd_files, _unresolved = _resolve_prd_scope([scope_entry])
        if not prd_files:
            return _entry("baseline_unresolvable", "prd_not_found", _effective_mode(configured, None))
        prd_file = prd_files[0]
        resolution = resolve_requirement_baseline(prd_id, prd_file)
        if resolution.status != "resolved":
            readable = None if resolution.reason == "current_unparseable" else prd_file
            return _entry(resolution.status, resolution.reason, _effective_mode(configured, readable), resolution)
        return _resolved_entry(prd_file, resolution, run, configured)
    except Exception as exc:  # justified: fail-loud, NFR02 names a checker fault not_evaluated, never an absent entry
        logger.warning("requirement_drift_not_evaluated", prd=prd_id, run=str(run), exc_info=True)
        # A fault after the baseline resolved keeps its history: the safety flag an approved version
        # held still decides the mode, so a checker exception cannot turn a block into a warning (core321-s3 r1).
        history = resolution.safety_critical_in_history if resolution is not None else False
        return _entry("not_evaluated", type(exc).__name__, _effective_mode(configured, prd_file, history), resolution)


def compute_requirement_drift(resolved_run: Path | None) -> RequirementDriftReport:
    """The FR05 report: one entry per id in the run's PRD scope union, keyed by PRD id.

    A scope entry that names no PRD id (a path or glob) is keyed by the entry
    itself and reported ``prd_not_found``. No run, or an empty union, gives
    ``{"scope": "not_declared", "prds": {}}``. An unreadable review receipt is
    reported as its own ``receipt:<stem>`` entry (``not_evaluated``,
    ``receipt_unreadable``, always ``warn``): reported, never dropped, never a block.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state.prd_utils import extract_prd_identifier
    from trw_mcp.tools._delivery_safety_critical_gate import _RUN_YAML_LABEL as RUN_YAML_LABEL
    from trw_mcp.tools._delivery_safety_critical_gate import DeclaredScope, declared_scope

    declared = declared_scope(resolved_run) if resolved_run is not None else DeclaredScope([])
    union = declared.union
    if resolved_run is None or not (union or declared.unreadable_receipts):
        return {"scope": "not_declared", "prds": {}}
    configured = get_config().requirement_drift_gate
    prds: dict[str, RequirementDriftEntry] = {}
    for stem in declared.unreadable_receipts:
        # DECISION (E2E-INC-106): an unreadable receipt is REPORTED here, never dropped and never
        # blocking. Its PRD ids are unknowable, so it cannot be attributed to a PRD's drift; the
        # safety-critical gate is the blocking authority for it (UNKNOWN_SCOPE). Mode is pinned to
        # "warn" regardless of requirement_drift_gate, so one corrupt file cannot mint a drift block.
        if stem == RUN_YAML_LABEL:  # E2E-INC-124: run metadata, not a receipt
            prds[f"run_metadata:{stem}"] = _entry("not_evaluated", "run_yaml_unreadable", "warn")
        else:
            prds[f"receipt:{stem}"] = _entry("not_evaluated", "receipt_unreadable", "warn")
    for scope_entry in union:
        prd_id = extract_prd_identifier(scope_entry) or scope_entry
        if prd_id not in prds:
            prds[prd_id] = _prd_entry(prd_id, scope_entry, resolved_run, configured)
    return {"scope": "declared", "prds": prds}


def drift_blocks_task(resolved_run: Path | None) -> bool:
    """FR05: whether ``deliver_gate_mode`` blocks this run's task type (``gate_mode_blocks_task``).

    No run: ``False`` (the report is then ``not_declared``, with nothing to block).
    An unreadable ``run.yaml`` is ``True``: fail-closed, and only block-mode
    PRDs are affected, each overridable by a PRD-CORE-191 record.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._deliver_gate_mode import gate_mode_blocks_task

    if resolved_run is None:
        return False
    run_yaml = resolved_run / "meta" / "run.yaml"
    try:
        run_data = FileStateReader().read_yaml(run_yaml) if run_yaml.is_file() else {}
    except Exception:  # justified: fail-closed, an unreadable run.yaml keeps block-mode PRDs blocking
        logger.warning("requirement_drift_run_yaml_unreadable", run=str(resolved_run), exc_info=True)
        return True
    task_type = str(run_data.get("task_type", "unknown")) or "unknown"
    return gate_mode_blocks_task(get_config(), task_type)


def _eligible_items(prd_id: str, entry: RequirementDriftEntry) -> list[str]:
    """FR05 block-eligible items: an unchecked PRD, and every unrecorded finding but ``evidence_not_checkable``."""
    items: list[str] = []
    if entry["baseline_status"] in _UNCHECKED_STATUSES:
        items.append(f"{prd_id} {entry['baseline_status']} ({entry['reason']})")
    for finding in entry["findings"]:
        if finding["recorded"] or finding["kind"] == "evidence_not_checkable":
            continue
        suffix = f" ({finding['reason']})" if finding["kind"] == "orphaned" else ""
        items.append(f"{finding['requirement_id']} {finding['kind']}{suffix}")
    return items


def _split(report: RequirementDriftReport, blocks_task: bool) -> tuple[list[str], list[str]]:
    """``(block, warn)``: items of block-mode PRDs block only when *blocks_task*; every other item warns."""
    block: list[str] = []
    warn: list[str] = []
    for prd_id, entry in report["prds"].items():
        target = block if blocks_task and entry["effective_mode"] == "block" else warn
        target.extend(_eligible_items(prd_id, entry))
    return block, warn


def drift_warning(report: RequirementDriftReport, blocks_task: bool) -> str:
    """The advisory naming every block-eligible item that does not block; ``""`` when there is none.

    ``evidence_not_checkable`` is reported in the entry but never named here: it is
    never block-eligible (FR03(a)).
    """
    _block, items = _split(report, blocks_task)
    if not items:
        return ""
    return (
        "Requirement drift advisory (PRD-CORE-321): " + "; ".join(items) + ". None of these is recorded by a "
        "Requirement Amendments row. Advisory only — this does not block delivery."
    )


def apply_requirement_drift_gate(
    report: RequirementDriftReport,
    blocks_task: bool,
    results: DeliverResultDict,
    errors: list[str],
    resolved_run: Path | None,
    trw_dir: Path,
    allow_unverified: bool,
    unverified_reason: str,
) -> bool:
    """FR05: block on the block-eligible items of block-mode PRDs. Returns True when delivery must BLOCK.

    Reuses *report* without recomputing it. The block goes through
    ``_hard_block_override``, so a valid PRD-CORE-191 acceptable-failure record
    is the only override.
    """
    block, _warn = _split(report, blocks_task)
    if not block:
        return False
    from trw_mcp.tools._deliver_gate_dispatch import _hard_block_override

    message = (
        "Requirement drift block (PRD-CORE-321): " + "; ".join(block) + ". Record a changed, dropped or orphaned "
        "requirement with a Requirement Amendments row naming its id (Date on or after the approval, Reason, Owner, "
        "Expiry), and baseline_reapproved or status_regressed with a row naming the PRD id. baseline_unresolvable, "
        "not_evaluated and shallow_clone pass only with a PRD-CORE-191 acceptable-failure record."
    )
    return _hard_block_override(
        results=results,
        errors=errors,
        resolved_run=resolved_run,
        trw_dir=trw_dir,
        allow_unverified=allow_unverified,
        unverified_reason=unverified_reason,
        block_reason=message,
        gate_type="requirement_drift",
        result_block_key="requirement_drift_block",
    )


__all__ = ["apply_requirement_drift_gate", "compute_requirement_drift", "drift_blocks_task", "drift_warning"]
