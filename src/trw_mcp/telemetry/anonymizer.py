"""Telemetry anonymization utilities — PRD-CORE-031, hardened under R2-014.

Non-reversible anonymization and secret/PII redaction for text leaving the
box. All functions operate on plain strings and are side-effect-free.

``redact_secrets`` is the SINGLE redaction chokepoint for trw-mcp (R2-014):
before this module was hardened, three independent redactors existed with
different coverage — this file's own ``strip_pii`` (email + one generic
API-key regex) on the telemetry hot path (``telemetry/pipeline.py``,
``state/otel_wrapper.py``, ``clients/llm.py``), a richer credential-pattern
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
import re
from pathlib import Path

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


# ---------------------------------------------------------------------------
# redact_secrets — the credential-pattern set (formerly tools/_feedback_redaction.py)
# ---------------------------------------------------------------------------
# Single chokepoint NFR01 mandates for secret hygiene. Pure functions (no I/O,
# idempotent) so they are trivially unit-testable. Patterns compiled at import
# so redaction stays O(n) over the message body per call.
_LICENSE_KEY_RE = re.compile(r"trw_lic_\S+")
# Classic API-key formats: Stripe secret/publishable keys, AWS access-key ids,
# and the hyphenated ``sk-`` family (OpenAI ``sk-proj-…``, Anthropic
# ``sk-ant-api03-…``).
#
# This pattern used to carry a comment rejecting provider prefixes outright as
# "a per-vendor token zoo", on the rationale that the env-var pattern below
# already catches ``OPENAI_API_KEY=…`` / ``GITHUB_TOKEN=…``. That rationale is
# sound and it is why this list stays short — but it holds only for the
# ASSIGNMENT form. The shapes that actually reach a feedback box carry no
# ``=`` at all: a pasted ``curl -H 'Authorization: Bearer eyJ…'``, an HTTP
# trace, or plain prose ("the token eyJ… was rejected"). The env-var rule
# cannot anchor on any of them, so those pastes leaked in clear text.
#
# The line drawn now is not "every vendor" but UNAMBIGUOUS BY CONSTRUCTION:
# a fixed prefix the vendor publishes for secret scanning, or a structural
# format (below). A prefix that could plausibly occur in prose does not qualify.
#
# ``sk-`` specifically is the near-miss this file already half-covered: it
# matched Stripe's UNDERSCORE ``sk_live_`` while OpenAI's HYPHEN ``sk-proj-``
# walked straight through, which is the single most likely secret in a bug
# report filed against an AI framework.
_API_KEY_RE = re.compile(
    r"(?:sk_(?:live|test)_\S+"
    r"|pk_(?:live|test)_\S+"
    r"|AKIA[0-9A-Z]{16}"
    r"|\bsk-[A-Za-z0-9_-]{16,}"  # OpenAI / Anthropic (hyphen form)
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"  # GitHub PAT / OAuth / server / refresh
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"  # GitHub fine-grained PAT
    r"|\bxox[baprs]-[A-Za-z0-9-]{10,}"  # Slack bot/user/app/refresh/legacy
    r")"
)
# A PEM private-key block. Structural, vendor-neutral, and the highest-severity
# thing a user can paste: the whole block collapses, header and footer included,
# so no base64 body survives. DOTALL because the body spans lines.
_PEM_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
# An ``Authorization:`` header value. The scheme (Bearer/Token/Basic/…) is
# preserved because it is diagnostically useful and is not itself a secret;
# everything after it is redacted whole. This is the form a user pastes when
# reporting a failing API call, and no ``key=value`` rule can reach it. The
# negative lookahead keeps the pass idempotent on its own placeholder.
# The "already redacted" lookahead has to span the OPTIONAL scheme, not just sit
# in front of the value: with the guard on the value alone, a second pass over
# ``Authorization: Bearer <REDACTED:authorization>`` backtracks — the optional
# scheme group gives up ``Bearer``, which then satisfies the value slot — and the
# line grows a second placeholder on every pass. Guarding the whole tail keeps
# redaction idempotent, which the NFR pins as hard as zero-false-negative.
_AUTH_HEADER_RE = re.compile(
    r"(?P<key>Authorization\s*:\s*)"
    r"(?!(?:(?:Bearer|Token|Basic|Digest|ApiKey)\s+)?<REDACTED:)"
    r"(?:(?P<scheme>Bearer|Token|Basic|Digest|ApiKey)\s+)?"
    r"\S+",
    re.IGNORECASE,
)
# A JSON Web Token. Structural, not per-vendor: ``eyJ`` is base64 for ``{"``,
# so a three-segment dotted run starting with it is a JWT header by
# construction and effectively cannot be benign prose. Catches the bare token
# in a narrative error message, which the header rule above does not see.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*")
# A bare ``Bearer``/``Token`` value with no ``Authorization:`` header in front
# of it (e.g. a raw ``curl -H 'Bearer <token>'`` paste, or prose narrating a
# rejected token). The header rule above only fires with the header present;
# this catches the same credential when the header text was trimmed away.
_BARE_BEARER_RE = re.compile(
    r"\b(Bearer|Token)\s+(?:(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{8,}|[A-Za-z0-9._~+/=-]{16,})",
    re.IGNORECASE,
)
# Connection-string credentials: scheme://user:password@host. Redact the
# user:password segment whole (preserve scheme + host for diagnostics). The
# password may contain URL-encoded chars / symbols, so it matches any non-`@`,
# non-`/` run. The username group is ``*`` (not ``+``) so an empty-username
# URL (``postgres://:pw@host``) still collapses. ``host`` is whatever follows
# the ``@``.
_CONN_STR_RE = re.compile(
    r"(?P<scheme>postgres|postgresql|mysql|mongodb|redis"
    r"|amqp|amqps|ldap|ldaps|ftp|sftp|mssql|sqlserver)://[^/\s:@]*:[^/\s@]+@",
    re.IGNORECASE,
)
# Query-string credentials: ``?password=…`` / ``&token=…`` etc. — credentials
# smuggled into a URL query rather than the userinfo segment. Preserve the
# separator + key for diagnostics, redact the value (stops at the next
# ``&``/``#``/whitespace). Runs alongside _CONN_STR_RE in the connection-string
# stage so a URL with BOTH userinfo and query creds is fully scrubbed.
_QUERY_CRED_RE = re.compile(
    r"(?P<lead>[?&])(?P<key>password|passwd|secret|token|api_key)=(?P<val>[^\s&#]+)",
    re.IGNORECASE,
)
# JSON-embedded secrets: "password": "…" / "api_key": "…" etc. Preserve the key
# for diagnostics, redact the value. Case-insensitive on the key; the value is
# any run of non-quote characters (handles empty and multi-word values). The
# key alternation accepts snake_case, kebab-case, AND camelCase variants
# (apiKey/apiToken/clientSecret/refreshToken/authToken) so a camelCase JSON
# secret is not a false-negative. ``client_id`` is deliberately NOT included —
# an id is an identifier, not a secret.
_JSON_SECRET_RE = re.compile(
    r"(?P<key>\"(?:"
    r"password|secret|token|private_key"
    r"|api[_-]?key|api[_-]?token|auth[_-]?token|client[_-]?secret|refresh[_-]?token"
    r"|access_key|access_token"
    # Credential-carrying HTTP headers serialized as JSON (a header dict in a log or payload).
    r"|(?:proxy[_-]?)?authorization|(?:set[_-]?)?cookie"
    r")\")"
    r"(?P<sep>\s*:\s*)"
    r"\"[^\"]*\"",
    re.IGNORECASE,
)
# Sensitive env-var KEY=value tokens. The key may carry a prefix
# (``DB_PASSWORD``, ``OPENAI_API_KEY``, ``GITHUB_TOKEN``): a leading ``\b``
# would never match between two word characters (``_`` is a word char), so a
# prefixed key would silently leak its value. We instead anchor on a non-key
# boundary (start-of-string or a non-``[A-Za-z0-9_]`` char) and allow an
# optional ``WORD_`` prefix segment before the sensitive keyword. The value
# captures an optionally-quoted token so ``PASSWORD="multi word secret"`` is
# redacted whole rather than leaking everything after the first space.
#
# A ``key=value`` shape also describes a URL query credential (``?password=…``)
# and an already-substituted placeholder (``password=<REDACTED:credentials>``).
# Those are handled by the connection-string stage which runs FIRST, so the
# value is a ``<REDACTED:…>`` marker by the time this pass runs. A negative
# lookahead on the value skips an already-redacted token: that (a) preserves
# the query-credential placeholder + its key for diagnostics instead of
# re-collapsing it into ``<REDACTED:env>``, and (b) keeps the whole pass
# idempotent (re-running never re-consumes a placeholder).
_ENV_RE = re.compile(
    r"(?:^|(?<=[^A-Za-z0-9_]))"  # boundary: start, or a non-identifier char
    r"(?:[A-Za-z0-9]*_)*"  # optional prefix segments (DB_, OPENAI_, AWS_SECRET_, ...)
    r"(?:PASSWORD|SECRET|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY)"
    r"(?:[_-]?(?:KEY|TOKEN))?"  # optional KEY/TOKEN suffix (SECRET_KEY, ACCESS_TOKEN)
    r"\s*=\s*"
    r"(?!<REDACTED:)"  # already-redacted value (query cred / 2nd pass): skip
    r"(?:\"[^\"]*\"|'[^']*'|\S+)",  # quoted value (any chars) or bare token
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Strip credentials, tokens, PEM keys and PII from text leaving the box.

    The single redaction chokepoint for trw-mcp (R2-014): telemetry
    (``telemetry/pipeline.py``), OTEL span message bodies
    (``state/otel_wrapper.py``), LLM-client failure previews
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
    redacted = _PEM_KEY_RE.sub("<REDACTED:private_key>", text)
    redacted = _LICENSE_KEY_RE.sub("<REDACTED:license_key>", redacted)
    redacted = _CONN_STR_RE.sub(r"\g<scheme>://<REDACTED:credentials>@", redacted)
    redacted = _QUERY_CRED_RE.sub(r"\g<lead>\g<key>=<REDACTED:credentials>", redacted)
    redacted = _JSON_SECRET_RE.sub(r'\g<key>\g<sep>"<REDACTED:json_secret>"', redacted)
    redacted = _AUTH_HEADER_RE.sub(
        lambda m: (
            m.group("key") + ((m.group("scheme") + " ") if m.group("scheme") else "") + "<REDACTED:authorization>"
        ),
        redacted,
    )
    redacted = _JWT_RE.sub("<REDACTED:jwt>", redacted)
    redacted = _API_KEY_RE.sub("<REDACTED:api_key>", redacted)
    redacted = _BARE_BEARER_RE.sub(r"\1 <REDACTED:bearer>", redacted)
    redacted = _ENV_RE.sub("<REDACTED:env>", redacted)
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
