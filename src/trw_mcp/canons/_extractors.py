"""Deterministic version extractors for managed canon/template bodies.

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

Each extractor reads a version token from an authoring body with a single
stable regex. They are pure and standard-library only (NFR02): the same bytes
always yield the same token, and an unreadable header yields ``None`` (never a
borrowed/plausible value — FR03/FR04/NFR07).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from trw_mcp.canons._models import VersionExtractor

_FRAMEWORK_HEADER_RE = re.compile(r"^(v[0-9]+(?:\.[0-9]+)?_TRW)(?=\s|—|-)")
_AAREF_HEADER_RE = re.compile(r"^\*\*Version\*\*:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", re.MULTILINE)
_TEMPLATE_FOOTER_RE = re.compile(r"\*Template version:\s*([0-9.]+)")


def _framework_header(text: str) -> str | None:
    match = _FRAMEWORK_HEADER_RE.match(text)
    return match.group(1) if match else None


def _aaref_header(text: str) -> str | None:
    match = _AAREF_HEADER_RE.search(text)
    return f"v{match.group(1)}" if match else None


def _template_footer(text: str) -> str | None:
    match = _TEMPLATE_FOOTER_RE.search(text)
    return match.group(1) if match else None


_EXTRACTORS: dict[VersionExtractor, Callable[[str], str | None]] = {
    VersionExtractor.FRAMEWORK_HEADER: _framework_header,
    VersionExtractor.AAREF_HEADER: _aaref_header,
    VersionExtractor.TEMPLATE_FOOTER: _template_footer,
}


def extract_version(extractor: VersionExtractor, text: str) -> str | None:
    """Read the version token an ``extractor`` recognizes, or ``None``.

    Deterministic: no environment or locale influence. A body that does not
    carry the expected header yields ``None`` so callers can treat the absence
    as fail/unknown rather than a substituted value.
    """
    return _EXTRACTORS[extractor](text)


__all__ = ["extract_version"]
