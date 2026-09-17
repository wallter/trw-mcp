"""Per-client output normalization for the dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. ``normalize_output`` turns a
client's raw stdout into ``(text, structured)`` where ``text`` is the final
human-readable answer and ``structured`` is the parsed payload when the client
emitted JSON/NDJSON (else ``None``).

Robustness contract: every per-client parser falls back to ``raw.strip()`` (and
``structured=None``) if parsing fails — a malformed/empty payload must never
raise, only degrade. ANSI escape sequences are stripped (agy / PTY runs).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from trw_mcp.dispatch._client_specs import OutputShape, UnknownClientError, client_spec_for
from trw_mcp.dispatch._types import DispatchClient

# Matches CSI / SGR ANSI escape sequences (colors, cursor moves) so PTY-wrapped
# output normalizes to plain text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


# A pseudo-terminal in cooked mode ECHOES the EOF it receives: with stdin closed
# (PRD-CORE-277-FR01) `script` forwards EOF immediately and the pty prints the
# literal characters ``^D`` followed by backspaces, so the stream starts
# ``^D\x08\x08{...`` and every JSON parser here bails to raw text. Measured on
# Darwin 2026-09-16; a denied agy run then came back as non-empty "text" and
# reported ok=True.
#
# Only a LEADING run is stripped, and the ``^D`` spelling must be followed by AT
# LEAST ONE backspace (``\x08+``, never ``\x08*``): the pty always backspaces
# over its own echo, so requiring it keeps an answer that merely opens with the
# literal text ``^D means EOF`` intact. Bare EOT/backspace control bytes are
# stripped on their own because they are never an answer.
_PTY_EOF_ECHO_RE = re.compile(r"\A(?:\^D\x08+|[\x04\x08])+")


def _strip_pty_echo(text: str) -> str:
    """Remove the pseudo-terminal's echo of the EOF we sent the child."""
    return _PTY_EOF_ECHO_RE.sub("", text)


def _strip_ansi(text: str) -> str:
    return _strip_pty_echo(_ANSI_RE.sub("", text))


def _normalize_claude(raw: str) -> tuple[str, dict[str, object] | None]:
    """claude -p --output-format json → {.result: str}."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return raw.strip(), None
    if isinstance(data, dict):
        result = data.get("result")
        text = result if isinstance(result, str) else raw.strip()
        return text.strip(), data
    return raw.strip(), None


def _normalize_codex(raw: str) -> tuple[str, dict[str, object] | None]:
    """Extract supported legacy envelopes; preserve unknown/new stream schemas."""
    cleaned = _strip_ansi(raw)
    # Treat only a complete object-per-line stream as structured output.
    # A JSON example inside prose must not replace the surrounding findings.
    last_obj: dict[str, object] | None = None
    parts: list[str] = []
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            return cleaned.strip(), None
        try:
            obj = json.loads(line)
        except ValueError:
            return cleaned.strip(), None
        if obj == {"type": "start"}:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "final":
            return cleaned.strip(), None
        answer_keys = set(obj) - {"type"}
        if len(answer_keys) != 1 or not answer_keys <= {"message", "text", "content", "result"}:
            return cleaned.strip(), None
        value = obj[next(iter(answer_keys))]
        if not isinstance(value, str):
            return cleaned.strip(), None
        parts.append(value)
        last_obj = obj
    if parts and any(part.strip() for part in parts):
        return "\n\n".join(parts).strip(), last_obj
    # Without a structured answer boundary, line count cannot distinguish
    # banner noise from findings. Preserve evidence rather than tail-truncating.
    return cleaned.strip(), None


# The envelope names that carry a completed answer, and the payload fields that
# hold its text. Both are tuples of DATA, not a client id: a second CLI emitting
# the same tagged-envelope shape reuses this parser by declaring the shape.
_TERMINAL_ENVELOPE_EVENTS = ("result",)
_ENVELOPE_ANSWER_FIELDS = ("response", "text", "content")


def _normalize_opencode(raw: str) -> tuple[str, dict[str, object] | None]:
    """opencode run --format json → NDJSON events; concat assistant text."""
    cleaned = _strip_ansi(raw)
    parts: list[str] = []
    final: dict[str, object] | None = None
    parsed_any = False
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            return cleaned.strip(), None
        try:
            event = json.loads(line)
        except ValueError:
            return cleaned.strip(), None
        # Unknown metadata may itself contain findings. Do not discard it by
        # treating arbitrary JSON objects as a recognized text-event stream.
        if not isinstance(event, dict) or set(event) not in ({"text"}, {"role", "text"}):
            return cleaned.strip(), None
        parsed_any = True
        final = event
        text = event.get("text")
        role = event.get("role")
        if not isinstance(text, str) or role not in (None, "assistant", "tool"):
            return cleaned.strip(), None
        if isinstance(text, str) and text and (role in (None, "assistant")):
            parts.append(text)
    if not parsed_any:
        return cleaned.strip(), None
    joined = "".join(parts).strip()
    return (joined or cleaned.strip()), final


def _normalize_enveloped_events(raw: str) -> tuple[str, dict[str, object] | None]:
    """Tagged-envelope NDJSON -> the terminal envelope's answer + its payload.

    Each line is ``{"event": <name>, <name>: {...}}``. The stream's answer lives
    in the payload of the LAST envelope naming a terminal event, so intermediate
    deltas are never concatenated -- re-assembling them would double the text
    that the terminal payload already carries whole.

    Degrades like every sibling: any non-JSON line, any untagged line, or a
    stream with no terminal envelope returns the ANSI-cleaned raw text and no
    structured payload, so a shape chosen wrongly costs nothing.

    A RECOGNIZED terminal envelope is the one case that does not degrade to raw
    text. If its answer field is absent, empty, whitespace-only or not a string,
    the answer is ``""`` and only the payload comes back — the stream parsed, so
    its bytes are diagnostics, not the model's reply.
    """

    cleaned = _strip_ansi(raw)
    terminal: dict[str, object] | None = None
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            return cleaned.strip(), None
        try:
            envelope = json.loads(line)
        except ValueError:
            return cleaned.strip(), None
        if not isinstance(envelope, dict):
            return cleaned.strip(), None
        name = envelope.get("event")
        if not isinstance(name, str):
            return cleaned.strip(), None
        if name in _TERMINAL_ENVELOPE_EVENTS:
            payload = envelope.get(name)
            if isinstance(payload, dict):
                terminal = payload
    if terminal is None:
        return cleaned.strip(), None
    for field in _ENVELOPE_ANSWER_FIELDS:
        answer = terminal.get(field)
        if isinstance(answer, str) and answer.strip():
            return answer.strip(), terminal
    # Recognized terminal envelope, no usable answer: the client spoke the
    # protocol and said nothing. Returning the raw NDJSON here would make the
    # diagnostic stream masquerade as the model's answer and flip
    # ``DispatchResult.ok`` to True (a denied-permission agy run has already
    # been recorded as a successful dispatch that way). The empty answer is the
    # truthful one; the payload is still handed back as diagnostics.
    return "", terminal


def _normalize_agy(raw: str) -> tuple[str, dict[str, object] | None]:
    """agy → strip ANSI and preserve the complete text (PTY-friendly)."""
    cleaned = _strip_ansi(raw)
    return cleaned.strip(), None


# Keyed on the OUTPUT SHAPE the registry records for a client, not on the client
# id (PRD-CORE-266-NFR04). A new client declares a shape and reuses a parser; it
# does not add a row here, and this module carries no client-id literal. The
# parser bodies stay functions because parsing genuinely is behaviour, not data
# (PRD-CORE-266 OQ-4) — only the SELECTION became data.
_NORMALIZERS: dict[OutputShape, Callable[[str], tuple[str, dict[str, object] | None]]] = {
    "single_json_object": _normalize_claude,
    "json_lines": _normalize_codex,
    "ndjson_events": _normalize_opencode,
    "enveloped_ndjson_events": _normalize_enveloped_events,
    "trailing_text": _normalize_agy,
}


def normalize_output(client: DispatchClient, raw_stdout: str) -> tuple[str, dict[str, object] | None]:
    """Normalize *raw_stdout* for *client* into ``(text, structured)``.

    Always returns; never raises. Falls back to ANSI-cleaned ``raw_stdout.strip()`` /
    ``None`` if a client-specific parser cannot extract a payload.
    """
    cleaned = _strip_ansi(raw_stdout)
    try:
        shape = client_spec_for(client).output_shape
    except UnknownClientError:  # pragma: no cover - guarded by the Literal upstream
        return cleaned.strip(), None
    normalizer = _NORMALIZERS[shape]
    try:
        return normalizer(cleaned)
    except Exception:  # justified: normalization must degrade, never raise
        return cleaned.strip(), None


# ── Silence classification (PRD-CORE-277-FR04) ───────────────────────────────

# Substrings that mark a credential or policy STOP. Matched case-insensitively
# against the child's STDERR and against NAMED status fields of the structured
# payload — never against the answer text, because a review whose subject is
# authentication would otherwise classify itself as an auth failure.
_STOP_MARKERS: tuple[str, ...] = (
    "401 unauthorized",
    "unauthorized",
    "not logged in",
    "invalid api key",
    "authentication",
    "auth expired",
    "credential",
    "content filter",
    "content_filter",
    "content policy",
    "refused by policy",
    "permission that headless mode cannot prompt for",
)

# Phrases a CLIENT emits to announce that it did not do the work. Unlike the
# markers above these are checked even when the run produced text, because the
# case they exist for is a stream where the "text" IS the diagnostic: a pty has
# one stream, so under --pty the child's stderr arrives inside stdout (measured
# 2026-09-17).
#
# They are consulted ONLY against that merged pty stream, and only when it did
# not parse into a structured payload. The wider version was measured WRONG the
# same day: a codex code-review dispatch of this very diff came back
# ok=False/auth_or_content_stop, because codex echoes the PROMPT to stderr and
# the prompt quoted "no output produced". A diagnostic phrase is no safer than a
# generic one on a channel that carries the caller's own words, which is exactly
# the false positive 3a2634cf4 fixed for the generic set.
_HARD_STOP_MARKERS: tuple[str, ...] = (
    "no output produced",
    "permission that headless mode cannot prompt for",
    "requires approval, but approval policy is never",
)

# Structured fields that carry a client's OWN verdict on the turn. Read by name;
# a blanket walk over structured VALUES is forbidden here because those values
# include the answer (agy's ``response``, claude's ``result``).
_STATUS_FIELDS: tuple[str, ...] = ("status", "subtype", "error", "error_type", "stop_reason", "finish_reason")

# Values of a status field that mean "the turn completed normally". Anything else
# in a status field is treated as a stop.
_OK_STATUS_VALUES: frozenset[str] = frozenset({"success", "ok", "completed", "complete", "done", "stop", "end_turn"})


def _structured_stop(structured: dict[str, object] | None) -> bool:
    """True if the client's own structured payload reports a stop."""
    if not structured:
        return False
    if structured.get("is_error") is True:
        return True
    for field in _STATUS_FIELDS:
        value = structured.get(field)
        if isinstance(value, str) and value.strip() and value.strip().lower() not in _OK_STATUS_VALUES:
            return True
        if isinstance(value, dict) and value:
            return True
    return False


def classify_silence(
    *,
    text: str,
    raw_stderr: str,
    structured: dict[str, object] | None,
    exit_code: int | None,
    timed_out: bool,
    merged_stderr: str = "",
) -> str | None:
    """Name why a run produced no usable answer, or return ``None``.

    Precedence is most-specific-first: a timeout, then a stop the child or its
    transport reported, then a bare non-zero exit, then an empty answer. The
    order matters because an expired codex credential exits 1 AND prints a 401 —
    measured 2026-09-16 — and "auth_or_content_stop" is the actionable half of
    that pair.

    ``merged_stderr`` is the PTY exception and the runner decides when it applies.
    A pseudo-terminal has ONE stream: under ``use_pty`` the child's stderr is
    merged into stdout and ``proc.stderr`` arrives empty, so both rules above
    would inspect nothing — measured 2026-09-17, a stub whose only output was a
    denial on stderr came back through ``script`` as non-empty ``text`` with
    ``ok=True``. The merged stream is therefore searched too, and for that shape
    alone — and only when the stream did NOT parse into a structured payload —
    the hard markers decide: an unparsed pty stream carrying a client's own
    "I did not run" line is diagnostics, whatever its length. A pty run that
    parsed is judged by the ordinary rules.

    KNOWN LIMIT (PRD-CORE-277-FR04): a client that exits 0, writes nothing to
    stderr and returns a prose refusal as its answer is NOT detectable here, and
    deliberately so — the alternative is scanning the answer for policy words,
    which misfires on any review whose subject is authentication. codex is in
    exactly that position today because TRW launches it without a structured
    output flag. Closing it means adopting ``codex exec --json`` and a parser for
    that schema.
    """
    if timed_out:
        return "timed_out"
    if _structured_stop(structured):
        return "auth_or_content_stop"
    produced_answer = exit_code == 0 and bool(text.strip())
    # A stderr marker counts only when the run produced no usable answer. codex
    # echoes the whole PROMPT to stderr, so a prompt that merely mentions
    # "credential" or "authentication" would otherwise turn a complete, exit-0
    # review into a reported auth stop (measured 2026-09-17 on PRD-CORE-278's
    # adversarial-audit dispatch: full text, ok=false).
    if structured is None and any(marker in merged_stderr.lower() for marker in _HARD_STOP_MARKERS):
        return "auth_or_content_stop"
    haystack = f"{raw_stderr}\n{merged_stderr}".lower()
    if not produced_answer and any(marker in haystack for marker in _STOP_MARKERS):
        return "auth_or_content_stop"
    if exit_code != 0:
        return "nonzero_exit"
    if not text.strip():
        return "empty_output"
    return None
