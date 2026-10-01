"""Write-time checks on factory checkpoints: receipt resolution (FACTORY-READY-RECEIPT-RESOLVE) and payload shape
(FACTORY-START-VALIDATE).

Payload shape: a ``factory:1`` message must carry a known kind, an ``attempt``, satisfy the reader's own
:func:`_schema_error`, and hold no key the kind does not define. W1 recorded STARTs with ``"subject"`` instead of
``"attempt"``; the tool accepted them and they surfaced only as a negative start_to_ready that excluded three
attempts. The journal is append-only, so the refusal is at the write, and only for NEW writes: the reader's
classification of history is untouched.

Receipt resolution:

A READY or USED that names a receipt id with no receipt file behind it (a placeholder such as
``build-PENDING`` recorded before ``trw_build_check`` returned) is worthless evidence, and the journal
is append-only: the first READY per attempt wins, so the slip could only be repaired with a new
attempt id. ``trw_checkpoint`` therefore refuses it BEFORE the write.

Only whether the receipt FILE resolves is decided here, with the same :func:`_resolve` the reader
(:mod:`trw_mcp.state._factory_status`) uses. Report-time judgments stay report-time: a receipt bound
to another sha (``build_sha_mismatch``), an unbound one, or one the verifier flags
``binding_unverifiable`` is present and parseable, so it is accepted here and reported there.
A payload that fails the reader's schema is left to the reader, which diagnoses it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from trw_mcp._refusal_echo import key_name
from trw_mcp.bootstrap._utils import printable
from trw_mcp.state._factory_status import _ALL_KINDS, CELL_KEYS, _load, _resolve, _schema_error

__all__ = ["STATUS_HINT", "factory_payload_refusal", "lifecycle_refusal", "unresolved_receipts"]

#: Every factory refusal ends with this: trw_status does not show attempts, the reader does (E2E-INC-095).
STATUS_HINT = "This run's attempts: `trw-mcp factory status --run <run_path>`."
#: admit.py refuses anything else later, but the journal is append-only, so the bad value would stay (E2E-INC-093).
_SHA = re.compile(r"[0-9a-f]{40}")

#: Keys every factory payload carries; the per-kind extras are what real workers and the verifier write.
#: Longest factory checkpoint message accepted, in characters.
MAX_PAYLOAD_CHARS = 8192
_BASE_KEYS = frozenset({"factory", "kind", "attempt"})
_KIND_KEYS: dict[str, frozenset[str]] = {
    "START": frozenset({"subject_sha", "branch", "base", "note", *CELL_KEYS}),
    "READY": frozenset({"subject_sha", "receipts", "note"}),
    "USED": frozenset({"receipts", "by", "note"}),
    "VOID": frozenset({"reason", "by", "note"}),
}


def _shown(text: object) -> str:
    """A caller-chosen name (an attempt id, a key) as a refusal may repeat it: identifier-shaped and clean, or withheld."""
    return key_name(
        printable(str(text))
    )  # no pre-truncation: a long value whose first 40 characters look clean is still withheld


def factory_payload_refusal(message: str) -> str | None:
    """Why a ``factory:1`` message must not be recorded, naming the key at fault; ``None`` when it may be."""
    if len(message) > MAX_PAYLOAD_CHARS:
        # A transition carries ids and receipt references, never content; it is written three times (the checkpoint
        # log and two event logs), so an unbounded one also burns its attempt id for good (E2E-INC-125 c).
        return f"payload is {len(message)} characters, over the {MAX_PAYLOAD_CHARS} limit; send ids and receipt references, not content"
    payload = json.loads(message)
    kind = payload.get("kind")
    if kind is None:
        return f"kind is required ({'|'.join(_ALL_KINDS)})"
    if kind not in _ALL_KINDS:
        return f"unknown kind: use one of {', '.join(_ALL_KINDS)}"
    if "attempt" not in payload:
        others = sorted(set(payload) - _BASE_KEYS - _KIND_KEYS[kind])
        hint = f" (found {', '.join(_shown(k) for k in others)} instead)" if others else ""
        return f"missing key: attempt{hint}; without it the {kind} never joins its attempt"
    if (problem := _schema_error(payload)) is not None:
        return problem
    sha = payload.get("subject_sha")
    if "subject_sha" in payload and not (isinstance(sha, str) and _SHA.fullmatch(sha)):
        return "subject_sha must be 40 lowercase hex (the full git sha)"
    if kind == "READY" and "subject_sha" not in payload:
        # Without it the reader can only call the receipts 'unbound': nothing ties the READY to a commit (E2E-INC-125 d).
        return "READY needs subject_sha (the full 40-hex git sha the work is at); without it the receipts bind to no commit"
    unknown = sorted(set(payload) - _BASE_KEYS - _KIND_KEYS[kind])
    if unknown:
        allowed = ", ".join(sorted(_BASE_KEYS | _KIND_KEYS[kind]))
        return f"unknown key(s) for {kind}: {', '.join(_shown(k) for k in unknown)} (allowed: {allowed})"
    return None


def subject_sha_refusal(message: str, project_root: Path) -> str | None:
    """Why a factory message's ``subject_sha`` must not be recorded: it names no commit here (CHECKPOINT-SUBJECT-SHA-VERIFY).

    A hand-extended short sha is 40 hex and passes the format check, but a READY for a commit that does not exist can
    never be verified. Checked only in a git checkout; elsewhere there is nothing to resolve it against.
    """
    import os
    import subprocess

    sha = json.loads(message).get("subject_sha")
    if not isinstance(sha, str) or not (project_root / ".git").exists():
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # no GIT_DIR redirect (codex r1 KI)
    try:
        done = subprocess.run(  # noqa: S603 -- fixed git argv; sha is 40 lowercase hex (checked above)
            ["git", "-C", str(project_root), "cat-file", "-e", f"{sha}^{{commit}}"],  # noqa: S607
            capture_output=True, check=False, timeout=20, env=env,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):  # codex r1 KI: a structured refusal, never an escape
        return f"subject_sha {sha} could not be verified (git did not run); nothing was recorded"
    return None if done.returncode == 0 else f"subject_sha {sha} is not a commit in this repository"


#: The kind each transition follows; the reader counts one without it incomplete, excluded or ignored (E2E-INC-096).
_FOLLOWS = {"READY": "START", "USED": "READY", "VOID": "READY"}


def lifecycle_refusal(run: Path, message: str) -> str | None:
    """Why a valid factory *message* must not join the attempts already journaled under *run*; ``None`` when it may.

    Each kind is recorded once per attempt (the reader keeps the first and excludes a differing second as a
    conflict) and after the kind it follows. Judged with the reader's own :func:`_load`, so write and read agree.
    """
    payload = json.loads(message)
    attempt, kind = payload["attempt"], payload["kind"]
    try:
        seen, _, _ = _load(run / "meta" / "events.jsonl", [], [])
    except FileNotFoundError:  # trw-fail-silent-allow: no journal yet means no attempt yet
        seen = {}
    if (attempt, kind) in seen:
        return (
            f"{kind} already recorded for attempt {_shown(attempt)} (journal line {seen[(attempt, kind)][2]}); "
            "the journal is append-only, so rework takes a new attempt id"
        )
    before = _FOLLOWS.get(kind)
    if before and (attempt, before) not in seen:
        return f"{kind} for attempt {_shown(attempt)} has no {before} before it; record the {before} first"
    return None


_GATED_KINDS = ("READY", "USED")
_PRESENT = frozenset({"referenced", "unbound", "build_sha_mismatch"})


def unresolved_receipts(run: Path, message: str) -> list[str]:
    """``family:id (state)`` for each receipt a READY/USED *message* names that does not resolve under *run*."""
    payload = json.loads(message)
    if payload.get("kind") not in _GATED_KINDS or _schema_error(payload) is not None:
        return []
    run = run.resolve()
    subject = payload.get("subject_sha")
    found: list[str] = []
    for family, refs in payload["receipts"].items():
        for ref in refs:
            label, state = _resolve(run, family, ref, subject)
            if state not in _PRESENT:
                # family:id as the caller named it, each part shown only when clean (E2E-INC-125): an id that is not
                # identifier-shaped or trips the secret detector reads <withheld>, the receipt is still counted.
                found.append(f"{':'.join(key_name(part) for part in str(label).split(':', 1))} ({state})")
    return found
