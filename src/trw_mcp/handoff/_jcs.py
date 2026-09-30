"""RFC 8785 (JCS) canonical bytes and the AHR digest (PRD-CORE-347-FR02).

Ported from the AHR 1.0-rc.1 reference checker (``specs/handoff/tools/ahr_check.py``
``jcs``/``digest``). Exact for documents that follow R-INT-3: integers only, within
the I-JSON safe range. Object keys order by UTF-16 code units, as RFC 8785 requires.

Do NOT replace this with ``trw_mcp.models._evidence_core.canonical_json``: that helper
orders keys by code point and adds no RFC 8785 guarantees (learning L-d3zI).
"""

from __future__ import annotations

import hashlib
import json

__all__ = ["JcsError", "digest", "jcs"]

_SAFE_INT = 2**53 - 1


class JcsError(ValueError):
    """The value cannot be canonicalized under R-INT-3/R-INT-4 (rule X-0)."""


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", errors="surrogatepass")


def _string(value: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise JcsError("X-0 lone surrogate (R-INT-4)") from exc
    return json.dumps(value, ensure_ascii=False)


def _encode(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, float):
        raise JcsError("X-0 non-integer number (R-INT-3)")
    if isinstance(value, int):
        if not -_SAFE_INT <= value <= _SAFE_INT:
            raise JcsError("X-0 integer outside the I-JSON safe range (R-INT-3)")
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=_utf16_key)
        return "{" + ",".join(_string(k) + ":" + _encode(value[k]) for k in keys) + "}"
    raise JcsError(f"X-0 value of type {type(value).__name__} is not JSON")


def jcs(value: object) -> bytes:
    """Return the RFC 8785 canonical UTF-8 bytes of ``value``."""
    return _encode(value).encode("utf-8")


def digest(doc: dict[str, object]) -> str:
    """Return ``sha256:<hex>`` over the JCS bytes of ``doc`` without ``integrity`` (R-INT-1)."""
    body = {k: v for k, v in doc.items() if k != "integrity"}
    return "sha256:" + hashlib.sha256(jcs(body)).hexdigest()
