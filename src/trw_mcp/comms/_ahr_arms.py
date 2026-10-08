"""The record-referencing arms of the AHR lifecycle replay (part of ``_ahr_state``).

Belongs to the ``_ahr_state`` module: :meth:`AhrLog.step` dispatches the ``readback_verified``,
``declined``, ``reported`` and ``superseded`` events here. Each arm is the reference checker's
``if kind == ...`` branch verbatim (``specs/handoff/tools/ahr_lifecycle.py``), with the loop's locals
carried in :class:`Step`; the vendored lifecycle vectors are the regression suite for both files.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from trw_mcp.handoff import validate

if TYPE_CHECKING:
    from trw_mcp.comms._ahr_state import AhrLog

JsonDoc = dict[str, Any]
AHR_MEDIA = "application/vnd.ahr+json"
TIER_RANK = {"minimal": 0, "standard": 1, "critical": 2}


def check_doc(doc: JsonDoc, handoff: JsonDoc | None = None) -> list[str]:
    """The reference ``check_doc``: every L1 finding as ``"<rule> <message>"``."""
    return [f"{finding.rule} {finding.message}" for finding in validate(doc, handoff)]


def _names_claim(claim_id: str, rationale: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9._:-]){re.escape(claim_id)}(?![A-Za-z0-9._:-])", rationale) is not None


@dataclass(frozen=True)
class Step:
    """The reference loop's locals for one event, as :meth:`AhrLog.step` computed them."""

    ev: JsonDoc
    resolved: Any
    actor: str
    tier: str
    sender: str
    addressed: bool
    completer: str | None
    verifier_id: str | None
    verifier_kind: str | None
    handoff: JsonDoc
    may_receive: bool
    fail: Callable[[str, str], object]


def readback_verified(log: AhrLog, s: Step) -> object:
    """R-LC-2/R-RB-5/R-RB-9/R-RB-11/R-RB-12: a verdict on a read-back (critical tier, S4)."""
    ev, actor, tier, sender, addressed, verifier_id, verifier_kind, handoff = (
        s.ev,
        s.actor,
        s.tier,
        s.sender,
        s.addressed,
        s.verifier_id,
        s.verifier_kind,
        s.handoff,
    )
    if log.state not in ("offered", "accepted"):
        return s.fail("R-LC-2", "readback_verified is allowed only while offered or accepted")
    v = ev["verification"]
    known = {d: rb for d, rb, _ in log.latest_rb.values()}
    rb = known.get(v["readback_digest"])
    if rb is None:
        return s.fail("R-RB-5", "verification names no read-back that is the latest of its author")
    if tier == "critical" and v["method"] == "deterministic":
        return s.fail("R-RB-12", "at critical a verdict must use method judge or human")
    if v["verifier"]["id"] != actor or v["verifier"]["kind"] != ev["actor"]["kind"] or actor == rb["by"]["id"]:
        return s.fail("R-RB-5", "the verifier must be the event actor and not the read-back author")
    if not addressed and actor in log.latest_rb:
        return s.fail("R-RB-5", "an unaddressed record's verifier must not have read it back")
    if (verifier_id and actor != verifier_id) or (not verifier_id and actor != sender):
        return s.fail(
            "R-RB-5",
            "only the designated verifier (or, if none is designated, the sender) may record a verdict",
        )
    if v["method"] == "human" and (
        ev["actor"]["kind"] != "human" or (verifier_kind and ev["actor"]["kind"] != verifier_kind)
    ):
        return s.fail("R-RB-9", "method 'human' requires a verifier principal of kind 'human'")
    ncs = [r["claim_id"] for r in rb["reverified"] if r["result"] == "not_checkable"]
    passing = v["outcome"] in ("pass", "pass_with_flags")
    if (
        tier == "critical"
        and passing
        and v["method"] == "human"
        and any(not _names_claim(cid, v["rationale"]) for cid in ncs)
    ):
        return s.fail("R-RB-9", "a human verdict must name each not_checkable claim_id in rationale")
    reverify = handoff.get("readback", {}).get("reverify", [])
    if (
        tier == "critical"
        and passing
        and (
            rb["disposition"] != "ready"
            or any(r["result"] in (None, "not_checked") for r in rb["reverified"] if r["claim_id"] in reverify)
            or set(reverify) - {r["claim_id"] for r in rb["reverified"]}
        )
    ):
        return s.fail("R-RB-11", "at critical a passing verdict needs a ready read-back that completes re-verification")
    if (
        tier == "critical"
        and passing
        and v["method"] != "human"
        and (log.any_fail or any(r["result"] == "not_checkable" for r in rb["reverified"]))
    ):
        return s.fail("R-RB-9", "at critical this passing verdict needs method 'human'")
    log.verdict[v["readback_digest"]] = v
    log.verifiers.add(actor)
    log.any_fail = log.any_fail or v["outcome"] == "fail" or (v["method"] == "judge" and v.get("confidence") == "low")
    return None


def declined(log: AhrLog, s: Step) -> object:
    """R-LC-2 and R-TIER-5: a decline, which on an unaddressed record must escalate."""
    ev, resolved, actor, addressed, completer, handoff = s.ev, s.resolved, s.actor, s.addressed, s.completer, s.handoff
    if log.state != "offered" or not s.may_receive:
        return s.fail(
            "R-LC-2",
            "declined must follow offered and come from the addressed receiver or an in-scope principal",
        )
    if not addressed and not (
        isinstance(resolved, dict)
        and resolved.get("type") == "handoff"
        and resolved.get("tier") == "critical"
        and resolved["to"].get("kind") != "unaddressed"
        and not check_doc(resolved)
        and resolved["subject"] != handoff["subject"]
        and resolved["from"]["id"] == actor
        and resolved["from"]["kind"] == ev["actor"]["kind"]
        and actor != completer
        and any(p.get("kind") == "record" and p.get("digest") == log.record_digest for p in resolved["next_read"])
        and all(p["handoff_id"] != handoff["handoff_id"] for p in resolved.get("supersedes", []))
    ):
        return s.fail(
            "R-TIER-5",
            "an unaddressed record is declined only to escalate, with ref to a critical handoff to a named "
            "principal, under a new subject and not listing the original",
        )
    log.state = "declined"
    return None


def reported(log: AhrLog, s: Step) -> object:
    """R-LC-2/R-RB-4/R-LC-12/R-TIER-5: a report, optionally with an onward handoff."""
    ev, resolved, actor, tier, handoff = s.ev, s.resolved, s.actor, s.tier, s.handoff
    if log.state != "accepted" or actor != log.receiver:
        return s.fail("R-LC-2", "reported must follow accepted and come from the receiver")
    mine = log.latest_rb.get(actor)
    if ev["outcome"] == "met" and mine and any(r["result"] == "contradicted" for r in mine[1]["reverified"]):
        return s.fail("R-RB-4", "outcome 'met' while the receiver's latest read-back lists a contradicted claim")
    if isinstance(resolved, dict) and resolved.get("type") != "handoff":
        return s.fail("R-LC-2", "a reported ref may be an AHR record only when it is an onward handoff")
    onward = isinstance(resolved, dict) and resolved.get("type") == "handoff" and not check_doc(resolved)
    if isinstance(resolved, dict) and resolved.get("type") == "handoff" and not onward:
        return s.fail("R-LC-12", "an onward handoff in a report must be a valid record")
    if ev["outcome"] == "met" and onward:  # rc.2 (A25-1): passing work on is `returned` or `escalated`
        return s.fail("R-LC-12", "a report with outcome 'met' must not ref an onward handoff")
    if ev["outcome"] == "escalated" and not (
        onward and resolved["tier"] == "critical" and resolved["to"].get("kind") != "unaddressed"
    ):
        return s.fail("R-TIER-5", "an escalated report must reference a critical handoff to a named principal")
    if onward and (
        resolved["subject"] != handoff["subject"]
        or resolved["from"]["id"] != actor
        or resolved["from"]["kind"] != ev["actor"]["kind"]
        or {"handoff_id": handoff["handoff_id"], "digest": log.record_digest} not in resolved.get("supersedes", [])
        or TIER_RANK[resolved["tier"]] < TIER_RANK[tier]
        or check_doc(resolved)
    ):
        return s.fail(
            "R-LC-12",
            "an onward handoff in a report must be valid, from the receiver, same subject, "
            "no lower tier, and list this record in supersedes",
        )
    log.state = "reported"
    return None


def superseded(log: AhrLog, s: Step) -> object:
    """R-SUP-2: an offered record ends because its sender listed it in a new record."""
    ev, resolved, actor, tier, sender, handoff = s.ev, s.resolved, s.actor, s.tier, s.sender, s.handoff
    if log.state != "offered":
        return s.fail("R-SUP-2", "an accepted record ends by reported/completed, not superseded (R-LC-12)")
    new = resolved
    if (
        ev["ref"].get("media_type") != AHR_MEDIA
        or not isinstance(new, dict)
        or new.get("type") != "handoff"
        or check_doc(new)
    ):
        s.fail("R-SUP-2", "ref must be a valid, digest-bound superseding handoff")
    elif {"handoff_id": handoff["handoff_id"], "digest": log.record_digest} not in new.get("supersedes", []):
        s.fail("R-SUP-2", "the new record does not list this record and its digest in supersedes")
    elif (
        actor != sender
        or new["from"]["id"] != actor
        or new["from"]["kind"] != ev["actor"]["kind"]
        or new["subject"] != handoff["subject"]
    ):
        s.fail(
            "R-SUP-2",
            "only the sender may supersede its offered record, with a record it sends for the same subject",
        )
    elif TIER_RANK[new["tier"]] < TIER_RANK[tier]:  # R-SUP-2 floor (live or terminal predecessor)
        s.fail("R-SUP-2", "a superseding record must not have a lower tier")
    else:
        log.state = "superseded"
    return None


ARMS: dict[str, Callable[[AhrLog, Step], object]] = {
    "readback_verified": readback_verified,
    "declined": declined,
    "reported": reported,
    "superseded": superseded,
}

__all__ = ["AHR_MEDIA", "ARMS", "TIER_RANK", "Step", "check_doc"]
