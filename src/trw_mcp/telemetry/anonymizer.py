"""Telemetry anonymization utilities — PRD-CORE-031, hardened under R2-014.

Non-reversible anonymization and secret/PII redaction for text leaving the
box. All functions operate on plain strings and are side-effect-free.

``redact_secrets`` is the SINGLE redaction chokepoint for trw-mcp (R2-014):
before this module was hardened, three independent redactors existed with
different coverage — this file's own ``strip_pii`` (email + one generic
API-key regex) on the telemetry hot path (``telemetry/pipeline.py``,
``clients/llm.py``), a richer credential-pattern
set in ``tools/_feedback_redaction.py`` (PEM keys, JWTs, connection strings,
JSON-embedded secrets, env-var assignments) guarding feedback egress, and a
composite of both plus a bare-bearer regex assembled ad hoc in
``tools/assess.py``. The weakest of the three sat on the highest-volume path.
``redact_secrets`` is now that composite, used everywhere: it applies the
full credential-pattern set, then a bare ``Bearer``/``Token`` catch, then
``trw_memory``'s PII sweep (email/phone/ssn/credit-card/ip), so every caller
gets the strongest coverage regardless of which path they were on before.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from trw_memory.security.credentials import mask_credentials
from trw_memory.security.pii import strip_pii as _strip_generic_pii


def anonymize_installation_id(raw_id: str) -> str:
    """Double SHA-256 hash for non-reversible anonymization.

    Applies two rounds of SHA-256 hashing so that the original value
    cannot be recovered even with rainbow tables of common inputs.
    Returns the first 16 hex characters of the second hash.
    """
    first = hashlib.sha256(raw_id.encode()).hexdigest()
    return hashlib.sha256(first.encode()).hexdigest()[:16]


def redact_paths(text: str, project_root: Path) -> str:
    """Replace absolute project paths with ``<project>/relative/path``.

    Scans *text* for occurrences of the resolved *project_root* string
    and replaces each with the ``<project>`` placeholder so that
    machine-specific filesystem layouts are not transmitted.
    """
    root_str = str(project_root)
    return text.replace(root_str, "<project>")


# The credential patterns live in ``trw_memory.security.credentials`` (the one detector).


def redact_secrets(text: str) -> str:
    """Strip credentials, tokens, PEM keys and PII from text leaving the box.

    The single redaction chokepoint for trw-mcp (R2-014): telemetry
    (``telemetry/pipeline.py``), LLM-client failure previews
    (``clients/llm.py``), outbound feedback submissions
    (``tools/submit_feedback.py``), and ``trw_assess`` state/question
    redaction (``tools/assess.py``) all route through this function before
    anything leaves the box.

    Order matters and is preserved from the strongest of the three prior
    redactors: PEM blocks collapse first (their body is multi-line base64
    that a later pass could partial-match), then license keys, connection
    strings, query-string creds, and JSON-embedded secrets (before the
    generic API-key pass so a token-shaped secret value is not left
    partially matched), then the ``Authorization:`` header, then bare
    JWTs, generic API-key prefixes, a bare ``Bearer``/``Token`` value, and
    ``KEY=value`` env-var assignments. ``$HOME`` is resolved at call time
    (not import time) so tests can override it via ``monkeypatch.setenv``.
    Finally, ``trw_memory``'s PII sweep (email/phone/ssn/credit-card/ip)
    runs last, catching anything the credential passes above did not.

    ``redact_secrets(redact_secrets(x)) == redact_secrets(x)`` — every
    pattern refuses to re-consume a ``<REDACTED:...>`` placeholder.
    """
    if not text:
        return text
    redacted = mask_credentials(text)
    home = os.path.expanduser("~")
    if home and home != "~":
        home_norm = home.rstrip("/")
        if home_norm:
            redacted = redacted.replace(home_norm, "$HOME")
    return _strip_generic_pii(redacted)


def redact_metadata(metadata: dict[str, str] | None) -> dict[str, str] | None:
    """Redact every user-supplied metadata VALUE (and KEY) through :func:`redact_secrets`.

    User-controlled metadata values are an exfil path just like a message
    body, so they get the same chokepoint. BOTH the key AND the value are
    scrubbed: a secret embedded in a metadata KEY name
    (``{"sk_live_abc...": "x"}``) would otherwise leak in clear text.

    Collision note: if two distinct keys redact to the same placeholder they
    collapse into a single dict entry (last write wins). This is acceptable —
    both values are themselves already redacted, so the only loss is a
    low-harm duplicate diagnostic key, never a leaked secret.
    """
    if not metadata:
        return metadata
    return {redact_secrets(k): redact_secrets(v) for k, v in metadata.items()}


__all__ = [
    "anonymize_installation_id",
    "redact_metadata",
    "redact_paths",
    "redact_secrets",
]
