"""Markdown view of an AHR handoff record (PRD-CORE-348-FR01, SPEC §13 and R-SEC-5).

A view only: the JSON record is normative and nothing parses this output back
(PRD-CORE-348-NFR01). The spec ships no reference renderer or vectors, so the golden
files under ``tests/handoff/golden/`` are TRW's own. Rendering a store's current state
for a subject (FR04) waits for the PRD-CORE-349 store.
"""

from __future__ import annotations

import json
import re
from typing import Any

from trw_mcp.handoff._jcs import digest

__all__ = ["escape", "render_markdown"]

JsonDoc = dict[str, Any]

# CommonMark backslash-escapable ASCII punctuation, minus & < > (entity-encoded in pass 2).
_PASS1 = re.compile(r"""([!"#$%'()*+,\-./:;=?@\[\\\]^_`{|}~])""")
_LINE_BREAKS = re.compile(r"\r\n|[\n\r\u0085\u2028\u2029\u000b\u000c]")
# Remaining C0/C1 controls and bidi overrides/isolates: a terminal or viewer could act on them.
_CONTROLS = re.compile(r"[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def escape(value: object) -> str:
    """R-SEC-5: one line, backslash-escape punctuation, then entity-encode & < >."""
    text = _CONTROLS.sub(" ", _LINE_BREAKS.sub(" ", str(value)))
    text = _PASS1.sub(r"\\\1", text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _none_known(block: JsonDoc) -> str:
    return f"None known — checked: {escape(block['checked'])}"


def _bullets(value: JsonDoc | list[str]) -> list[str]:
    if isinstance(value, dict):
        return [_none_known(value)]
    return [f"- {escape(item)}" for item in value]


def _principal(p: JsonDoc) -> str:
    extra = [escape(p[k]) for k in ("capability_tier", "model", "session") if k in p]
    return f"{escape(p['id'])} [{', '.join([escape(p['kind']), *extra])}]"


def _header(doc: JsonDoc) -> list[str]:
    to = doc["to"]
    target = f"unaddressed: {escape(to['scope'])}" if to.get("kind") == "unaddressed" else _principal(to)
    completer = escape(to["completer"]["id"]) if to.get("completer") else "none"
    as_of = doc["as_of"]
    base = as_of.get("base_ref") or {}
    changed = base.get("changed_paths", {}).get("uri", "—")
    integ = doc.get("integrity") or {}
    sealed = integ.get("digest")
    shown = escape(sealed) if sealed else f"{escape(digest(doc))} (computed; unsealed)"
    sup = " ".join(f"{escape(s['handoff_id'])}@{escape(s['digest'])}" for s in doc.get("supersedes", [])) or "none"
    status = (
        "Status: unadmitted note (advisory only; no lifecycle events, R-SUP-4)"
        if doc["tier"] == "minimal"
        else "Status: lifecycle state unknown (rendered from a file, not a store)"
    )
    return [
        f"# Handoff {escape(doc['handoff_id'])} — {escape(doc['subject'])}   "
        f"(tier: {escape(doc['tier'])}; {escape(doc.get('tier_reason', '—'))})",
        "",
        f"Rendered view of {escape(doc['handoff_id'])} {shown}; the JSON record is normative.  ",
        f"from {_principal(doc['from'])} → {target}; completer {completer}  ",
        f"created {escape(doc['created_at'])} · state as of {escape(as_of['at'])} @ {escape(base.get('commit', '—'))} "
        f"({escape(base.get('tree_state', '—'))}; changed: {escape(changed)}) · run {escape(as_of.get('run_ref', '—'))} "
        f"· expires {escape(doc.get('expires_at', '—'))} · digest {shown} "
        f"· signature {escape(integ.get('signature', {}).get('uri', '—'))}  ",
        f"Supersedes: {sup}  ",
        status,
    ]


def _constraint(con: object) -> str:
    if isinstance(con, dict):
        src = con["source"]
        return f"- {escape(con['text'])} (source: {escape(src['uri'])} {escape(src.get('digest', ''))})".rstrip()
    return f"- {escape(con)}"


def _action(a: JsonDoc) -> str:
    deps = ", ".join(escape(d) for d in a.get("depends_on", [])) or "—"
    return (
        f"- ({escape(a['id'])}) {escape(a['action'])} — owner {escape(a.get('owner', '—'))}; "
        f"done when {escape(a.get('done_when', '—'))}; depends on {deps}"
    )


def _evidence(ev: JsonDoc) -> str:
    raw = ev.get("raw", {})
    parts = [ev["procedure"], ev["scope"], ev["result"], ev["at"], ev.get("producer", "—"), raw.get("uri", "—")]
    return "; ".join(escape(p) for p in parts)


def _state(claims: list[JsonDoc]) -> list[str]:
    rows = ["| id | label | claim | basis or evidence |", "|---|---|---|---|"]
    for c in claims:
        support = " · ".join(_evidence(e) for e in c.get("evidence", [])) or escape(c.get("basis", "—"))
        rows.append(f"| {escape(c['id'])} | {escape(c['label'])} | {escape(c['text'])} | {support} |")
    return rows


def _sections(doc: JsonDoc) -> list[tuple[str, list[str]]]:
    obj = doc["objective"]
    objective = [f"- Goal: {escape(obj['goal'])}"]
    objective += [f"- Intent: {escape(obj['intent'])}"] if "intent" in obj else []
    objective += [f"- Done when: {escape(d)}" for d in obj["done_when"]]
    cons = doc.get("constraints")
    risks = doc["risks"]
    actions = doc["next_actions"]
    readback = doc.get("readback")
    return [
        ("Objective", objective),
        ("Constraints", [_none_known(cons)] if isinstance(cons, dict) else [_constraint(c) for c in cons or []]),
        (
            "Risks",
            [_none_known(risks)]
            if isinstance(risks, dict)
            else [
                f"- [{escape(r['severity'])}] ({escape(r['id'])}) {escape(r['text'])}"
                + (f" — {escape(r['mitigation'])}" if "mitigation" in r else "")
                for r in risks
            ],
        ),
        ("First action", [_action(actions[0])]),
        (
            "Read first",
            [
                f"{i}. {escape(p['uri'])} ({escape(p.get('kind', '—'))}) — {escape(p['why'])}"
                + (f" [{escape(p['digest'])}]" if "digest" in p else "")
                for i, p in enumerate(doc["next_read"], 1)
            ],
        ),
        ("State", _state(doc["claims"])),
        ("Not done", _bullets(doc["not_done"])),
        ("Unknowns", _bullets(doc["unknowns"])),
        ("Contingencies", [f"- If {escape(c['if'])} then {escape(c['then'])}" for c in doc.get("contingencies", [])]),
        (
            "Decisions",
            [
                f"- {escape(d['decision'])} — {escape(d.get('rationale', '—'))}"
                + (f" (rejected: {', '.join(escape(r) for r in d['rejected'])})" if d.get("rejected") else "")
                for d in doc.get("decisions", [])
            ],
        ),
        ("Remaining actions", [_action(a) for a in actions[1:]]),
        (
            "Read-back requested",
            [
                f"required {'yes' if readback.get('required') else 'no'} · reverify "
                f"{', '.join(escape(c) for c in readback.get('reverify', [])) or '—'}"
                + (f" · verifier {_principal(readback['verifier'])}" if readback.get("verifier") else "")
            ]
            if readback
            else [],
        ),
        (
            "Extensions",
            [
                f"- {escape(k)}: {escape(json.dumps(v, ensure_ascii=False, sort_keys=True))}"
                for k, v in doc.get("extensions", {}).items()
            ],
        ),
    ]


def render_markdown(doc: JsonDoc) -> str:
    """Render a valid handoff record in the §13 section order. Callers validate first."""
    lines = _header(doc)
    for title, body in _sections(doc):
        if body:
            lines += ["", f"## {title}", *body]
    return "\n".join(lines) + "\n"
