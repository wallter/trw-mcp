"""The shared redacting entry writer every pack section uses (PRD-CORE-323 FR06).

Invariant: every string that reaches the rendered pack bytes -- copied values, the
keys they sit under, source paths and other identifiers taken from file or directory
names, and the entry metadata derived from them -- passes the chokepoint first.

Order is fixed: ``redact_secrets`` then ``redact_paths`` (both in
``telemetry/anonymizer.py``, the single trw-mcp chokepoint), then truncation to
:data:`FIELD_CHAR_LIMIT` characters, then the digest. An entry's ``sha256`` is
taken over its own canonical bytes as they appear in the pack, so no entry ever
carries a digest of text that redaction changed. A file-level digest over raw
bytes is published only when redaction leaves the whole file unchanged; otherwise
it is taken over the redacted text and says so in ``file_digest_basis``.

Key context. Some chokepoint patterns fire only with the key in view
(``"password": "..."``, ``DB_PASSWORD=...``), which a key/value walk would lose.
The writer asks the chokepoint itself whether a key carries secret context by
redacting the key with a probe value in both forms; when the probe disappears,
the whole value under that key becomes the chokepoint's own placeholder. This
reuses the chokepoint's key vocabulary instead of copying it, keeps each value to
one redaction pass, and avoids redacting JSON-escaped text (escaping ``\\n`` hides
the boundary ``_ENV_RE`` anchors on).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

from trw_mcp.evidence_pack._wording import LABEL_OBSERVED, LABEL_REDACTED, LABEL_UNKNOWN
from trw_mcp.models._evidence_core import canonical_json
from trw_mcp.telemetry.anonymizer import redact_paths, redact_secrets

#: NFR02 per-field cap, applied after redaction and before digesting.
FIELD_CHAR_LIMIT = 4096
REDACTOR_NAME = "redact_secrets+redact_paths"

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_PROBE = "trw-evidence-pack-context-probe"
_PLACEHOLDER_RE = re.compile(r"<REDACTED:[A-Za-z_]+>")
_FALLBACK_PLACEHOLDER = "<REDACTED:context>"


class _WalkState:
    """Per-entry accumulator: did redaction change anything, and what was truncated."""

    def __init__(self) -> None:
        self.changed = False
        self.truncated: dict[str, JsonValue] = {}


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


class EntryWriter:
    """Redact, truncate and seal pack entries against one project root.

    Runtime caller: ``_pack.build_pack`` constructs one per export and hands it to
    every section builder (``_requirements``, ``_evidence``).
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        # redact_secrets rewrites $HOME before redact_paths runs, so a project
        # under $HOME reaches redact_paths in its "$HOME/..." form. Redacting both
        # forms keeps the fixed order and still turns every absolute project path
        # into "<project>".
        seen_form = redact_secrets(str(project_root))
        self._root_forms: tuple[Path, ...] = (
            (project_root,) if seen_form == str(project_root) else (Path(seen_form), project_root)
        )
        self._context: dict[str, str | None] = {}

    def redact(self, text: str) -> str:
        """Apply the chokepoint in its fixed order: redact_secrets, then redact_paths.

        Runtime callers: :meth:`_text` (every copied string), :meth:`file_digest`
        and :meth:`identifier`.
        """
        redacted = redact_secrets(text)
        for form in self._root_forms:
            redacted = redact_paths(redacted, form)
        return redacted

    def identifier(self, text: str) -> str:
        """Redact and bound an identifier that is not inside an entry (the header's run identity).

        Runtime caller: ``_pack.build_pack``. Soundness scope: the same chokepoint
        and the same 4,096-character bound as entry fields, nothing more.
        """
        return self.redact(text)[:FIELD_CHAR_LIMIT]

    def file_digest(self, raw: bytes) -> dict[str, JsonValue]:
        """Digest a source file: raw bytes only when redaction leaves it unchanged.

        Runtime callers: ``_requirements`` (PRD files) and ``_evidence`` (receipts).
        Soundness scope: proves the published digest never covers text the
        chokepoint would change; it cannot see secrets in shapes the patterns miss.
        """
        text = raw.decode("utf-8", errors="replace")
        redacted = self.redact(text)
        if redacted == text and text.encode("utf-8") == raw:
            return {"file_sha256": hashlib.sha256(raw).hexdigest(), "file_digest_basis": "raw_bytes"}
        return {
            "file_sha256": hashlib.sha256(redacted.encode("utf-8")).hexdigest(),
            "file_digest_basis": "redacted_text",
        }

    def seal(
        self,
        *,
        source: Mapping[str, object],
        as_of: str,
        fields: Mapping[str, object],
        reason: str | None = None,
    ) -> dict[str, JsonValue]:
        """Build one entry: redact every copied string, key and identifier, truncate, label, then digest.

        Runtime callers: every section builder. ``source`` (paths built from file and
        directory names) and ``fields`` are copied from sources and pass the
        chokepoint with key context; ``as_of``, ``reason`` and the labels are
        exporter-authored. A ``reason`` makes the entry ``unknown``. Soundness
        scope: the ``sha256`` covers exactly the entry's canonical bytes minus the
        ``sha256`` key, after redaction and truncation; secrets in shapes the
        chokepoint does not know survive.
        """
        state = _WalkState()
        walked_source = self._walk_mapping(source, "source", state)
        walked_fields = self._walk_mapping(fields, "", state)
        entry: dict[str, JsonValue] = {"source": walked_source, "as_of": as_of, **walked_fields}
        if reason is not None:
            entry["label"] = LABEL_UNKNOWN
            entry["reason"] = reason
        else:
            entry["label"] = LABEL_REDACTED if state.changed else LABEL_OBSERVED
        if state.changed:
            entry["redactor"] = REDACTOR_NAME
        if state.truncated:
            # The paths are built from redacted keys; redact them once more as metadata.
            entry["truncated"] = {self.redact(path): size for path, size in sorted(state.truncated.items())}
        entry["sha256"] = hashlib.sha256(canonical_json(entry)).hexdigest()
        return entry

    def _context_placeholder(self, key: str) -> str | None:
        """The placeholder the chokepoint puts on any value under *key*, or None when the key has no secret context."""
        if key not in self._context:
            placeholder: str | None = None
            # The probe must not occur in the key, or "probe gone" could never be seen (core323-s1 r2).
            probe = _PROBE
            while probe in key:
                probe += "-x"
            for form in (json.dumps({key: probe}, ensure_ascii=False), f"{key}={probe}"):
                redacted = redact_secrets(form)
                if probe not in redacted:
                    match = _PLACEHOLDER_RE.search(redacted)
                    placeholder = match.group(0) if match else _FALLBACK_PLACEHOLDER
                    break
            self._context[key] = placeholder
        return self._context[key]

    def _text(self, text: str, path: str, state: _WalkState) -> str:
        redacted = self.redact(text)
        state.changed = state.changed or redacted != text
        if len(redacted) > FIELD_CHAR_LIMIT:
            state.truncated[path] = len(redacted)
            redacted = redacted[:FIELD_CHAR_LIMIT]
        return redacted

    def _walk_mapping(
        self, value: Mapping[str, object] | Mapping[object, object], path: str, state: _WalkState
    ) -> dict[str, JsonValue]:
        """Walk a mapping pair by pair, so a value is judged with its key in view (module docstring)."""
        walked: dict[str, JsonValue] = {}
        for raw_key, item in value.items():
            key = self._text(str(raw_key), _join(path, "<key>"), state)
            placeholder = self._context_placeholder(str(raw_key))
            if placeholder is not None and item is not None and not isinstance(item, bool):
                state.changed = True
                walked[key] = placeholder
            else:
                walked[key] = self._walk(item, _join(path, key), state)
        return walked

    def _walk(self, value: object, path: str, state: _WalkState) -> JsonValue:
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, Mapping):
            return self._walk_mapping(value, path, state)
        if isinstance(value, list | tuple):
            return [self._walk(item, f"{path}[{index}]", state) for index, item in enumerate(value)]
        text = value.isoformat() if isinstance(value, datetime | date) else str(value)
        return self._text(text, path, state)
