"""PII redaction for outbound feedback submissions (PRD-INFRA-132 FR04a).

Parent facade: ``trw_mcp.tools.submit_feedback``, which re-exports ``_redact_pii``
and ``_redact_metadata`` so existing import paths and any monkeypatching of
``submit_feedback._redact_pii`` keep working unchanged.

Extracted because the pattern set outgrew the 350 effective-LOC ceiling once it
learned the header, JWT and PEM shapes. The concern is a natural deep module: a
pure, import-time-compiled, idempotent function with no I/O and a two-function
interface, which is exactly what contract tests want to sit in front of.

INVARIANTS
  * Pure and idempotent — ``_redact_pii(_redact_pii(x)) == _redact_pii(x)``.
    Every pattern must refuse to re-consume a ``<REDACTED:...>`` placeholder.
  * Order matters: PEM blocks collapse first, connection strings before generic
    API-key matching, the Authorization header before JWT/api-key, so a
    credential is never partially matched by a later, broader rule.
  * Zero false-negative is paired with zero false-positive; every new pattern
    needs a benign mirror in ``tests/test_feedback_redact.py``.
"""

from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# PRD-INFRA-132 FR04a — PII redaction
# ---------------------------------------------------------------------------
# Single chokepoint NFR01 mandates for secret hygiene. Pure function (no I/O,
# idempotent) so it is trivially unit-testable. Patterns compiled at import
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
# `_redact_pii` idempotent, which the NFR pins as hard as zero-false-negative.
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
# Connection-string credentials: scheme://user:password@host. Redact the
# user:password segment whole (preserve scheme + host for diagnostics). The
# password may contain URL-encoded chars / symbols, so it matches any non-`@`,
# non-`/` run. The username group is ``*`` (not ``+``) so an empty-username
# URL (``postgres://:pw@host``) still collapses (finding 1a). ``host`` is
# whatever follows the ``@``.
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
# secret is not a false-negative (finding 1b). ``client_id`` is deliberately
# NOT included — an id is an identifier, not a secret.
_JSON_SECRET_RE = re.compile(
    r"(?P<key>\"(?:"
    r"password|secret|token|private_key"
    r"|api[_-]?key|api[_-]?token|auth[_-]?token|client[_-]?secret|refresh[_-]?token"
    r"|access_key|access_token"
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


def _redact_pii(text: str) -> str:
    """Strip license keys, API keys, tokens, env-var values, and $HOME paths.

    PRD-INFRA-132 FR04a — applied to the submission ``message`` before the
    network call so secrets never leave the box in clear form. ``HOME`` is
    resolved at call time (not import time) so tests can override it via
    ``monkeypatch.setenv``.
    """
    if not text:
        return text
    # PEM blocks first: the body is multi-line base64 that later passes could
    # partial-match, and collapsing it whole removes that surface entirely.
    redacted = _PEM_KEY_RE.sub("<REDACTED:private_key>", text)
    redacted = _LICENSE_KEY_RE.sub("<REDACTED:license_key>", redacted)
    # Connection-string credentials BEFORE generic API-key matching so the
    # user:password segment is collapsed whole and a token-shaped password
    # cannot leak via a partial match.
    redacted = _CONN_STR_RE.sub(r"\g<scheme>://<REDACTED:credentials>@", redacted)
    # Query-string credentials (?password=…) — same connection-string stage so
    # creds smuggled into the query rather than userinfo are collapsed before
    # the generic API-key pass can partial-match a token-shaped value.
    redacted = _QUERY_CRED_RE.sub(r"\g<lead>\g<key>=<REDACTED:credentials>", redacted)
    # JSON-embedded secrets: preserve the key, redact the value.
    redacted = _JSON_SECRET_RE.sub(r'\g<key>\g<sep>"<REDACTED:json_secret>"', redacted)
    # Authorization header BEFORE the JWT/api-key passes so the value collapses
    # once, as a credential, rather than leaving a scheme glued to a placeholder.
    redacted = _AUTH_HEADER_RE.sub(
        lambda m: (
            m.group("key") + ((m.group("scheme") + " ") if m.group("scheme") else "") + "<REDACTED:authorization>"
        ),
        redacted,
    )
    redacted = _JWT_RE.sub("<REDACTED:jwt>", redacted)
    redacted = _API_KEY_RE.sub("<REDACTED:api_key>", redacted)
    redacted = _ENV_RE.sub("<REDACTED:env>", redacted)
    home = os.path.expanduser("~")
    if home and home != "~":
        home_norm = home.rstrip("/")
        if home_norm:
            redacted = redacted.replace(home_norm, "$HOME")
    return redacted


def _redact_metadata(metadata: dict[str, str] | None) -> dict[str, str] | None:
    """Redact every user-supplied metadata VALUE through :func:`_redact_pii`.

    PRD-INFRA-132 NFR01: user-controlled metadata values are an exfil path
    just like the message body, so they get the same chokepoint. BOTH the key
    AND the value are scrubbed (finding 3b): a secret embedded in a metadata
    KEY name (``{"sk_live_abc...": "x"}``) would otherwise leak in clear text.
    Auto-attached metadata (``python_version`` / ``os_platform`` /
    ``trw_mcp_version``) is NOT routed through here — it is generated locally
    and known-safe, and redacting it would risk mangling a benign platform
    string.

    Collision note: if two distinct keys redact to the same placeholder they
    collapse into a single dict entry (last write wins). This is acceptable —
    both values are themselves already redacted, so the only loss is a
    low-harm duplicate diagnostic key, never a leaked secret.
    """
    if not metadata:
        return metadata
    return {_redact_pii(k): _redact_pii(v) for k, v in metadata.items()}
