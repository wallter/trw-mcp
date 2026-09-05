"""Read-only roll-up of member run state (FR07, FR08, FR11).

WHAT THIS READS, AND WHAT IT REFUSES TO READ. One row per member, assembled
from that member's OWN run directory: ``meta/run.yaml`` for phase and run
status, the tail of ``meta/checkpoints.jsonl`` for the last checkpoint, and the
tail of ``meta/events.jsonl`` for the latest build and review outcome. It opens
no memory store and it MUST NOT read, import, or ingest any member's learnings
or handoff text. That is not a performance choice: under
``docs/CONSTITUTION.md`` a delegated agent's memory is untrusted data, and
promoting it into the orchestrator's knowledge base because the two runs share
a manifest would launder unverified content into the project's memory. The
prohibition is asserted by a memory-write spy in the FR07 test.

STALENESS IS REPORTED, NEVER APPLIED (FR08). A member whose pin is gone or
whose heartbeat is past ``pin_ttl_hours`` is annotated ``stale`` with a distinct
reason. Its recorded status does not change, and nothing here writes anything:
an agent that is merely slow is indistinguishable from one that died, and
guessing would either discard live work or fabricate completion. Only an
FR05-authenticated orchestrator revision may move a stale member to
``abandoned`` or ``reassigned``. No scheduler, timer, or background task is
added — staleness is computed at read time from state the pin store already
maintains.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

from trw_mcp.formation._manifest import TERMINAL_STATUSES, FormationManifest

logger = structlog.get_logger(__name__)

__all__ = ["MemberRow", "has_delivery_record", "member_rows", "non_terminal_members"]

#: What counts as a DELIVERY RECORD on a member's OWN run. FR11 re-checks this
#: independently of the member's self-reported manifest status, so a
#: ``delivered`` stamp with no record behind it — a hand-edited manifest, or a
#: facade call from a member that never delivered — cannot satisfy the gate.
#:
#: Two accepted shapes, because two different writers produce them: the run
#: STATUS is what the stale-run sweep records when it closes a run, and the
#: EVENT is what the member's own successful ``trw_deliver`` appends. Requiring
#: only the status would make the gate block forever after a legitimate member
#: delivery, which the PRD names as worse than having no gate at all.
_DELIVERED_RUN_STATUSES = frozenset({"delivered", "complete"})
DELIVERY_EVENT = "trw_deliver_complete"

_BUILD_EVENT = "build_check_complete"
_REVIEW_EVENT = "review_complete"
#: Bound on the events tail scanned per member. A run accumulates thousands of
#: ``tool_invocation`` rows; the roll-up needs only the most recent build and
#: review, and the 2-second SLO for 16 members is what makes the bound explicit
#: rather than a full file read.
_EVENT_TAIL_LINES = 400


@dataclass(frozen=True)
class MemberRow:
    """One rendered status row. Every field is derived; none is typed by hand."""

    member_id: str
    client: str
    role: str
    status: str
    run_path: str = ""
    phase: str = ""
    last_checkpoint: str = ""
    last_checkpoint_ts: str = ""
    build: str = ""
    review: str = ""
    delivery: str = "none"
    stale: bool = False
    stale_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """Plain-data projection for the ``trw_status`` block and the CLI."""
        return {
            "member_id": self.member_id,
            "client": self.client,
            "role": self.role,
            "status": self.status,
            "run_path": self.run_path,
            "phase": self.phase,
            "last_checkpoint": self.last_checkpoint,
            "last_checkpoint_ts": self.last_checkpoint_ts,
            "build": self.build,
            "review": self.review,
            "delivery": self.delivery,
            "stale": self.stale,
            "stale_reason": self.stale_reason,
        }


def member_rows(manifest: FormationManifest, *, member_limit: int, pin_ttl_hours: int) -> list[MemberRow]:
    """Assemble one row per member, up to *member_limit*."""
    raw_pins = _raw_pin_store()
    return [
        _row_for(member, raw_pins=raw_pins, pin_ttl_hours=pin_ttl_hours) for member in manifest.members[:member_limit]
    ]


def _row_for(member: Any, *, raw_pins: dict[str, Any], pin_ttl_hours: int) -> MemberRow:
    run_path = Path(member.run_path) if member.run_path else None
    phase = ""
    delivery = "none"
    warnings: list[str] = []
    if run_path is not None:
        run_data = _read_yaml(run_path / "meta" / "run.yaml")
        phase = str(run_data.get("phase", "")) if run_data else ""
        if has_delivery_record(run_path):
            delivery = "delivered"
    if str(member.status) == "delivered" and delivery != "delivered":
        warnings.append("member reports delivered but its run carries no delivery record")
    message, ts = _last_checkpoint(run_path)
    stale, reason = _staleness(member, raw_pins=raw_pins, pin_ttl_hours=pin_ttl_hours)
    build, review = _latest_outcomes(run_path)
    return MemberRow(
        member_id=member.member_id,
        client=member.client,
        role=member.role,
        status=str(member.status),
        run_path=member.run_path or "",
        phase=phase,
        last_checkpoint=message,
        last_checkpoint_ts=ts,
        build=build,
        review=review,
        delivery=delivery,
        stale=stale,
        stale_reason=reason,
        warnings=warnings,
    )


def has_delivery_record(run_path: Path) -> bool:
    """True when *run_path*'s own run carries delivery evidence (FR11)."""
    if _run_status(_read_yaml(run_path / "meta" / "run.yaml")) in _DELIVERED_RUN_STATUSES:
        return True
    return _has_delivery_event(run_path / "meta" / "events.jsonl")


def _has_delivery_event(events_path: Path) -> bool:
    """Whether the run's event log carries a delivery-complete record."""
    if not events_path.is_file():
        return False
    try:
        lines = events_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        if DELIVERY_EVENT not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and str(record.get("event") or record.get("event_type") or "") == DELIVERY_EVENT:
            return True
    return False


def non_terminal_members(manifest: FormationManifest) -> list[tuple[str, str]]:
    """``(member_id, status)`` for every joined member the gate must wait on.

    A ``delivered`` member whose run carries no delivery record is INCLUDED, as
    FR11 requires: the self-report is a claim, and a claim with no evidence
    behind it is exactly what the gate exists to catch.
    """
    pending: list[tuple[str, str]] = []
    for member in manifest.members:
        if not member.run_path:
            continue
        status = str(member.status)
        if status not in TERMINAL_STATUSES:
            pending.append((member.member_id, status))
        elif status == "delivered" and not has_delivery_record(Path(member.run_path)):
            pending.append((member.member_id, "delivered (no delivery record on its run)"))
    return pending


def _run_status(run_data: dict[str, object] | None) -> str:
    return str(run_data.get("status", "")).strip().lower() if run_data else ""


def _read_yaml(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("formation_member_yaml_unreadable", path=str(path), error=str(exc))
        return None
    return data if isinstance(data, dict) else None


def _last_checkpoint(run_path: Path | None) -> tuple[str, str]:
    if run_path is None:
        return "", ""
    path = run_path / "meta" / "checkpoints.jsonl"
    if not path.is_file():
        return "", ""
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return "", ""
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            return str(record.get("message", "")), str(record.get("ts", ""))
    return "", ""


def _latest_outcomes(run_path: Path | None) -> tuple[str, str]:
    """Latest build and review outcome from the bounded events tail."""
    build = ""
    review = ""
    if run_path is None:
        return build, review
    path = run_path / "meta" / "events.jsonl"
    if not path.is_file():
        return build, review
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-_EVENT_TAIL_LINES:]
    except OSError:
        return build, review
    for line in reversed(lines):
        if build and review:
            break
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        name = str(record.get("event") or record.get("event_type") or "")
        if not build and name == _BUILD_EVENT:
            build = "passed" if record.get("tests_passed") else "failed"
        elif not review and name == _REVIEW_EVENT:
            review = str(record.get("verdict") or record.get("status") or "recorded")
    return build, review


def _raw_pin_store() -> dict[str, Any]:
    """The pin store as written, WITHOUT the eviction passes.

    ``load_pin_store`` drops an expired entry, so through it "pin absent" and
    "pin expired" are one answer. FR08 requires two distinguishable reasons, so
    the raw file is read once per roll-up and the expiry predicate is applied
    here, using the pin store's own ``pin_entry_is_expired`` rather than a
    second age rule.
    """
    from trw_mcp.state._pin_store import pin_store_path

    path = pin_store_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _staleness(member: Any, *, raw_pins: dict[str, Any], pin_ttl_hours: int) -> tuple[bool, str]:
    from trw_mcp.state._pin_ttl import pin_entry_is_expired

    if str(member.status) in TERMINAL_STATUSES or not member.run_path:
        return False, ""
    pin_key = member.pin_key
    if not pin_key:
        return True, "pin absent (member joined without a pin key)"
    entry = raw_pins.get(pin_key)
    if not isinstance(entry, dict):
        return True, "pin absent"
    expired, age = pin_entry_is_expired(entry, pin_ttl_hours=pin_ttl_hours)
    if expired:
        age_text = f"{age:.1f}h" if age is not None else "unknown age"
        return True, f"pin expired (heartbeat age {age_text}, ttl {pin_ttl_hours}h)"
    return False, ""
