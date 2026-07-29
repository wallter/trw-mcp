"""The ONE strict contract loader shared by every control point (PRD-SEC-013 R10).

``YAML(typ="safe")`` alone rejects duplicate mapping keys but SILENTLY resolves
anchors/aliases/merge keys, so a checker and a runtime that both "use safe YAML"
can still disagree about what a document says. This loader therefore composes the
document first (round-trip composer, which exposes ``Node.anchor``) and fails
closed on any anchor, alias, merge key, or non-core tag BEFORE the safe load runs.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import io
import unicodedata
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError

from trw_mcp.security.intent_contract._models import Contract, MustNotHappenClaim
from trw_mcp.security.intent_contract.paths import read_bytes_nofollow

__all__ = [
    "ContractLoadError",
    "LoadFailureReason",
    "configured_allowed_commands",
    "load_contract",
    "load_contract_bytes",
]

LoadFailureReason = Literal[
    "invalid_utf8",
    "anchor_alias_or_tag",
    "duplicate_key",
    "malformed_yaml",
    "not_a_mapping",
    "schema_invalid",
    "non_nfc",
    "unreadable",
]

_CORE_TAG_PREFIX = "tag:yaml.org,2002:"
_MERGE_TAG = "tag:yaml.org,2002:merge"


class ContractLoadError(Exception):
    """Raised for every malformed/unloadable contract. Always maps to fail-closed."""

    def __init__(self, reason: LoadFailureReason, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason: LoadFailureReason = reason
        self.detail = detail


def _reject_anchors_aliases_tags(text: str) -> None:
    """Fail closed on anchors, aliases, merge keys, or non-core tags."""
    rt = YAML(typ="rt")
    try:
        node = rt.compose(io.StringIO(text))
    except DuplicateKeyError as exc:
        raise ContractLoadError("duplicate_key", str(exc).splitlines()[0]) from exc
    except YAMLError as exc:
        raise ContractLoadError("malformed_yaml", type(exc).__name__) from exc
    if node is None:
        return
    seen: set[int] = set()
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            # A back-reference can only exist through an alias, which is rejected below.
            raise ContractLoadError("anchor_alias_or_tag", "recursive alias")
        seen.add(id(current))
        if getattr(current, "anchor", None):
            raise ContractLoadError("anchor_alias_or_tag", "anchor or alias present")
        tag = str(getattr(current, "tag", ""))
        if tag == _MERGE_TAG:
            raise ContractLoadError("anchor_alias_or_tag", "merge key present")
        if tag and not tag.startswith(_CORE_TAG_PREFIX):
            raise ContractLoadError("anchor_alias_or_tag", "non-core tag present")
        value = getattr(current, "value", None)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, tuple):
                    stack.extend(item)
                else:
                    stack.append(item)


def _require_nfc(claim: MustNotHappenClaim) -> None:
    for label, value in (("claim_id", claim.claim_id), *(("anchor", a) for a in claim.anchors)):
        if unicodedata.normalize("NFC", value) != value:
            raise ContractLoadError("non_nfc", f"{label} is not NFC-normalized")


#: Top-level keys an author might plausibly reach for instead of the real
#: ``must_not_happen`` claims-list key. A document carrying one of these AND
#: no ``must_not_happen``/``semantic_delta.must_not_happen`` almost certainly
#: means "I typed the wrong key", not "this contract has zero claims" — and a
#: silently-empty contract is the worst possible failure direction for this
#: package (silently-no-enforcement).
_NEAR_MISS_CLAIMS_KEYS = ("claims", "must_not_happens", "mustNotHappen")


def _claim_payloads(document: object) -> list[object]:
    if not isinstance(document, dict):
        raise ContractLoadError("not_a_mapping", "contract document must be a mapping")
    raw = document.get("must_not_happen")
    if raw is None:
        delta = document.get("semantic_delta")
        raw = delta.get("must_not_happen") if isinstance(delta, dict) else None
    if raw is None:
        near_miss = next((key for key in _NEAR_MISS_CLAIMS_KEYS if key in document), None)
        if near_miss is not None:
            raise ContractLoadError(
                "schema_invalid",
                f"document has top-level key {near_miss!r} but no 'must_not_happen' "
                "(or 'semantic_delta.must_not_happen') — the claims list key is "
                f"'must_not_happen'; {near_miss!r} is ignored and would silently load as "
                "zero claims (no enforcement). Rename the key, or add an explicit empty "
                "'must_not_happen: []' if this contract genuinely has no claims yet.",
            )
        return []
    if not isinstance(raw, list):
        raise ContractLoadError("schema_invalid", "must_not_happen must be a list")
    return list(raw)


def configured_allowed_commands() -> tuple[str, ...]:
    """The ONE falsifier allowlist — the same typed-config value the runtime executes with.

    Resolving it here (rather than letting each caller pass its own, or none)
    is what stops a commit-time checker from accepting a contract the runtime
    then refuses to load: a committable contract is always a runnable one.
    """
    from trw_mcp.models.config._loader import get_config

    return tuple(get_config().security.intent.falsifier_allowed_commands)


def _require_allowlisted_falsifiers(claim: MustNotHappenClaim, allowed_commands: tuple[str, ...]) -> None:
    """Reject a disallowed falsifier command at LOAD time, not at execution time."""
    for ref in claim.falsifiers:
        command = "pytest" if ref.kind == "pytest" else (ref.argv[0] if ref.argv else "")
        if command not in allowed_commands:
            raise ContractLoadError("schema_invalid", f"falsifier command not allowlisted: {command or '<empty>'}")


def load_contract_bytes(raw: bytes, allowed_commands: tuple[str, ...] | None = None) -> Contract:
    """Parse contract *raw* bytes strictly. Raises :class:`ContractLoadError`.

    ``allowed_commands=None`` resolves the typed-config allowlist via
    :func:`configured_allowed_commands`, so EVERY caller — commit checker and
    runtime hook alike — validates falsifier refs against the same set. Passing
    an explicit tuple is for tests that need a different allowlist.
    """
    commands = configured_allowed_commands() if allowed_commands is None else allowed_commands
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ContractLoadError("invalid_utf8", "contract bytes are not valid UTF-8") from exc

    _reject_anchors_aliases_tags(text)

    safe = YAML(typ="safe")
    safe.allow_duplicate_keys = False
    try:
        document = safe.load(io.StringIO(text))
    except DuplicateKeyError as exc:
        raise ContractLoadError("duplicate_key", str(exc).splitlines()[0]) from exc
    except YAMLError as exc:
        raise ContractLoadError("malformed_yaml", type(exc).__name__) from exc

    if document is None:
        return Contract()

    claims: list[MustNotHappenClaim] = []
    for payload in _claim_payloads(document):
        if not isinstance(payload, dict):
            raise ContractLoadError("schema_invalid", "each claim must be a mapping")
        try:
            claim = MustNotHappenClaim.model_validate(payload)
        except ValidationError as exc:
            raise ContractLoadError("schema_invalid", f"{exc.error_count()} claim field error(s)") from exc
        _require_nfc(claim)
        _require_allowlisted_falsifiers(claim, commands)
        claims.append(claim)

    contract_id = document.get("contract_id", "") if isinstance(document, dict) else ""
    return Contract(contract_id=str(contract_id or ""), claims=tuple(claims))


def load_contract(path: Path, allowed_commands: tuple[str, ...] | None = None) -> Contract | None:
    """Load the contract at *path*; ``None`` when the file is absent (fail-open case)."""
    if not path.exists():
        return None
    try:
        raw = read_bytes_nofollow(path)
    except OSError as exc:
        raise ContractLoadError("unreadable", type(exc).__name__) from exc
    return load_contract_bytes(raw, allowed_commands)
