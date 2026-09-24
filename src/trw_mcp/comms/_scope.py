"""Scope addressing: reach whoever DECLARED the ground, not whoever is named (CORE276).

A scope is a repo-relative path. It resolves to recipients by intersecting that
path with each member's manifest-declared ``owned_paths`` — the same declared,
non-overlapping partition the formation already maintains. No new registry, no
subscription state, and no way for a caller to name a peer it could not already
address directly.

The intersection is LEXICAL and touches no filesystem. A declared path need not
exist, and existence was never what conferred ownership; asking the disk would
make resolution depend on whether a peer had created its files yet.

Two deliberate asymmetries:

* A wildcard scope is refused outright rather than bounded afterwards. The
  bound exists for honest scopes; a caller asking for everything is asking for
  the thing this design prevents, and saying no in the parser is cheaper.
* A member's declared glob may itself contain wildcards. That is fine — the
  member declared it, the manifest refuses overlaps between members, and the
  matching below can under-match but never over-match.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from trw_mcp.comms._envelope import MEMBER_ID_PATTERN, AdmissionError
from trw_mcp.formation import declaration_covers

#: Separator for derived per-recipient keys. A control character, because
#: ordinary request keys refuse control characters (FR07), which keeps the
#: derived namespace disjoint from anything a direct caller writes.
SHARD = "\x1f"
_SHARD_KEY = re.compile(rf"^{SHARD}n[0-9a-f]{{32}}{SHARD}[0-9a-f]{{16}}{SHARD}{MEMBER_ID_PATTERN}$")
_WILDCARD = re.compile(r"[*?\[\]]")
_DRIVE = re.compile(r"^[A-Za-z]:")


def is_shard_key(value: str) -> bool:
    """True for a key this module derived, and only for one."""
    return _SHARD_KEY.fullmatch(value) is not None


def has_control_characters(value: str) -> bool:
    """C0 and C1, including tab and newline.

    Deliberately stricter than "printable": a key is an identifier, not prose,
    and a control character in one is either a mistake or an attempt to occupy
    the derived namespace.
    """
    return any(character < "\x20" or "\x7f" <= character <= "\x9f" for character in value)


def parse(raw: str, *, max_bytes: int) -> str:
    """Canonicalize a caller-supplied scope or refuse it. Never guesses."""
    if not isinstance(raw, str) or not raw:
        raise AdmissionError("invalid_scope")
    text = unicodedata.normalize("NFC", raw)
    if has_control_characters(text) or len(text.encode("utf-8")) > max_bytes:
        raise AdmissionError("invalid_scope")
    if _WILDCARD.search(text) or "\\" in text or _DRIVE.match(text) or text.startswith("/"):
        raise AdmissionError("invalid_scope")
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    text = text.rstrip("/")
    segments = text.split("/")
    if not text or any(segment in ("", ".", "..") for segment in segments):
        raise AdmissionError("invalid_scope")
    return text


def intersects(scope: str, declared: str) -> bool:
    """Does a declared ownership glob overlap this scope path?

    Both directions, because a scope can be narrower or wider than what a
    member declared: ``trw-mcp/src/**`` covers the file ``trw-mcp/src/a.py``,
    and the directory scope ``trw-mcp/src`` covers the declaration
    ``trw-mcp/src/a/**``.

    The first direction is NOT re-implemented here. It is exactly the question
    the commit boundary and the pre-edit hook ask — "does this declaration cover
    this path" — and the one definition lives in ``formation``. Writing a second
    one looked equivalent and was not: a bare directory declaration such as
    ``trw-mcp/src/trw_mcp/comms``, which enforcement treats as owning everything
    beneath it, matched no scope at all under a plain ``fnmatch``. The failure
    mode was the dangerous direction — a member silently NOT notified about
    ground it owns.
    """
    pattern = declared.strip()
    if not pattern:
        return False
    return declaration_covers(pattern, scope) or pattern.startswith(f"{scope}/")


def _digest(value: str, size: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


def shard_key(request_key: str, scope: str, member_id: str) -> str:
    """One stable per-recipient key, so the UNIQUE constraint stays the enforcer.

    Derived rather than stored: the same notify retried produces the same keys,
    so idempotency stays a property of the storage engine rather than of
    application timing.

    THE KEY CARRIES THE SCOPE, and that is load-bearing. The prefix is over the
    caller's request key alone, so every shard of one notify is findable
    together; the scope digest sits INSIDE the key, so a retained fan-out can be
    asked what scope produced it without the schema growing a column. An
    external review found the hole this closes: a second call under the same key
    with a DIFFERENT scope that happened to resolve to the same recipients was
    handed back the first call's receipts, reporting success for a scope nothing
    had ever been sent about.
    """
    return f"{SHARD}n{_digest(request_key, 32)}{SHARD}{_digest(scope, 16)}{SHARD}{member_id}"


def shard_prefix(request_key: str) -> str:
    """The prefix every shard of one notify shares, whatever its scope."""
    return f"{SHARD}n{_digest(request_key, 32)}{SHARD}"


def scope_digest_of(key: str) -> str | None:
    """The scope digest a retained shard key was built with, or None."""
    if not is_shard_key(key):
        return None
    return key.split(SHARD)[2]


def scope_digest(scope: str) -> str:
    """The digest a shard key for this scope would carry."""
    return _digest(scope, 16)
