"""JSONC (JSON-with-comments) parsing for ``opencode.json``.

Belongs to the ``_opencode.py`` facade. Re-exported there for back-compat.

The string-aware comment stripper shared by the pure ``_parse_jsonc`` helper
and the fail-closed, content-free ``_read_existing_opencode_config`` read seam
used by the FR16 smart-merge path in ``_opencode.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts._opencode import OpencodeConfig

logger = structlog.get_logger(__name__)


def _strip_jsonc_comments(content: str) -> str:
    """Strip ``//`` line and ``/* */`` block comments from a JSONC string.

    The string-aware core shared by :func:`_parse_jsonc` (which then parses the
    result) and :func:`_read_existing_opencode_config` (which parses through
    ``json.loads`` so the parsed value is genuinely untyped and its top-level
    shape can be validated). Comment delimiters inside JSON string literals are
    preserved.
    """
    # Remove block comments /* ... */ (including multi-line)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    # Remove line comments // ... (but not inside strings)
    # Simple approach: remove // comments that are on their own segment
    # Uses a regex that skips strings
    result_parts: list[str] = []
    i = 0
    in_string = False
    escape_next = False
    while i < len(content):
        ch = content[i]
        if escape_next:
            result_parts.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            result_parts.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result_parts.append(ch)
            i += 1
            continue
        if not in_string and ch == "/" and i + 1 < len(content) and content[i + 1] == "/":
            # Skip to end of line
            while i < len(content) and content[i] != "\n":
                i += 1
            continue
        result_parts.append(ch)
        i += 1
    return "".join(result_parts)


def _parse_jsonc(content: str) -> OpencodeConfig:
    """Parse JSONC (JSON with comments) by stripping comments.

    Handles // line comments and /* block comments */.
    Returns parsed dict. Raises json.JSONDecodeError on invalid JSON.
    """
    result: OpencodeConfig = json.loads(_strip_jsonc_comments(content))
    return result


def _read_existing_opencode_config(
    path: Path,
    *,
    result: dict[str, list[str]],
) -> OpencodeConfig | None:
    """Read an existing ``opencode.json`` as a JSONC object, or return ``None``.

    The deep seam behind the FR16 smart-merge read. It is JSONC-aware (the
    OpenCode config permits ``//`` and ``/* */`` comments, so it cannot reuse
    the plain-JSON ``read_json_object`` seam) yet shares that seam's
    fail-closed, content-free policy: every malformed-input outcome collapses to
    ``None`` plus a structural diagnostic, never a crash.

    Outcomes (the prior call site read text + parsed inline and caught only
    ``json.JSONDecodeError`` / ``OSError``, so the first two below escaped or
    surfaced raw parser context):

      - **unreadable** — ``OSError`` (permission, race, is-a-directory, ...).
      - **non_utf8** — bytes are not valid UTF-8. ``bytes.decode("utf-8")``
        raises ``UnicodeDecodeError`` (a ``ValueError`` subclass, *not* an
        ``OSError``), which the prior call site let escape and crash bootstrap.
      - **malformed_json** — valid UTF-8 but the JSONC payload will not parse.
      - **non_object** — parses, but the top level is an array or scalar;
        ``merge_opencode_json`` would then ``.get(...)`` on a non-mapping
        and raise ``AttributeError``.

    On every failure a *content-free* reason category
    (``unreadable`` / ``non_utf8`` / ``malformed_json`` / ``non_object``) is
    appended to ``result["errors"]`` against the stable rel-name
    ``opencode.json`` — never an absolute path, the raw bytes, a secret marker,
    the decode offset, or ``str(exc)``. A malformed config that happens to hold
    a token therefore never leaks into the result or logs.

    Returns the parsed mapping on success, else ``None`` (the caller reports the
    recorded error and leaves the user's file untouched).
    """
    rel = path.name  # stable "opencode.json"; never the absolute path

    try:
        raw = path.read_bytes()
    except OSError:
        logger.warning("opencode_json_unreadable", path=str(path), reason="unreadable")
        result["errors"].append(f"Failed to read {rel}: unreadable")
        return None

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("opencode_json_non_utf8", path=str(path), reason="non_utf8")
        result["errors"].append(f"Failed to read {rel}: non_utf8")
        return None

    parsed: object
    try:
        parsed = json.loads(_strip_jsonc_comments(text))
    except json.JSONDecodeError:
        logger.warning("opencode_json_malformed", path=str(path), reason="malformed_json")
        result["errors"].append(f"Failed to read {rel}: malformed_json")
        return None

    if not isinstance(parsed, dict):
        logger.warning(
            "opencode_json_non_object",
            path=str(path),
            reason="non_object",
            json_kind=type(parsed).__name__,
        )
        result["errors"].append(f"Failed to read {rel}: non_object")
        return None

    return cast("OpencodeConfig", parsed)
