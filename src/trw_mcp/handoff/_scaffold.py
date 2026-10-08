"""Draft AHR 1.0-rc.2 handoff records: the mechanics filled in, the judgement left as a sentinel.

Belongs to the :mod:`trw_mcp.handoff` package (``trw-mcp handoff new``). The tool owns what an
agent should never hand-compute: the id, UTC timestamps, git state, sender identity, the
recipient and raw-bytes pointer digests (R-INT-6). Every judgement field, including the enum
choices a default would silently decide (a claim's ``label``, a risk's ``severity``), holds the
``TODO(handoff):`` sentinel, which ``validate`` reports as ``placeholder`` until the agent
replaces it, so an unfilled draft can never be sealed.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trw_mcp.handoff._repo import GitState, file_uri, raw_digest
from trw_mcp.handoff._validate import PLACEHOLDER as _T

__all__ = ["TIERS", "Draft", "build_draft", "harness", "new_handoff_id", "sender_id", "utc"]

TIERS = ("minimal", "standard", "critical")
EXPIRY = timedelta(days=7)  # PROPOSAL default (R2 §2): no measured value exists (R-TIME-3)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_PASS_THROUGH = {"https": "url", "trw": None}  # non-file pointers are carried, never fetched
_OPERATOR: dict[str, Any] = {"id": "operator", "kind": "human"}
# Schema order, so the draft reads top to bottom the way the receiver will.
_ORDER = (
    "ahr", "type", "handoff_id", "subject", "tier", "tier_reason", "from", "to", "created_at", "expires_at",
    "as_of", "objective", "constraints", "claims", "not_done", "risks", "unknowns", "contingencies",
    "next_actions", "next_read", "readback",
)  # fmt: skip


@dataclass(frozen=True)
class Draft:
    """The draft record plus the ``changed_paths`` sidecar to write beside it (dirty tree only)."""

    doc: dict[str, Any]
    sidecar: tuple[Path, str] | None


def utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_handoff_id(now: datetime) -> str:
    return f"ho-{now.astimezone(UTC):%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def _harness_session(env: dict[str, str] | os._Environ[str]) -> tuple[str, str]:
    if env.get("CLAUDE_CODE_SESSION_ID") or env.get("CLAUDECODE"):
        return "claude-code", env.get("CLAUDE_CODE_SESSION_ID", "")
    if env.get("CODEX_THREAD_ID") or env.get("CODEX_CLI_VERSION") or env.get("CODEX_SANDBOX_TYPE"):
        return "codex", env.get("CODEX_THREAD_ID", "")
    return "agent", ""


def harness(environ: dict[str, str] | None = None) -> str:
    """The harness this process runs under (``claude-code``, ``codex``), else ``agent``."""
    return _harness_session(os.environ if environ is None else environ)[0]


def sender_id(environ: dict[str, str] | None = None) -> str:
    """``<harness>:<session>`` (R-ROLE-1: two sessions never share an id); random when the harness is silent."""
    name, session = _harness_session(os.environ if environ is None else environ)
    candidate = f"{name}:{_UNSAFE.sub('', session)[:100]}"
    return candidate if session and _ID_RE.match(candidate) else f"{name}:{secrets.token_hex(6)}"


def _pointer(spec: str, root: Path) -> dict[str, Any]:
    """One ``next_read`` entry. Paths (plain, relative to the working directory, or ``file:``, relative to
    ``root``) must resolve inside ``root``: a record never carries an absolute local path."""
    scheme = spec.partition(":")[0].lower()
    if scheme in _PASS_THROUGH:
        kind = _PASS_THROUGH[scheme]
        return {"uri": spec, "why": f"{_T} why the receiver reads this"} | ({"kind": kind} if kind else {})
    path = (root / spec[5:] if scheme == "file" else Path(spec).expanduser()).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"--next-read {spec}: outside the repository root; records carry repo-relative paths only")
    if not path.is_file():
        raise ValueError(f"--next-read {spec}: not a readable regular file")
    return {
        "uri": file_uri(path, root),
        "why": f"{_T} why the receiver reads this, and when",
        "kind": "file",
        "digest": raw_digest(path),  # raw bytes (R-INT-6), never the JCS record digest
    }


def _base_ref(
    state: GitState, root: Path | None, out_path: Path, handoff_id: str
) -> tuple[dict[str, Any], tuple[Path, str] | None]:
    ref: dict[str, Any] = {"commit": state.commit} if state.commit else {}
    if state.branch:
        ref["branch"] = state.branch[:280]
    ref["tree_state"] = state.tree_state
    if not state.changed or root is None:
        return ref, None
    sidecar = out_path.with_name(f"{handoff_id}.changed-paths.txt")
    text = "\n".join(state.changed) + "\n"
    digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()  # raw bytes of the sidecar as written
    ref["changed_paths"] = {"uri": file_uri(sidecar, root), "media_type": "text/plain", "digest": digest}
    return ref, (sidecar, text)


def _judgement(tier: str) -> dict[str, Any]:
    critical = tier == "critical"
    action: dict[str, Any] = {
        "id": "a1",
        "action": f"{_T} the first thing the receiver does",
        "done_when": f"{_T} the check that shows it is done",
    }
    # Both shapes are drafted so no agent has to guess the evidence object (the commonest seal failure in the
    # 2026-10-06 skill eval): a `verified` claim keeps `evidence` and drops `basis`; any other label the reverse.
    claim = {
        "id": "c1",
        "text": f"{_T} what is true now",
        "label": f"{_T} verified | observed | inferred | unknown",
        "basis": f"{_T} observed/inferred: what you saw or your premises (delete for verified and unknown)",
        "evidence": [
            {
                "procedure": f"{_T} verified only: the command another agent can re-run (else delete evidence)",
                "scope": f"{_T} what the check covered and what it did not",
                "result": "supports",
                "at": f"{_T} UTC time the check ran, e.g. {utc(datetime.now(UTC))}",
            }
        ],
    }
    fields: dict[str, Any] = {
        "tier_reason": f"{_T} why this tier (name any rollback record relied on)",
        "objective": {
            "goal": f"{_T} one outcome, not a list of steps",
            "done_when": [f"{_T} a check a named command or observation can decide"],
        },
        "claims": [claim],
        "not_done": [f"{_T} what is left, or {{none_known, checked}}"],
        "risks": [{"id": "r1", "text": f"{_T} what could go wrong", "severity": f"{_T} low | medium | high"}],
        "unknowns": [f"{_T} what nobody has checked, or {{none_known, checked}}"],
        "next_actions": [action | ({"depends_on": ["c1"]} if critical else {})],
    }
    if tier != "minimal":
        fields["objective"]["intent"] = f"{_T} why the goal matters and what may change without asking"
        fields["readback"] = {"required": True}
    if critical:
        fields["constraints"] = [f"{_T} operative wording, verbatim and inline (or {{none_known, checked}})"]
        fields["contingencies"] = [{"if": f"{_T} the condition", "then": f"{_T} what the receiver does"}]
        # X-17: the verifier is never the receiver. The receiver is the continuing agent; the operator verifies.
        fields["readback"] |= {"reverify": ["c1"], "verifier": dict(_OPERATOR)}
    return fields


def _recipient(tier: str, to_id: str | None, to_scope: str) -> dict[str, Any]:
    """Critical (and any ``--to-id``) addresses the continuing agent; otherwise the record is unaddressed."""
    if to_id is None and tier != "critical":
        return {"kind": "unaddressed", "scope": to_scope, "completer": dict(_OPERATOR)}
    target = to_id or f"{harness()}:next"
    if not _ID_RE.match(target):
        raise ValueError(f"--to-id {target!r} is not an AHR identifier")
    if target == _OPERATOR["id"]:
        raise ValueError("--to-id operator: the operator is the verifier; address the continuing agent")
    return {"id": target, "kind": "agent"}


def build_draft(
    *,
    handoff_id: str,
    out_path: Path,
    tier: str,
    subject: str,
    next_read: list[str],
    to_scope: str,
    to_id: str | None = None,
    paths: list[str] | None = None,
    root: Path | None,
    git: GitState,
    now: datetime,
) -> Draft:
    """Assemble a draft. ``root`` is the git top-level and ``git`` its state with the handoff dir excluded
    (outside git: ``root`` is ``None`` and the tree state ``unknown``)."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}")
    if not _ID_RE.match(subject):
        raise ValueError(f"--subject {subject!r} is not an AHR identifier ([A-Za-z0-9][A-Za-z0-9._:-]*)")
    base = root or Path.cwd().resolve()
    pointers = [_pointer(spec, base) for spec in next_read] or [
        {"uri": f"{_T} file:<repo-relative path the receiver reads first>", "why": f"{_T} why", "kind": "file"}
    ]
    base_ref, sidecar = _base_ref(git, root, out_path, handoff_id)
    stamp = utc(now)
    to = _recipient(tier, to_id, to_scope)
    doc: dict[str, Any] = {
        "ahr": "1.0-rc.2",
        "type": "handoff",
        "handoff_id": handoff_id,
        "subject": subject,
        "tier": tier,
        "from": {"id": sender_id(), "kind": "agent"},
        "to": to,
        "created_at": stamp,
        "expires_at": utc(now + EXPIRY),
        "as_of": {"at": stamp, "base_ref": base_ref},
        "next_read": pointers,
    }
    doc |= _judgement(tier)
    if paths:  # R-REC-3: what the receiver may change; validate refuses absolute, `..` and control characters
        doc["objective"]["paths"] = list(dict.fromkeys(paths))
    return Draft({key: doc[key] for key in _ORDER if key in doc}, sidecar)
