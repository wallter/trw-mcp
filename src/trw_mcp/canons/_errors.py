"""Stable error taxonomy for the canon registry.

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

The registry is fail-closed: every malformed, unsafe, or unknown manifest
condition raises :class:`CanonRegistryError` carrying one stable machine-readable
``code`` (PRD-INFRA-164 FR01/NFR07). Codes never change between releases so
callers, tests, and release gates can branch on them deterministically.

Standard-library only (PRD-INFRA-164 NFR02).
"""

from __future__ import annotations

from enum import Enum


class CanonErrorCode(str, Enum):
    """Closed set of stable registry failure codes.

    Values are the wire/log representation; never rename an existing value.
    """

    UNSUPPORTED_SCHEMA = "unsupported_schema"
    NOT_AN_OBJECT = "not_an_object"
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"
    WRONG_TYPE = "wrong_type"
    EMPTY_VALUE = "empty_value"
    DUPLICATE_ID = "duplicate_id"
    DUPLICATE_PATH_ROLE = "duplicate_path_role"
    ABSOLUTE_PATH = "absolute_path"
    TRAVERSING_PATH = "traversing_path"
    CONTROL_CHARACTER = "control_character"
    UNSUPPORTED_EXTRACTOR = "unsupported_extractor"
    UNSUPPORTED_KIND = "unsupported_kind"
    UNSUPPORTED_ROLE = "unsupported_role"
    UNSUPPORTED_POLICY = "unsupported_policy"
    UNSUPPORTED_USAGE = "unsupported_usage"
    MALFORMED_VALUE = "malformed_value"
    OVERSIZED_INPUT = "oversized_input"
    SOURCE_UNREADABLE = "source_unreadable"


class CanonRegistryError(Exception):
    """Fail-closed registry error with one stable :class:`CanonErrorCode`.

    ``str(err)`` is ``"<code>: <message>"`` and never contains an absolute
    checkout path or secret value (PRD-INFRA-164 NFR04).
    """

    def __init__(self, code: CanonErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


__all__ = ["CanonErrorCode", "CanonRegistryError"]
