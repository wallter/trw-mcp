"""Telemetry anonymization utilities — PRD-CORE-031.

Non-reversible anonymization and PII redaction for telemetry data.
All functions operate on plain strings and are side-effect-free.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


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


def strip_pii(text: str) -> str:
    """Remove email addresses and API key patterns from text.

    Replaces recognised PII patterns with safe placeholders:

    * Email addresses → ``<email>``
    * Common API key / token patterns → ``<api_key>``
    """
    # Email addresses
    text = re.sub(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "<email>",
        text,
    )
    # API key / token patterns (prefix followed by 20+ alphanumeric chars)
    text = re.sub(
        r"(sk|pk|api|key|token)[-_][a-zA-Z0-9]{20,}",
        "<api_key>",
        text,
        flags=re.IGNORECASE,
    )
    return text
