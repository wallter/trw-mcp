"""AHR 1.0-rc.2 static cross-field rules for handoffs and read-backs (SPEC §13).

A port of ``check_handoff``/``check_readback`` from the reference checker
(``specs/handoff/tools/ahr_check.py``). The reference checker and the vectors are
authoritative where the SPEC prose appears to differ (PRD-CORE-347-FR03). Each
message starts with its rule id; ``_validate`` turns them into ``Finding`` objects.
Both functions assume the document already passed the schema.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any

from trw_mcp.handoff._jcs import digest

__all__ = ["check_handoff", "check_readback", "instant", "norm", "visible"]

JsonDoc = dict[str, Any]

PLACEHOLDERS = frozenset({"", "n a", "na", "none", "nothing", "tbd", "todo", "unknown", "checked", "ok"})
_WHITESPACE = frozenset(
    "\t\n\u000b\u000c\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)  # Unicode White_Space
_FILLERS = frozenset({"\u3164", "\u1160", "\u115f", "\uffa0"})
_SEVERITY = {"low": 0, "medium": 1, "high": 2}


def instant(value: str) -> datetime:
    """Parse the R-TIME-1 profile; raises ``ValueError`` on an impossible calendar date."""
    base, _, frac = value[:-1].partition(".")
    frac = (frac + "000000")[:6] if frac else "000000"
    return datetime.fromisoformat(f"{base}.{frac}+00:00")


def norm(text: str) -> str:
    """X-8 normalization: NFKC, casefold, drop punctuation/symbols/format chars, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] not in "PS" and unicodedata.category(ch) != "Cf")
    spaced = "".join(" " if ch in _WHITESPACE else ch for ch in text)
    return " ".join(tok for tok in spaced.split(" ") if tok)


def visible(text: str) -> bool:
    """X-12: at least one character outside Unicode categories C, M, P, S, Z (and Hangul fillers)."""
    return any(
        unicodedata.category(ch)[0] not in "CMPSZ" and ch not in _FILLERS for ch in unicodedata.normalize("NFKC", text)
    )


def _placeholder(text: str) -> bool:
    return norm(text) in PLACEHOLDERS


def _risk_ids(handoff: JsonDoc) -> list[str]:
    return [r["id"] for r in handoff["risks"]] if isinstance(handoff["risks"], list) else []


def _handoff_ids(doc: JsonDoc, claims: dict[str, JsonDoc], errs: list[str]) -> None:
    ids = [c["id"] for c in doc["claims"]] + [a["id"] for a in doc["next_actions"]] + _risk_ids(doc)
    if len(ids) != len(set(ids)):
        errs.append("X-2 claim, action and risk ids must be unique within a record")
    requested: list[str] = doc.get("readback", {}).get("reverify", [])
    if len(requested) != len(set(requested)):
        errs.append("X-3 readback.reverify repeats a claim id")
    errs.extend(
        f"X-3 readback.reverify names {cid!r}, which is not a claim in this record"
        for cid in requested
        if cid not in claims
    )
    errs.extend(
        f"X-3 next_actions {action['id']!r} depends_on unknown claim {cid!r}"
        for action in doc["next_actions"]
        for cid in action.get("depends_on", [])
        if cid not in claims
    )
    if doc["tier"] == "critical":
        want = {cid for cid, c in claims.items() if c["label"] == "verified"}
        for action in doc["next_actions"]:
            want |= set(action.get("depends_on", []))
        missing = sorted(want - set(requested))
        if missing:
            errs.append(
                "X-4 critical: readback.reverify must include every verified claim and every "
                f"claim any next action depends on; missing {missing}"
            )


def _handoff_principals(doc: JsonDoc, errs: list[str]) -> None:
    to = doc["to"]
    if to.get("kind") != "unaddressed" and to["id"] == doc["from"]["id"]:
        errs.append("X-17 an addressed handoff cannot name its sender as receiver")
    verifier = doc.get("readback", {}).get("verifier")
    if verifier and to.get("id") == verifier["id"]:
        errs.append("X-17 the designated verifier cannot be the receiver")
    principals = [doc["from"]] + ([to] if to.get("id") else []) + ([to["completer"]] if to.get("completer") else [])
    principals += [verifier] if verifier else []
    seen_kind: dict[str, str] = {}
    errs.extend(
        f"X-21 principal id {pr['id']!r} appears with two different kinds"
        for pr in principals
        if seen_kind.setdefault(pr["id"], pr["kind"]) != pr["kind"]
    )


def _handoff_texts(doc: JsonDoc, claims: dict[str, JsonDoc], errs: list[str]) -> None:
    texts = [c["text"] for c in doc["claims"]] + [doc["objective"]["goal"]] + doc["objective"]["done_when"]
    texts += [c.get("basis", "x") for c in doc["claims"]]
    texts += [a["rollback"]["procedure"] for a in doc["next_actions"] if "rollback" in a]
    texts += [doc[n]["checked"] for n in ("not_done", "risks", "unknowns") if isinstance(doc[n], dict)]
    constraints = doc.get("constraints")
    if isinstance(constraints, dict):
        texts.append(constraints["checked"])
        if _placeholder(constraints["checked"]):
            errs.append("X-12 constraints.checked is a placeholder (R-EMP-2)")
    if not all(visible(t) for t in texts):
        errs.append("X-12 a required text field has no visible content")
    errs.extend(
        "X-12 an excerpted constraint needs a digest-bound source"
        for con in (constraints if isinstance(constraints, list) else [])
        if isinstance(con, dict) and con.get("excerpt") and "source" not in con
    )
    errs.extend(
        f"X-12 {name}.checked is a placeholder, not a description of a check (R-EMP-2)"
        for name in ("not_done", "risks", "unknowns")
        if isinstance(doc[name], dict) and _placeholder(doc[name]["checked"])
    )
    for cid, claim in claims.items():
        errs.extend(
            f"X-12 claim {cid!r} evidence procedure or scope is empty or a placeholder"
            for ev in claim.get("evidence", [])
            if _bad_evidence_text(ev)
        )
        if "basis" in claim and _placeholder(claim["basis"]):
            errs.append(f"X-12 claim {cid!r} basis is a placeholder (R-CLM-3)")


def _bad_evidence_text(ev: JsonDoc) -> bool:
    return any(not visible(ev[k]) or _placeholder(ev[k]) for k in ("procedure", "scope"))


def check_handoff(doc: JsonDoc) -> list[str]:
    """Static X-rules for a handoff record (X-2..X-6, X-12, X-17, X-19..X-21)."""
    errs: list[str] = []
    claims = {c["id"]: c for c in doc["claims"]}
    _handoff_ids(doc, claims, errs)
    if doc.get("expires_at") and instant(doc["expires_at"]) <= instant(doc["created_at"]):
        errs.append("X-5 expires_at must be later than created_at")
    if instant(doc["as_of"]["at"]) > instant(doc["created_at"]):
        errs.append("X-6 as_of.at must not be later than created_at")
    _handoff_principals(doc, errs)
    if doc["tier"] == "critical":
        bare = [i for i, ptr in enumerate(doc["next_read"]) if "digest" not in ptr]
        if bare:
            errs.append(f"X-20 critical: every next_read pointer must carry a digest; missing at {bare}")
    sup = [p["handoff_id"] for p in doc.get("supersedes", [])]
    if len(sup) != len(set(sup)) or doc["handoff_id"] in sup:
        errs.append("X-19 supersedes must not repeat an entry or name the record itself")
    _handoff_texts(doc, claims, errs)
    return errs


def _readback_restatements(doc: JsonDoc, handoff: JsonDoc, errs: list[str]) -> None:
    first = handoff["next_actions"][0]
    rtexts = [doc["goal_restated"], doc["first_action"]["restated"], doc["top_risk"]["restated"]]
    rtexts += [doc["done_when_restated"]] if "done_when_restated" in doc else []
    rtexts += [e["text"] for e in doc.get("constraints_restated", [])]
    if not all(visible(t) for t in rtexts):
        errs.append("X-12 a restatement has no visible content")
    if doc["first_action"]["action_id"] != first["id"]:
        errs.append("X-8 first_action.action_id is not next_actions[0].id")
    if norm(doc["first_action"]["restated"]) == norm(first["action"]):
        errs.append("X-8 first_action.restated copies the source after normalization")
    if norm(doc["goal_restated"]) == norm(handoff["objective"]["goal"]):
        errs.append("X-8 goal_restated copies the source after normalization")
    risks = handoff["risks"] if isinstance(handoff["risks"], list) else []
    rid = doc["top_risk"].get("risk_id")
    if risks:
        by_id = {r["id"]: r for r in risks}
        if rid not in by_id:
            errs.append("X-8 top_risk.risk_id must name a listed risk when risks are listed")
        else:
            if _SEVERITY[by_id[rid]["severity"]] < max(_SEVERITY[r["severity"]] for r in risks):
                errs.append("X-8 top_risk.risk_id must name a risk of the highest listed severity")
            if norm(doc["top_risk"]["restated"]) == norm(by_id[rid]["text"]):
                errs.append("X-8 top_risk.restated copies the source after normalization")
    elif rid is not None:
        errs.append("X-8 top_risk.risk_id given but the handoff lists no risks")


def _readback_reverified(doc: JsonDoc, handoff: JsonDoc, ready: bool, errs: list[str]) -> None:
    claim_ids = {c["id"] for c in handoff["claims"]}
    got = [r["claim_id"] for r in doc["reverified"]]
    if len(got) != len(set(got)) or any(cid not in claim_ids for cid in got):
        errs.append("X-9 reverified claim ids must be unique and resolve in the handoff")
    results = {r["claim_id"]: r["result"] for r in doc["reverified"]}
    if handoff["tier"] == "critical" and ready:
        need = handoff.get("readback", {}).get("reverify", [])
        missing = sorted(c for c in need if results.get(c) in (None, "not_checked"))
        errs.extend(
            f"X-14 critical: confirmed claim {r['claim_id']!r} needs evidence.raw with a digest"
            for r in doc["reverified"]
            if r["result"] == "confirmed" and "digest" not in r.get("evidence", {}).get("raw", {})
        )
        if missing:
            errs.append(f"X-9 critical: requested re-verification not performed for {missing}")
    named = {d.get("claim_id") for d in doc["discrepancies"]}
    if any(d.get("pointer_index", 0) >= len(handoff["next_read"]) for d in doc["discrepancies"]):
        errs.append("X-11 a discrepancies pointer_index is out of range")
    if any(cid is not None and cid not in claim_ids for cid in named):
        errs.append("X-10 a discrepancies entry names a claim_id that is not in the handoff")
    for cid, result in results.items():
        if result == "contradicted" and cid not in named:
            errs.append(f"X-10 contradicted claim {cid!r} has no discrepancies entry with that claim_id")


def _readback_pointers(doc: JsonDoc, handoff: JsonDoc, ready: bool, errs: list[str]) -> None:
    ptrs = handoff["next_read"]
    critical_ready = handoff["tier"] == "critical" and ready
    if sorted(pc["index"] for pc in doc["pointer_checks"]) != list(range(len(ptrs))):
        errs.append("X-11 pointer_checks must cover each next_read index exactly once")
        return
    drift_named = {d.get("pointer_index") for d in doc["discrepancies"]}
    for pc in doc["pointer_checks"]:
        idx, status = pc["index"], pc["status"]
        has = "digest" in ptrs[idx]
        if status in ("match", "drift") and not has:
            errs.append(f"X-11 pointer {idx} has no digest, so status {status!r} is impossible")
        elif status in ("match", "drift") and (pc["observed_digest"] == ptrs[idx]["digest"]) != (status == "match"):
            errs.append(f"X-11 pointer {idx}: status {status!r} disagrees with observed_digest")
        if critical_ready and status in ("not_accessed", "missing"):
            errs.append(f"X-11 critical: pointer {idx} is {status!r} in a ready read-back")
        if critical_ready and status == "drift" and idx not in drift_named:
            errs.append(f"X-11 critical: drift on pointer {idx} needs a discrepancies entry with that pointer_index")
        if status == "no_digest" and has:
            errs.append(f"X-11 pointer {idx} carries a digest; status 'no_digest' is wrong")


def _readback_authorship(doc: JsonDoc, handoff: JsonDoc, errs: list[str]) -> None:
    if doc["by"]["id"] == handoff["from"]["id"]:
        errs.append("X-13 the sender cannot author the read-back for its own handoff")
    to = handoff["to"]
    if to.get("kind") != "unaddressed" and (doc["by"]["id"] != to["id"] or doc["by"]["kind"] != to["kind"]):
        errs.append("X-13 read-back author is not the addressed receiver")
    at = instant(doc["at"])
    created = instant(handoff["created_at"])
    if at < created:
        errs.append("X-13 read-back is earlier than the handoff's created_at")
    for r in doc["reverified"]:
        ev = r.get("evidence")
        if ev and _bad_evidence_text(ev):
            errs.append(
                f"X-12 re-verification evidence for {r['claim_id']!r} has an empty or placeholder procedure or scope"
            )
    for r in doc["reverified"]:
        ev = r.get("evidence")
        if ev and (ev.get("producer") != doc["by"]["id"] or not created <= instant(ev["at"]) <= at):
            errs.append(
                f"X-14 re-verification evidence for {r['claim_id']!r} must be produced by the "
                "read-back author between the handoff's created_at and the read-back's at"
            )


def check_readback(doc: JsonDoc, handoff: JsonDoc) -> list[str]:
    """Static X-rules for a read-back against its handoff (X-7..X-14, X-18)."""
    errs: list[str] = []
    if doc["handoff"]["handoff_id"] != handoff["handoff_id"] or doc["handoff"]["digest"] != digest(handoff):
        errs.append("X-7 read-back does not bind to this exact handoff (handoff_id or digest mismatch)")
    ready = doc["disposition"] == "ready"
    _readback_restatements(doc, handoff, errs)
    _readback_reverified(doc, handoff, ready, errs)
    _readback_pointers(doc, handoff, ready, errs)
    _readback_authorship(doc, handoff, errs)
    hcons = handoff.get("constraints", [])
    hcons = hcons if isinstance(hcons, list) else []
    idx = sorted(e["index"] for e in doc.get("constraints_restated", []))
    if handoff["tier"] == "critical" and ready and idx != list(range(len(hcons))):
        errs.append("X-18 critical: constraints_restated must restate each handoff constraint index exactly once")
    elif idx and (len(idx) != len(set(idx)) or idx[-1] >= len(hcons)):
        errs.append("X-18 constraints_restated indices must be unique and name handoff constraints")
    return errs
