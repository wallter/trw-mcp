"""Thin production promotion path for SAFE-001 candidate promotion."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.meta_tune.eval_gaming_detector import detect_eval_gaming
from trw_mcp.meta_tune.promote_helpers import (
    append_audit_entry_or_raise,
    build_diff,
    build_sandbox_payload,
    derive_outcome_trace,
    materialize_sandbox_command,
    parse_sandbox_stdout,
    persist_snapshot,
    resolve_repo,
    resolve_repo_path,
)
from trw_mcp.meta_tune.promote_helpers import (
    sandbox_escape_signals as collect_sandbox_escape_signals,
)
from trw_mcp.meta_tune.promote_state import (
    load_recent_history as _load_recent_history,
)
from trw_mcp.meta_tune.promote_state import (
    target_write_lock as _target_write_lock,
)
from trw_mcp.meta_tune.promotion_gate import PromotionGate, PromotionProposal
from trw_mcp.meta_tune.sandbox import SandboxResult, run_sandboxed
from trw_mcp.meta_tune.surface_registry import classify_candidate
from trw_mcp.models.meta_tune import CandidateEdit

logger = structlog.get_logger(__name__)


class PromotionResult(BaseModel):
    """Typed result for the shipped SAFE-001 promotion entrypoint."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    edit_id: str
    promotion_session_id: str
    decision: str
    reason: str
    target_path: str
    promoted: bool
    surface_classification: str
    sandbox_exit_code: int
    sandbox_timed_out: bool
    eval_gaming_flags: tuple[str, ...] = Field(default=())
    audit_log_path: str


def _default_state_dir() -> Path:
    return Path(".trw/meta_tune/state")


def promote_candidate(
    *,
    target_path: Path,
    candidate_content: str,
    proposer_id: str,
    reviewer_id: str | None,
    approval_ts: datetime | None,
    sandbox_command: list[str],
    declared_metric_delta: float | None = None,
    promotion_session_id: str | None = None,
    edit_id: str | None = None,
    state_dir: Path | None = None,
    _config: Any = None,
) -> PromotionResult:
    """Run a candidate through classify -> sandbox -> detector -> gate -> write."""
    if not sandbox_command:
        raise ValueError("promote_candidate requires a non-empty sandbox_command")
    cfg = _config
    if cfg is None:
        from trw_mcp.models.config import get_config

        cfg = get_config()

    if not cfg.meta_tune.enabled:
        logger.info(
            "meta-tune-disabled",
            component="meta_tune.promote",
            op="promote_candidate",
            outcome="noop",
        )
        return PromotionResult(
            edit_id=edit_id or str(uuid.uuid4()),
            promotion_session_id=promotion_session_id or str(uuid.uuid4()),
            decision="reject",
            reason="meta-tune-disabled",
            target_path=str(target_path),
            promoted=False,
            surface_classification="control",
            sandbox_exit_code=-1,
            sandbox_timed_out=False,
            audit_log_path=str(Path(cfg.meta_tune.audit_log_path)),
        )

    resolved_target = target_path.resolve()
    repo_root = resolve_repo(resolved_target)
    resolved_state_dir = (state_dir or _default_state_dir()).resolve()
    resolved_audit_log = resolve_repo_path(cfg.meta_tune.audit_log_path, repo_root=repo_root)
    resolved_corpus_path = resolve_repo_path(cfg.meta_tune.corpus_path, repo_root=repo_root)
    resolved_edit_id = edit_id or str(uuid.uuid4())
    resolved_session_id = promotion_session_id or str(uuid.uuid4())
    with _target_write_lock(resolved_target, resolved_state_dir):
        original_content = resolved_target.read_text(encoding="utf-8") if resolved_target.exists() else ""
        diff = build_diff(resolved_target, original_content, candidate_content)

        candidate = CandidateEdit(
            edit_id=resolved_edit_id,
            proposer_id=proposer_id,
            target_path=resolved_target,
            diff=diff,
            created_ts=datetime.now(timezone.utc),
        )
        classification = classify_candidate(candidate, _config=cfg)
        surface_classification = "control" if classification.is_control else "advisory"

        append_audit_entry_or_raise(
            resolved_audit_log,
            edit_id=resolved_edit_id,
            event="proposed",
            proposer_id=proposer_id,
            candidate_diff=diff,
            surface_classification=surface_classification,
            gate_decision="pending",
            payload={"target_path": str(resolved_target)},
            promotion_session_id=resolved_session_id,
            reviewer_id=reviewer_id,
            _config=cfg,
        )

        if classification.is_control:
            append_audit_entry_or_raise(
                resolved_audit_log,
                edit_id=resolved_edit_id,
                event="rejected",
                proposer_id=proposer_id,
                candidate_diff=diff,
                surface_classification="control",
                gate_decision="reject",
                payload={"reason": classification.rationale or "control-surface-violation"},
                promotion_session_id=resolved_session_id,
                reviewer_id=reviewer_id,
                _config=cfg,
            )
            return PromotionResult(
                edit_id=resolved_edit_id,
                promotion_session_id=resolved_session_id,
                decision="reject",
                reason="control-surface-violation",
                target_path=str(resolved_target),
                promoted=False,
                surface_classification="control",
                sandbox_exit_code=-1,
                sandbox_timed_out=False,
                audit_log_path=str(resolved_audit_log),
            )

        staging_dir = resolved_state_dir / "staging" / resolved_edit_id
        staging_dir.mkdir(parents=True, exist_ok=True)
        # The staging dir is a transient sandbox workspace; nothing downstream
        # reads it after the sandbox run (approve writes to resolved_target and
        # snapshots to backup_dir). Clean it up on EVERY exit path — return,
        # raise, or fall-through — so rejected/sandbox-failed/needs-review
        # dispatches do not leak directories to disk.
        try:
            return _run_after_staging(
                cfg=cfg,
                staging_dir=staging_dir,
                resolved_target=resolved_target,
                resolved_state_dir=resolved_state_dir,
                resolved_audit_log=resolved_audit_log,
                resolved_corpus_path=resolved_corpus_path,
                resolved_edit_id=resolved_edit_id,
                resolved_session_id=resolved_session_id,
                repo_root=repo_root,
                candidate_content=candidate_content,
                proposer_id=proposer_id,
                reviewer_id=reviewer_id,
                approval_ts=approval_ts,
                sandbox_command=sandbox_command,
                declared_metric_delta=declared_metric_delta,
                diff=diff,
                classification=classification,
            )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)


def _run_after_staging(
    *,
    cfg: Any,
    staging_dir: Path,
    resolved_target: Path,
    resolved_state_dir: Path,
    resolved_audit_log: Path,
    resolved_corpus_path: Path,
    resolved_edit_id: str,
    resolved_session_id: str,
    repo_root: Path,
    candidate_content: str,
    proposer_id: str,
    reviewer_id: str | None,
    approval_ts: datetime | None,
    sandbox_command: list[str],
    declared_metric_delta: float | None,
    diff: str,
    classification: Any,
) -> PromotionResult:
    """Sandbox -> detector -> gate -> write. Staging cleanup is the caller's."""
    staged_candidate_path = staging_dir / resolved_target.name
    staged_candidate_path.write_text(candidate_content, encoding="utf-8")
    rendered_command = materialize_sandbox_command(
        sandbox_command,
        candidate_path=staged_candidate_path,
        live_target_path=resolved_target,
        corpus_path=resolved_corpus_path,
        repo_root=repo_root,
    )
    # SAFE-001 candidate replay needs FULL environment inheritance: the
    # rendered command re-runs an operator-approved meta-tune candidate, which
    # invokes the project's test/eval harness inside the active venv. Without
    # PYTHONPATH / VIRTUAL_ENV / TRW_* the replay subprocess fails for ENV
    # reasons (import errors, missing interpreter context) and the proposal is
    # scored as refuted for the wrong reason. The sandbox sanitizes the env by
    # default (CORE-144 §7.6 secret-exfiltration control for untrusted probes);
    # this caller is the documented full-inheritance exception because the
    # candidate is operator-approved, not agent-authored untrusted input.
    sandbox_result: SandboxResult = run_sandboxed(  # trw:intentional SAFE-001 full-env replay
        rendered_command,
        timeout_s=cfg.meta_tune.sandbox_timeout_seconds,
        readonly_paths=[resolved_target] if resolved_target.exists() else [],
        writable_paths=[staging_dir],
        env=os.environ.copy(),
    )

    sandbox_payload = build_sandbox_payload(sandbox_result)
    append_audit_entry_or_raise(
        resolved_audit_log,
        edit_id=resolved_edit_id,
        event="sandboxed",
        proposer_id=proposer_id,
        candidate_diff=diff,
        surface_classification="advisory",
        gate_decision="pending",
        payload=sandbox_payload,
        promotion_session_id=resolved_session_id,
        reviewer_id=reviewer_id,
        _config=cfg,
    )

    sandbox_escape_signals = collect_sandbox_escape_signals(sandbox_result)
    if sandbox_escape_signals:
        logger.warning(
            "meta_tune_sandbox_policy_violation",
            component="meta_tune.promote",
            op="promote_candidate",
            outcome="reject",
            edit_id=resolved_edit_id,
            sandbox_escape_signals=sandbox_escape_signals,
        )
        append_audit_entry_or_raise(
            resolved_audit_log,
            edit_id=resolved_edit_id,
            event="rejected",
            proposer_id=proposer_id,
            candidate_diff=diff,
            surface_classification="advisory",
            gate_decision="reject",
            payload={
                "reason": "sandbox-policy-violation",
                "sandbox_escape_signals": list(sandbox_escape_signals),
                **sandbox_payload,
            },
            promotion_session_id=resolved_session_id,
            reviewer_id=reviewer_id,
            _config=cfg,
        )
        return PromotionResult(
            edit_id=resolved_edit_id,
            promotion_session_id=resolved_session_id,
            decision="reject",
            reason="sandbox-policy-violation",
            target_path=str(resolved_target),
            promoted=False,
            surface_classification="advisory",
            sandbox_exit_code=sandbox_result.exit_code,
            sandbox_timed_out=sandbox_result.timed_out,
            audit_log_path=str(resolved_audit_log),
        )

    if sandbox_result.exit_code != 0 or sandbox_result.timed_out:
        append_audit_entry_or_raise(
            resolved_audit_log,
            edit_id=resolved_edit_id,
            event="rejected",
            proposer_id=proposer_id,
            candidate_diff=diff,
            surface_classification="advisory",
            gate_decision="reject",
            payload={"reason": "sandbox-replay-failed", **sandbox_payload},
            promotion_session_id=resolved_session_id,
            reviewer_id=reviewer_id,
            _config=cfg,
        )
        return PromotionResult(
            edit_id=resolved_edit_id,
            promotion_session_id=resolved_session_id,
            decision="reject",
            reason="sandbox-replay-failed",
            target_path=str(resolved_target),
            promoted=False,
            surface_classification="advisory",
            sandbox_exit_code=sandbox_result.exit_code,
            sandbox_timed_out=sandbox_result.timed_out,
            audit_log_path=str(resolved_audit_log),
        )

    sandbox_report = parse_sandbox_stdout(sandbox_result.stdout)
    outcome_trace = derive_outcome_trace(sandbox_report)
    metric_delta = declared_metric_delta
    report_delta = sandbox_report.get("declared_metric_delta")
    if metric_delta is None and isinstance(report_delta, (int, float)):
        metric_delta = float(report_delta)
    if metric_delta is None:
        raise ValueError("promote_candidate requires declared_metric_delta or sandbox output")

    eval_verdict = detect_eval_gaming(
        diff=diff,
        target_path=str(resolved_target),
        outcome_trace=outcome_trace,
        _config=cfg,
    )
    # Pin the gate's audit-log path to the same resolved file dispatch
    # appends to, so the hash chain stays linear and the Goodhart history
    # window reads back the deltas the gate itself wrote (SAFE-001 FR-2).
    gate_cfg = cfg.model_copy(
        update={"meta_tune": cfg.meta_tune.model_copy(update={"audit_log_path": str(resolved_audit_log)})}
    )
    gate = PromotionGate(config=gate_cfg, history=_load_recent_history(resolved_audit_log))
    decision = gate.evaluate(
        PromotionProposal(
            proposal_id=resolved_edit_id,
            declared_metric_delta=metric_delta,
            surface_classification="advisory",
            surfaces=tuple(surface.value for surface in classification.surfaces),
            diff_lines_touched=len(diff.splitlines()),
            eval_gaming_ok=not eval_verdict.rejected,
            eval_gaming_flags=tuple(eval_verdict.flags),
        ),
        reviewer_id=reviewer_id,
        approval_ts=approval_ts,
        promotion_session_id=resolved_session_id,
    )

    if decision.decision == "approve":
        backup_dir = resolved_state_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{resolved_edit_id}.bak"
        if resolved_target.exists():
            shutil.copy2(resolved_target, backup_path)
        else:
            backup_path.write_text("", encoding="utf-8")
        resolved_target.parent.mkdir(parents=True, exist_ok=True)
        resolved_target.write_text(candidate_content, encoding="utf-8")
        persist_snapshot(
            edit_id=resolved_edit_id,
            state_dir=resolved_state_dir,
            target_path=resolved_target,
            original_path=backup_path,
            promotion_session_id=resolved_session_id,
        )

    return PromotionResult(
        edit_id=resolved_edit_id,
        promotion_session_id=resolved_session_id,
        decision=decision.decision,
        reason=decision.reason,
        target_path=str(resolved_target),
        promoted=decision.decision == "approve",
        surface_classification="advisory",
        sandbox_exit_code=sandbox_result.exit_code,
        sandbox_timed_out=sandbox_result.timed_out,
        eval_gaming_flags=tuple(eval_verdict.flags),
        audit_log_path=str(resolved_audit_log),
    )


__all__ = ["PromotionResult", "promote_candidate"]
