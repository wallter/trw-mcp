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


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


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
    return cleaned.strip(), terminal


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
