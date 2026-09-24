"""Append-only, fail-closed formation merge queue (CORE-296 FR07).

Approvals and outcomes replay from the orchestrator run's existing events.jsonl;
there is no second queue store. An attempt without a terminal event is UNKNOWN,
not success, even if Git's ref moved before the writer crashed. Only a pinned
orchestrator approves; only it or an enrolled integrator consumes the queue.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from trw_mcp.formation._manifest import FormationError
from trw_mcp.formation._merge_git import MergeGitError, apply, prepare
from trw_mcp.formation._store import FormationContext, _exclusive
from trw_mcp.models._evidence_plans import RequiredValidationPlan
from trw_mcp.models._evidence_records import BuildReceipt
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._evidence_gates import validate_build_receipt
from trw_mcp.state._paths_pin_mgmt import get_pinned_run
from trw_mcp.state.persistence import FileEventLogger, FileStateReader, FileStateWriter

_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
_RECEIPT = re.compile(r"build-[0-9a-f]{32}\Z")
_PLAN = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
_EVENTS = frozenset(
    {"formation_merge_approved", "formation_merge_attempt", "formation_merge_merged", "formation_merge_stopped"}
)


class MergeQueueError(FormationError):
    """Closed queue refusal with a stable reason."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True)
class MergeItem:
    approval_id: str
    branch: str
    sha: str
    member_id: str
    receipt_id: str
    approver_member_id: str
    approver_pin_digest: str
    admitted_at: str
    state: str = "approved"
    reason: str = ""
    merge_sha: str = ""

    def as_dict(self) -> dict[str, str]:
        return dict(vars(self))


def _events_path(context: FormationContext) -> Path:
    return Path(context.manifest.orchestrator_run_path) / "meta" / "events.jsonl"


def _caller(context: FormationContext, *, require_integrator: bool) -> str:
    call = build_call_context(None)
    pinned = get_pinned_run(context=call)
    if pinned is None:
        raise MergeQueueError("no_pinned_run", "pin this session's own run before using the merge queue")
    if context.is_orchestrator and pinned.resolve() == Path(context.manifest.orchestrator_run_path).resolve():
        return call.session_id
    if not require_integrator:
        raise MergeQueueError("not_orchestrator", "only the pinned formation orchestrator may approve merges")
    member = context.manifest.member(context.member_id or "")
    if (
        member.role != "integrator"
        or str(member.status) not in {"joined", "active"}
        or not member.run_path
        or Path(member.run_path).resolve() != pinned.resolve()
        or member.pin_key != call.session_id
    ):
        raise MergeQueueError("not_integrator", "merge execution requires an enrolled integrator's own pin")
    return call.session_id


def _append(path: Path, event: str, item: MergeItem, **extra: str) -> None:
    data: dict[str, object] = {"approval_id": item.approval_id, **extra}
    if event == "formation_merge_approved":
        data.update(item.as_dict())
    FileEventLogger(FileStateWriter()).log_event(path, event, data)


def _replay(path: Path) -> list[MergeItem]:
    try:
        rows = FileStateReader().read_jsonl(path, strict=True)
    except Exception as exc:
        raise MergeQueueError(
            "ledger_unreadable", "formation event ledger is unreadable; no merge is authorized"
        ) from exc
    items: dict[str, MergeItem] = {}
    for row in rows:
        event = row.get("event")
        if event not in _EVENTS:
            continue
        key = row.get("approval_id")
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{32}", key):
            raise MergeQueueError("ledger_malformed", "merge event has no valid approval id")
        if event == "formation_merge_approved":
            if key in items:
                raise MergeQueueError("ledger_malformed", "duplicate approval id in merge ledger")
            try:
                item = MergeItem(
                    approval_id=key,
                    branch=str(row["branch"]),
                    sha=str(row["sha"]),
                    member_id=str(row["member_id"]),
                    receipt_id=str(row["receipt_id"]),
                    approver_member_id=str(row["approver_member_id"]),
                    approver_pin_digest=str(row["approver_pin_digest"]),
                    admitted_at=str(row["admitted_at"]),
                )
            except KeyError as exc:
                raise MergeQueueError("ledger_malformed", "approval event is missing a required field") from exc
            items[key] = item
            continue
        old = items.get(key)
        if old is None or old.state != ("approved" if event == "formation_merge_attempt" else "attempted"):
            raise MergeQueueError("ledger_malformed", "merge event transition is out of order")
        new_state = {
            "formation_merge_attempt": "attempted",
            "formation_merge_merged": "merged",
            "formation_merge_stopped": "stopped",
        }[str(event)]
        items[key] = MergeItem(
            **{
                **old.as_dict(),
                "state": new_state,
                "reason": str(row.get("reason", "")),
                "merge_sha": str(row.get("merge_sha", "")),
            }
        )
    return list(items.values())


def list_items(context: FormationContext) -> list[MergeItem]:
    _caller(context, require_integrator=True)
    return _replay(_events_path(context))


def enqueue(context: FormationContext, *, branch: str, sha: str, member_id: str, receipt_id: str) -> MergeItem:
    pin = _caller(context, require_integrator=False)
    if _SHA.fullmatch(sha) is None or _RECEIPT.fullmatch(receipt_id) is None:
        raise MergeQueueError("invalid_approval", "approval requires a commit SHA and typed build receipt id")
    context.manifest.member(member_id)
    path = _events_path(context)
    with _exclusive(path):
        for old in _replay(path):
            if old.state != "stopped" and (old.branch, old.sha, old.member_id, old.receipt_id) == (
                branch,
                sha,
                member_id,
                receipt_id,
            ):
                return old  # lost enqueue response is an exact replay, never a second approval
        from datetime import datetime, timezone

        item = MergeItem(
            approval_id=secrets.token_hex(16),
            branch=branch,
            sha=sha,
            member_id=member_id,
            receipt_id=receipt_id,
            approver_member_id=context.member_id or "orchestrator",
            approver_pin_digest=hashlib.sha256(pin.encode("utf-8")).hexdigest(),
            admitted_at=datetime.now(timezone.utc).isoformat(),
        )
        _append(path, "formation_merge_approved", item)
        return item


def _member_root(run_path: Path) -> Path:
    store = next((parent for parent in run_path.parents if parent.name == ".trw"), None)
    if store is None:
        raise MergeQueueError("receipt_unavailable", "member run has no project store")
    return store.parent


def _check_receipt(context: FormationContext, item: MergeItem) -> None:
    member = context.manifest.member(item.member_id)
    if not member.run_path or _RECEIPT.fullmatch(item.receipt_id) is None:
        raise MergeQueueError("receipt_unavailable", "member has no typed build receipt")
    run = Path(member.run_path)
    try:
        raw = (run / "meta" / "receipts" / "build" / f"{item.receipt_id}.json").read_text(encoding="utf-8")
        receipt = BuildReceipt.model_validate_json(raw)
        if receipt.receipt_id != item.receipt_id or receipt.run_id != run.name or receipt.git_sha != item.sha:
            raise MergeQueueError("receipt_sha_mismatch", "build receipt does not bind this immutable commit")
        if _PLAN.fullmatch(receipt.plan_id) is None:
            raise MergeQueueError("receipt_unavailable", "build receipt plan id is invalid")
        plan_raw = (run / "meta" / "plans" / "validation" / f"{receipt.plan_id}.json").read_text(encoding="utf-8")
        plan = RequiredValidationPlan.model_validate_json(plan_raw)
        verdict = validate_build_receipt(receipt, plan, _member_root(run))
    except MergeQueueError:
        raise
    except (OSError, ValueError) as exc:
        raise MergeQueueError("receipt_unavailable", "typed build receipt or plan is missing or invalid") from exc
    if not verdict.is_positive:
        raise MergeQueueError("red_gate", f"build receipt is not a current passing gate: {verdict.reason_code}")


def run_one(context: FormationContext, *, repo: Path, target: str) -> MergeItem:
    _caller(context, require_integrator=True)
    path = _events_path(context)
    with _exclusive(path):
        items = _replay(path)
        pending = next((item for item in items if item.state in {"approved", "attempted"}), None)
        if pending is None:
            raise MergeQueueError("queue_empty", "no approved merge is pending")
        if pending.state != "approved":
            raise MergeQueueError(
                "requires_reconciliation", "prior attempt is outcome-unknown; inspect before reapproval"
            )
        _append(path, "formation_merge_attempt", pending)
        try:
            _check_receipt(context, pending)
            old, tree, paths = prepare(repo, pending.branch, pending.sha, target)
            if not paths:
                raise MergeQueueError("empty_change", "approved branch changes no paths")
            from trw_mcp import formation

            for changed in paths:
                owner = formation.owner_of(changed, context=context, project_root=repo)
                if owner is None or owner.member_id != pending.member_id:
                    raise MergeQueueError("unowned_path", f"{changed} is not owned by {pending.member_id}")
            merged = apply(repo, pending.branch, pending.sha, target, old, tree)
        except (MergeQueueError, MergeGitError) as exc:
            _append(path, "formation_merge_stopped", pending, reason=exc.reason)
            raise
        except FormationError as exc:
            _append(path, "formation_merge_stopped", pending, reason="ownership_unavailable")
            raise MergeQueueError("ownership_unavailable", "ownership could not be verified") from exc
        _append(path, "formation_merge_merged", pending, merge_sha=merged)
        return MergeItem(**{**pending.as_dict(), "state": "merged", "merge_sha": merged})
