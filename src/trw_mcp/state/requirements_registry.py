"""Sole executable-requirements registry writer (PRD-QUAL-121 FR03/FR04).

One generated registry contains the executable active PRDs — lifecycle,
priority, dependencies, owner, execution state, renewal date, and source
digest — plus the scheduling-ledger head digest and the EvaluationEpoch
derived from it. INDEX.md and ROADMAP.md are projections rendered from this
registry by ``state/index_sync.py``; they are never independent authorities.

Scheduling actions are append-only, hash-chained, authorized inputs. Callers
can never supply an epoch: the sole writer stamps dates from its injected
trusted UTC clock, and every reconciliation consumes the latest committed
ledger head. Ambient wall-clock changes after ledger commit alter no
canonical registry bytes (NFR01).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.requirements import (
    EvaluationEpoch,
    ExecutionState,
    PrdActiveLimits,
    RequirementRegistryEntry,
    SchedulingAction,
)
from trw_mcp.state.persistence import lock_for_rmw
from trw_mcp.state.prd_utils import parse_frontmatter

logger = structlog.get_logger(__name__)

REGISTRY_SCHEMA = "requirements-registry/v1"
LEDGER_FILENAME = "scheduling-ledger.jsonl"
REGISTRY_FILENAME = "requirements-registry.json"
GENESIS_DIGEST = "genesis"

# Lifecycle statuses with no executable work remaining. Everything else is an
# executable-registry member (includes non-canonical open aliases).
_TERMINAL_STATUSES = frozenset({"done", "implemented", "merged", "deprecated", "delivered", "complete"})

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


class SchedulingLedgerError(RuntimeError):
    """Typed ledger failure — fork, gap, stale head, rollback, or tamper."""


class ActivationRefusedError(RuntimeError):
    """WIP-limit refusal (PRD-QUAL-121-FR04): carries the occupied slots."""

    def __init__(self, reason: str, occupied_slots: list[str]) -> None:
        super().__init__(reason)
        self.occupied_slots = occupied_slots


ANCHOR_FILENAME = "ledger-head.json"


def _anchor_path(ledger_path: Path) -> Path:
    return ledger_path.parent / ANCHOR_FILENAME


def _read_anchor(ledger_path: Path) -> tuple[int, str] | None:
    """Return the last committed (sequence, head_digest) anchor, if any."""
    anchor = _anchor_path(ledger_path)
    if not anchor.exists():
        return None
    try:
        data = json.loads(anchor.read_text(encoding="utf-8"))
        return int(data["sequence"]), str(data["head_digest"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SchedulingLedgerError(f"ledger head anchor unreadable: {exc}") from exc


def _write_anchor(ledger_path: Path, sequence: int, head_digest: str) -> None:
    _anchor_path(ledger_path).write_text(
        json.dumps({"sequence": sequence, "head_digest": head_digest}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def verify_ledger_head_anchor(ledger_path: Path, actions: list[SchedulingAction]) -> None:
    """Anti-rollback anchor check (PRD-QUAL-121 §Authorized Scheduling Ledger).

    Prefix-chain verification alone cannot detect a truncated ledger (a valid
    prefix) or an in-place rewrite of the TAIL action (nothing chains atop it).
    The writer records the committed (sequence, head digest) beside the ledger
    after every append; any ledger whose length or head diverges from the
    anchor is an older/rolled-back/tampered head and MUST reconcile as
    ``stale_scheduling_head``. Threat model: operator error and concurrent
    races — a filesystem-level adversary who can forge both files is out of
    scope (the ledger has no signing key by design).
    """
    anchor = _read_anchor(ledger_path)
    if anchor is None:
        if actions:
            raise SchedulingLedgerError("ledger has actions but no committed head anchor")
        return
    sequence, head = anchor
    if len(actions) != sequence or ledger_head_digest(actions) != head:
        raise SchedulingLedgerError(
            f"ledger head diverges from committed anchor (anchor seq={sequence}, ledger seq={len(actions)}): "
            "older, rolled-back, or tail-tampered head"
        )


def action_digest(action: SchedulingAction) -> str:
    """Content digest binding an action into the hash chain."""
    payload = json.dumps(action.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_ledger(ledger_path: Path) -> list[SchedulingAction]:
    """Load and chain-verify the scheduling ledger (typed failure on tamper).

    Verifies sequence continuity (1..n) and that each action's
    ``previous_action_digest`` equals the digest of its predecessor.
    """
    if not ledger_path.exists():
        return []
    actions: list[SchedulingAction] = []
    previous_digest = GENESIS_DIGEST
    for line_no, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            action = SchedulingAction.model_validate(json.loads(line))
        except Exception as exc:  # justified: boundary — malformed ledger is a typed failure
            raise SchedulingLedgerError(f"ledger line {line_no} does not parse: {exc}") from exc
        if action.sequence != len(actions) + 1:
            raise SchedulingLedgerError(
                f"ledger sequence gap or fork at line {line_no}: expected {len(actions) + 1}, got {action.sequence}"
            )
        if action.previous_action_digest != previous_digest:
            raise SchedulingLedgerError(
                f"ledger chain break at sequence {action.sequence}: stale or forked previous digest"
            )
        previous_digest = action_digest(action)
        actions.append(action)
    return actions


def ledger_head_digest(actions: list[SchedulingAction]) -> str:
    return action_digest(actions[-1]) if actions else GENESIS_DIGEST


def derive_evaluation_epoch(actions: list[SchedulingAction]) -> EvaluationEpoch:
    """EvaluationEpoch = (sequence, effective_utc_date, ledger_head_digest) of the
    latest authorized ``advance_evaluation_epoch`` at the committed head."""
    head = ledger_head_digest(actions)
    for action in reversed(actions):
        if action.kind == "advance_evaluation_epoch":
            return EvaluationEpoch(
                sequence=action.sequence,
                effective_utc_date=action.effective_utc_date,
                ledger_head_digest=head,
            )
    return EvaluationEpoch(sequence=0, effective_utc_date="1970-01-01", ledger_head_digest=head)


class RegistryWriter:
    """The sole scheduling-ledger writer.

    The trusted UTC clock is injected at construction; append methods take no
    date parameter, so a caller-supplied epoch is unrepresentable. Every append
    re-verifies the chain, extends the current head, and rejects dates earlier
    than the prior epoch or later than the writer's current UTC date.
    """

    def __init__(self, ledger_path: Path, *, utc_today: Callable[[], date] | None = None) -> None:
        self._ledger_path = ledger_path
        self._utc_today = utc_today or (lambda: datetime.now(timezone.utc).date())

    def _append(self, kind: str, payload: dict[str, str], authorization_receipt: str, actor: str) -> SchedulingAction:
        # trw:intentional cross-process advisory lock — trw-mcp runs one OS
        # process per MCP client, so an in-process thread lock cannot serialize
        # the read→verify→append→anchor cycle across concurrent workers. The
        # sibling advisory ``{ledger_path}.lock`` (same helper _trust_outcome
        # uses) is the only barrier that makes the sequence atomic (NFR02).
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_for_rmw(self._ledger_path):
            return self._append_locked(kind, payload, authorization_receipt, actor)

    def _append_locked(
        self, kind: str, payload: dict[str, str], authorization_receipt: str, actor: str
    ) -> SchedulingAction:
        """Append body — the caller MUST already hold ``lock_for_rmw(ledger_path)``.

        Split from :meth:`_append` so a WIP-gated transition can hold ONE lock
        across the activation gate *and* the append (a nested ``lock_for_rmw``
        on the same file would deadlock — flock locks per open file description).
        """
        if not authorization_receipt.strip():
            raise SchedulingLedgerError("scheduling actions require an authorization receipt")
        actions = load_ledger(self._ledger_path)  # re-verify: stale/forked ledgers never extend
        verify_ledger_head_anchor(self._ledger_path, actions)
        today = self._utc_today()
        prior_epoch = derive_evaluation_epoch(actions)
        if kind == "advance_evaluation_epoch" and today.isoformat() < prior_epoch.effective_utc_date:
            raise SchedulingLedgerError(
                f"epoch advance to {today.isoformat()} would roll back prior epoch {prior_epoch.effective_utc_date}"
            )
        action = SchedulingAction(
            action_id=str(uuid.uuid4()),
            sequence=len(actions) + 1,
            kind=kind,  # type: ignore[arg-type]
            effective_utc_date=today.isoformat(),
            previous_action_digest=ledger_head_digest(actions),
            authorization_receipt=authorization_receipt,
            actor=actor,
            payload=payload,
        )
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(action.model_dump(mode="json"), sort_keys=True) + "\n")
        _write_anchor(self._ledger_path, action.sequence, action_digest(action))
        logger.info("scheduling_action_appended", kind=kind, sequence=action.sequence)
        return action

    def advance_evaluation_epoch(self, *, authorization_receipt: str, actor: str) -> SchedulingAction:
        return self._append("advance_evaluation_epoch", {}, authorization_receipt, actor)

    def set_execution_state(
        self,
        prd_id: str,
        state: ExecutionState,
        *,
        prds_dir: Path,
        authorization_receipt: str,
        actor: str,
        owner: str = "",
    ) -> SchedulingAction:
        """Transition a PRD's execution state — WIP limits are enforced HERE.

        This is the sole production mutation point for execution state, so the
        FR04 activation gate runs on every WIP-consuming transition (ACTIVE and
        BLOCKED_EXTERNAL): the current registry is reconciled and the nested
        limits evaluated BEFORE any ledger write. An over-limit transition
        raises :class:`ActivationRefusedError` naming the occupied slots and
        appends nothing. ``prds_dir`` is required so the gate cannot be
        skipped by omission.
        """
        payload = {"prd_id": prd_id, "state": state.value}
        if owner:
            payload["owner"] = owner
        if state not in (ExecutionState.ACTIVE, ExecutionState.BLOCKED_EXTERNAL):
            return self._append("set_execution_state", payload, authorization_receipt, actor)

        # WIP-consuming transition: the activation gate (build_registry +
        # evaluate_activation) and the ledger append MUST be atomic, or two
        # concurrent workers each pass the gate against the same pre-append
        # registry and both activate — busting the NFR02 WIP invariant. Hold
        # ONE cross-process lock across the whole read→evaluate→append.
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_for_rmw(self._ledger_path):
            registry = build_registry(prds_dir, self._ledger_path)
            if registry.status != "ok":
                raise SchedulingLedgerError(
                    f"cannot activate {prd_id}: registry is {registry.status} ({registry.error})"
                )
            candidate = next((entry for entry in registry.entries if entry.prd_id == prd_id), None)
            if candidate is not None:
                # Evaluate the limits AS IF the candidate held the requested
                # state/owner, so first-time activations hit the right branch.
                candidate.execution_state = state
                if owner:
                    candidate.owner = owner
                decision = evaluate_activation(registry, prd_id)
                if not decision.allowed:
                    raise ActivationRefusedError(decision.reason, decision.occupied_slots)
            return self._append_locked("set_execution_state", payload, authorization_receipt, actor)

    def renew(self, prd_id: str, *, authorization_receipt: str, actor: str) -> SchedulingAction:
        return self._append("renew", {"prd_id": prd_id}, authorization_receipt, actor)


@dataclass(slots=True)
class RegistryBuildResult:
    """Typed outcome of a registry reconciliation."""

    status: str  # "ok" | "stale_scheduling_head"
    entries: list[RequirementRegistryEntry] = field(default_factory=list)
    epoch: EvaluationEpoch | None = None
    head_digest: str = GENESIS_DIGEST
    hot_path: list[str] = field(default_factory=list)
    expired: list[str] = field(default_factory=list)
    limits: PrdActiveLimits = field(default_factory=PrdActiveLimits)
    error: str = ""

    def canonical_document(self) -> dict[str, object]:
        """Byte-stable registry document — no ambient time, no volatile fields."""
        return {
            "schema": REGISTRY_SCHEMA,
            "status": self.status,
            "entries": [entry.model_dump(mode="json") for entry in self.entries],
            "evaluation_epoch": self.epoch.model_dump(mode="json") if self.epoch else None,
            "scheduling_ledger_head_digest": self.head_digest,
            "hot_path": self.hot_path,
            "expired": self.expired,
            "limits": self.limits.model_dump(mode="json"),
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.canonical_document(), sort_keys=True, separators=(",", ":")).encode("utf-8")

    def receipt_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()


def _renewal_window_days(entry: RequirementRegistryEntry, limits: PrdActiveLimits) -> int | None:
    state = ExecutionState(entry.execution_state)
    if state == ExecutionState.CANDIDATE:
        return limits.candidate_renewal_days
    if state == ExecutionState.QUEUED:
        return limits.queued_renewal_days
    if state == ExecutionState.BLOCKED_EXTERNAL:
        return limits.blocked_external_renewal_days
    return None  # active/closing do not expire by renewal window


def _scan_executable_entries(prds_dir: Path) -> list[RequirementRegistryEntry]:
    entries: list[RequirementRegistryEntry] = []
    if not prds_dir.exists():
        return entries
    for prd_file in sorted(prds_dir.glob("PRD-*.md")):
        try:
            raw = prd_file.read_bytes()
        except OSError:
            continue
        fm = parse_frontmatter(raw.decode("utf-8", errors="replace"))
        if not fm:
            continue
        status = str(fm.get("status", "draft")).strip().lower()
        if status in _TERMINAL_STATUSES:
            continue
        dates = fm.get("dates")
        updated = str(dates.get("updated", "")) if isinstance(dates, dict) else ""
        traceability = fm.get("traceability")
        depends = (
            [str(d) for d in traceability.get("depends_on", [])]
            if isinstance(traceability, dict) and isinstance(traceability.get("depends_on"), list)
            else []
        )
        entries.append(
            RequirementRegistryEntry(
                prd_id=str(fm.get("id", prd_file.stem)),
                title=str(fm.get("title", "")),
                lifecycle_status=status,
                priority=str(fm.get("priority", "P1")).upper(),
                category=str(fm.get("category", "")).upper(),
                dependencies=depends,
                owner=str(fm.get("owner", "") or "unassigned"),
                execution_state=ExecutionState.CANDIDATE,
                renewal_date=updated if _ISO_DATE_RE.match(updated) else "",
                source_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
            )
        )
    entries.sort(key=lambda entry: entry.prd_id)
    return entries


def build_registry(
    prds_dir: Path,
    ledger_path: Path,
    *,
    limits: PrdActiveLimits | None = None,
) -> RegistryBuildResult:
    """Reconcile PRD intent + the committed scheduling ledger into the registry.

    A stale, forked, or tampered ledger yields ``status="stale_scheduling_head"``
    — an unknown result that cannot activate, renew, expire, or release WIP.
    """
    effective_limits = limits or PrdActiveLimits()
    try:
        actions = load_ledger(ledger_path)
        # A clean truncation is a valid prefix, and a tail rewrite has nothing
        # chained atop it — only the committed head anchor detects both.
        verify_ledger_head_anchor(ledger_path, actions)
    except SchedulingLedgerError as exc:
        return RegistryBuildResult(status="stale_scheduling_head", limits=effective_limits, error=str(exc))

    epoch = derive_evaluation_epoch(actions)
    entries = _scan_executable_entries(prds_dir)
    by_id = {entry.prd_id: entry for entry in entries}
    for action in actions:
        target = by_id.get(action.payload.get("prd_id", ""))
        if target is None:
            continue
        if action.kind == "set_execution_state":
            target.execution_state = ExecutionState(action.payload["state"])
            target.renewal_date = action.effective_utc_date
            if action.payload.get("owner"):
                target.owner = action.payload["owner"]
        elif action.kind == "renew":
            target.renewal_date = action.effective_utc_date

    epoch_date = date.fromisoformat(epoch.effective_utc_date)
    expired: list[str] = []
    hot_path: list[str] = []
    for entry in entries:
        window = _renewal_window_days(entry, effective_limits)
        is_expired = False
        if window is not None and entry.renewal_date:
            try:
                is_expired = (epoch_date - date.fromisoformat(entry.renewal_date[:10])).days > window
            except ValueError:
                is_expired = False
        if is_expired:
            # Expiry removes the record from the hot path WITHOUT touching
            # lifecycle status or evidence (FR04).
            expired.append(entry.prd_id)
        else:
            hot_path.append(entry.prd_id)

    return RegistryBuildResult(
        status="ok",
        entries=entries,
        epoch=epoch,
        head_digest=ledger_head_digest(actions),
        hot_path=hot_path,
        expired=expired,
        limits=effective_limits,
    )


@dataclass(slots=True)
class ActivationDecision:
    """Typed WIP-activation outcome — failures name the occupied slots."""

    allowed: bool
    reason: str
    occupied_slots: list[str] = field(default_factory=list)


def evaluate_activation(registry: RegistryBuildResult, prd_id: str) -> ActivationDecision:
    """Check nested WIP limits for activating ``prd_id`` (PRD-QUAL-121-FR04)."""
    if registry.status != "ok":
        return ActivationDecision(False, f"registry unknown: {registry.status}")
    candidate = next((entry for entry in registry.entries if entry.prd_id == prd_id), None)
    if candidate is None:
        return ActivationDecision(False, f"{prd_id} is not in the executable registry")

    limits = registry.limits
    wip_states = {ExecutionState.ACTIVE.value, ExecutionState.BLOCKED_EXTERNAL.value}
    wip = [entry for entry in registry.entries if str(entry.execution_state) in wip_states]

    def _ids(items: list[RequirementRegistryEntry]) -> list[str]:
        return sorted(entry.prd_id for entry in items)

    if str(candidate.execution_state) == ExecutionState.BLOCKED_EXTERNAL.value:
        owner_blocked = [
            entry
            for entry in wip
            if entry.owner == candidate.owner
            and str(entry.execution_state) == ExecutionState.BLOCKED_EXTERNAL.value
            and entry.prd_id != prd_id
        ]
        if len(owner_blocked) >= limits.blocked_external_exception_max:
            return ActivationDecision(
                False,
                f"blocked-external exception limit {limits.blocked_external_exception_max} "
                f"for owner {candidate.owner} is occupied",
                _ids(owner_blocked),
            )

    p0 = [entry for entry in wip if entry.priority == "P0" and entry.prd_id != prd_id]
    p0_p1 = [entry for entry in wip if entry.priority in ("P0", "P1") and entry.prd_id != prd_id]
    checks: list[tuple[bool, list[RequirementRegistryEntry], str, int]] = [
        (candidate.priority == "P0", p0, "global P0 active", limits.global_p0_active_max),
        (candidate.priority in ("P0", "P1"), p0_p1, "global P0/P1 active", limits.global_p0_p1_active_max),
        (
            candidate.priority == "P0",
            [entry for entry in p0 if entry.owner == candidate.owner],
            f"per-owner P0 active ({candidate.owner})",
            limits.per_owner_p0_active_max,
        ),
        (
            candidate.priority in ("P0", "P1"),
            [entry for entry in p0_p1 if entry.owner == candidate.owner],
            f"per-owner P0/P1 active ({candidate.owner})",
            limits.per_owner_p0_p1_active_max,
        ),
    ]
    for applies, occupied, label, maximum in checks:
        if applies and len(occupied) >= maximum:
            return ActivationDecision(False, f"{label} limit {maximum} is occupied", _ids(occupied))
    return ActivationDecision(True, "activation permitted within limits")


def persist_registry(registry: RegistryBuildResult, registry_dir: Path) -> Path:
    """Write the canonical registry document + receipt digest (projection input)."""
    registry_dir.mkdir(parents=True, exist_ok=True)
    target = registry_dir / REGISTRY_FILENAME
    document = registry.canonical_document()
    rendered = json.dumps(
        {"registry": document, "receipt_digest": registry.receipt_digest()},
        sort_keys=True,
        indent=2,
    )
    target.write_text(rendered + "\n", encoding="utf-8")
    return target
