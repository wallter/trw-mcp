"""Thin production dispatch path for SAFE-001 candidate promotion."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp._locking import _lock_ex, _lock_un
from trw_mcp.meta_tune.audit import AuditAppendError, AuditIntegrityError, append_audit_entry
from trw_mcp.meta_tune.dispatch_helpers import (
    build_diff,
    derive_outcome_trace,
    materialize_sandbox_command,
    parse_sandbox_stdout,
    persist_snapshot,
    resolve_repo,
    resolve_repo_path,
)
from trw_mcp.meta_tune.dispatch_helpers import (
    sandbox_escape_signals as collect_sandbox_escape_signals,
)
from trw_mcp.meta_tune.errors import MetaTuneSafetyUnavailableError
from trw_mcp.meta_tune.eval_gaming_detector import detect_eval_gaming
from trw_mcp.meta_tune.promotion_gate import PromotionGate, PromotionProposal
from trw_mcp.meta_tune.sandbox import SandboxResult, run_sandboxed
from trw_mcp.meta_tune.surface_registry import classify_candidate
from trw_mcp.models.meta_tune import CandidateEdit

logger = structlog.get_logger(__name__)


class DispatchResult(BaseModel):
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


@contextmanager
def _target_write_lock(target_path: Path, state_dir: Path) -> Any:
    """Exclusive advisory lock serializing the read-modify-write of ``target_path``.

    Without it, two concurrent promotes against the same target race on
    read-then-write and corrupt the file. Per-target sidecar under the state dir.
    """
    locks_dir = state_dir / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(target_path).encode("utf-8")).hexdigest()[:32]
    with (locks_dir / f"{digest}.lock").open("a", encoding="utf-8") as lock_fh:
        _lock_ex(lock_fh.fileno())
        try:
            yield
        finally:
            _lock_un(lock_fh.fileno())


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
) -> DispatchResult:
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
            component="meta_tune.dispatch",
            op="promote_candidate",
            outcome="noop",
        )
        return DispatchResult(
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

        try:
            append_audit_entry(
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
        except (AuditAppendError, AuditIntegrityError) as exc:
            raise MetaTuneSafetyUnavailableError(
                dependency_id="audit_log",
                activation_gate_blocked_reason=str(exc),
            ) from exc

        if classification.is_control:
            try:
                append_audit_entry(
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
            except (AuditAppendError, AuditIntegrityError) as exc:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="audit_log",
                    activation_gate_blocked_reason=str(exc),
                ) from exc
            return DispatchResult(
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
        staged_candidate_path = staging_dir / resolved_target.name
        staged_candidate_path.write_text(candidate_content, encoding="utf-8")
        rendered_command = materialize_sandbox_command(
            sandbox_command,
            candidate_path=staged_candidate_path,
            live_target_path=resolved_target,
            corpus_path=resolved_corpus_path,
            repo_root=repo_root,
        )
        sandbox_result: SandboxResult = run_sandboxed(
            rendered_command,
            timeout_s=cfg.meta_tune.sandbox_timeout_seconds,
            readonly_paths=[resolved_target] if resolved_target.exists() else [],
            writable_paths=[staging_dir],
        )

        sandbox_payload: dict[str, Any] = {
            "exit_code": sandbox_result.exit_code,
            "timed_out": sandbox_result.timed_out,
            "wall_ms": sandbox_result.wall_ms,
            "writes_outside_tmp": sandbox_result.writes_outside_tmp,
            "network_attempted": sandbox_result.network_attempted,
        }
        if sandbox_result.exit_code == 0 and not sandbox_result.timed_out:
            sandbox_payload["stdout"] = sandbox_result.stdout
        else:
            sandbox_payload["stderr"] = sandbox_result.stderr
        try:
            append_audit_entry(
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
        except (AuditAppendError, AuditIntegrityError) as exc:
            raise MetaTuneSafetyUnavailableError(
                dependency_id="audit_log",
                activation_gate_blocked_reason=str(exc),
            ) from exc

        sandbox_escape_signals = collect_sandbox_escape_signals(sandbox_result)
        if sandbox_escape_signals:
            logger.warning(
                "meta_tune_sandbox_policy_violation",
                component="meta_tune.dispatch",
                op="promote_candidate",
                outcome="reject",
                edit_id=resolved_edit_id,
                sandbox_escape_signals=sandbox_escape_signals,
            )
            try:
                append_audit_entry(
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
            except (AuditAppendError, AuditIntegrityError) as exc:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="audit_log",
                    activation_gate_blocked_reason=str(exc),
                ) from exc
            return DispatchResult(
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
            try:
                append_audit_entry(
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
            except (AuditAppendError, AuditIntegrityError) as exc:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="audit_log",
                    activation_gate_blocked_reason=str(exc),
                ) from exc
            return DispatchResult(
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
        gate = PromotionGate(config=cfg)
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

        return DispatchResult(
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


__all__ = ["DispatchResult", "promote_candidate"]
