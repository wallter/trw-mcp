"""Draft an AHR 1.0-rc.2 read-back for a handoff record (``trw-mcp handoff readback-new``).

Belongs to the :mod:`trw_mcp.handoff` package. The tool fills what the receiver should never
hand-compute: the read-back id, its author (``by``), the UTC ``at``, the handoff's digest, the
``pointer_checks`` from ``check`` and one ``reverified`` entry per claim to re-verify. The
restatements, results and evidence hold the ``TODO(handoff):`` sentinel, so an unfilled draft
never seals (``validate`` reports ``placeholder``).

``by``: when the record is addressed to a principal, the read-back author must be that principal
(X-13). The draft takes ``to`` only when the caller confirms it is the addressee: either the
record names an agent of this harness (``claude-code:next`` read by a Claude Code session) or
the caller passes ``as_addressee``. Otherwise ``AddresseeError``: stop and ask the user.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from trw_mcp.handoff._jcs import digest
from trw_mcp.handoff._validate import PLACEHOLDER as _T

__all__ = ["AddresseeError", "build_readback_draft", "reverify_ids"]

JsonDoc = dict[str, Any]
_SEVERITY = {"low": 0, "medium": 1, "high": 2}


class AddresseeError(ValueError):
    """The record is addressed to another principal; this session must not read it back as that principal."""


def reverify_ids(handoff: JsonDoc) -> list[str]:
    """``readback.reverify`` when given, else every verified claim plus what ``next_actions[0]`` depends on."""
    listed = handoff.get("readback", {}).get("reverify")
    if listed:
        return list(listed)
    ids = [c["id"] for c in handoff["claims"] if c["label"] == "verified"]
    ids += [cid for cid in handoff["next_actions"][0].get("depends_on", []) if cid not in ids]
    return ids


def _author(handoff: JsonDoc, sender: str, harness_name: str, *, as_addressee: bool) -> JsonDoc:
    to = handoff["to"]
    if to.get("kind") == "unaddressed":
        return {"id": sender, "kind": "agent"}
    mine = to["kind"] == "agent" and str(to["id"]).partition(":")[0] == harness_name
    if not (mine or as_addressee):
        raise AddresseeError(
            f"the record is addressed to {to['id']} ({to['kind']}), not to this {harness_name} session: "
            "stop and tell the user; pass --as-addressee only if the user confirms you are that principal"
        )
    return {"id": to["id"], "kind": to["kind"]}


def _top_risk(handoff: JsonDoc) -> JsonDoc:
    risks = handoff["risks"] if isinstance(handoff["risks"], list) else []
    out: JsonDoc = {"restated": f"{_T} the top risk in your own words"}
    if risks:
        out = {"risk_id": max(risks, key=lambda r: _SEVERITY[r["severity"]])["id"]} | out
    return out


def _reverified(cid: str, producer: str, critical: bool) -> JsonDoc:
    evidence: JsonDoc = {
        "procedure": f"{_T} the exact command you ran yourself",
        "scope": f"{_T} what your run covered and what it did not",
        "result": f"{_T} supports | contradicts | inconclusive",
        "at": f"{_T} UTC time you ran it (date -u +%Y-%m-%dT%H:%M:%SZ), before this read-back's at",
        "producer": producer,
    }
    if critical:  # X-14: a confirmed claim at critical carries the raw output, digest-bound
        evidence["raw"] = {
            "uri": f"{_T} file:<repo-relative path of the saved output>",
            "digest": f"{_T} sha256:<raw bytes of that file>",
        }
    allowed = "confirmed | contradicted | not_checkable" + ("" if critical else " | not_checked")
    return {"claim_id": cid, "result": f"{_T} {allowed}", "evidence": evidence}


def build_readback_draft(
    handoff: JsonDoc,
    report: JsonDoc,
    *,
    sender: str,
    harness_name: str,
    now: datetime,
    as_addressee: bool = False,
) -> JsonDoc:
    """A read-back draft bound to ``handoff``; ``report`` is ``check_record``'s output for it."""
    by = _author(handoff, sender, harness_name, as_addressee=as_addressee)
    critical = handoff["tier"] == "critical"
    pointer_checks = [{k: v for k, v in pc.items() if k != "reason"} for pc in report["pointer_checks"]]
    discrepancies = [
        {"pointer_index": pc["index"], "text": f"{_T} what changed in pointer {pc['index']} and whether it matters"}
        for pc in pointer_checks
        if pc["status"] in ("drift", "missing")
    ]
    doc: JsonDoc = {
        "ahr": "1.0-rc.2",
        "type": "readback",
        "readback_id": f"rb-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}",
        "handoff": {"handoff_id": handoff["handoff_id"], "digest": digest(handoff)},
        "by": by,
        "at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "goal_restated": f"{_T} the goal in your own words",
        "first_action": {
            "action_id": handoff["next_actions"][0]["id"],
            "restated": f"{_T} the first action in your own words",
        },
        "top_risk": _top_risk(handoff),
    }
    constraints = handoff.get("constraints")
    if isinstance(constraints, list) and constraints:
        doc["constraints_restated"] = [
            {"index": i, "text": f"{_T} constraint {i}, verbatim or restated"} for i in range(len(constraints))
        ]
    doc |= {
        "pointer_checks": pointer_checks,
        "reverified": [_reverified(cid, by["id"], critical) for cid in reverify_ids(handoff)],
        "discrepancies": discrepancies,
        "questions": [],
        "disposition": f"{_T} ready | questions",
    }
    return doc
