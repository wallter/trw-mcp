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

from trw_mcp.dispatch._types import DispatchClient

# Matches CSI / SGR ANSI escape sequences (colors, cursor moves) so PTY-wrapped
# output normalizes to plain text.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _last_nonempty_lines(text: str, *, count: int = 3) -> str:
    """Return the trailing up-to-*count* non-empty lines, joined by newline."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    return "\n".join(lines[-count:])


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
    """codex exec → trailing text; if --json was used, parse the last JSON line."""
    cleaned = _strip_ansi(raw)
    # Try JSON-lines (experimental --json): scan for the last parseable object
    # carrying a textual field.
    last_obj: dict[str, object] | None = None
    for line in cleaned.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            last_obj = obj
    if last_obj is not None:
        for key in ("message", "text", "content", "result"):
            val = last_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip(), last_obj
    # Plain-text default: trailing non-empty lines after banner/hook noise.
    return _last_nonempty_lines(cleaned), None


def _normalize_opencode(raw: str) -> tuple[str, dict[str, object] | None]:
    """opencode run --format json → NDJSON events; concat assistant text."""
    cleaned = _strip_ansi(raw)
    parts: list[str] = []
    final: dict[str, object] | None = None
    parsed_any = False
    for line in cleaned.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        parsed_any = True
        final = event
        text = event.get("text")
        role = event.get("role")
        if isinstance(text, str) and text and (role in (None, "assistant")):
            parts.append(text)
    if not parsed_any:
        return cleaned.strip(), None
    joined = "".join(parts).strip()
    return (joined or _last_nonempty_lines(cleaned)), final


def _normalize_agy(raw: str) -> tuple[str, dict[str, object] | None]:
    """agy → strip ANSI, return trailing non-empty text (PTY-friendly)."""
    cleaned = _strip_ansi(raw)
    return _last_nonempty_lines(cleaned, count=5), None


_NORMALIZERS = {
    "claude": _normalize_claude,
    "codex": _normalize_codex,
    "opencode": _normalize_opencode,
    "agy": _normalize_agy,
}


def normalize_output(client: DispatchClient, raw_stdout: str) -> tuple[str, dict[str, object] | None]:
    """Normalize *raw_stdout* for *client* into ``(text, structured)``.

    Always returns; never raises. Falls back to ``raw_stdout.strip()`` /
    ``None`` if a client-specific parser cannot extract a payload.
    """
    normalizer = _NORMALIZERS.get(client)
    if normalizer is None:  # pragma: no cover - guarded by the Literal upstream
        return raw_stdout.strip(), None
    try:
        return normalizer(raw_stdout)
    except Exception:  # justified: normalization must degrade, never raise
        return raw_stdout.strip(), None
