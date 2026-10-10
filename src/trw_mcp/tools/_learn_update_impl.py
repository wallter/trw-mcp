"""``trw_learn``'s update mode: change an existing learning in place.

Belongs to the ``learning.py`` facade. The tool body resolves its collaborators
from ``learning``'s namespace (``adapter_update``, ``resolve_trw_dir`` ...) and
passes them in, so suites that patch ``trw_mcp.tools.learning.*`` keep seeing
their patch.

PARTIAL-UPDATE SENTINEL: every field defaults to ``None`` and the adapter reads
``None`` as "the caller did not ask me to touch this". Do not "helpfully"
default a missing key to an empty value; that would silently clear data the
caller never mentioned. A blank string (``""`` or whitespace) for ANY text field
(``detail`` and every string in ``metadata``) means unset, i.e. no change: some MCP
clients send ``""`` for unset optionals, so clear-by-``""`` wiped data. Clearing is
explicit only for list fields, via ``[]`` (``tags`` also accepts ``""``). A blank
``summary`` is rejected, never stored.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.tools._learn_arg_bags import LearnUpdateFields
from trw_mcp.tools._learn_assertion_stamp import stamp_assertions
from trw_mcp.tools._learning_module_helpers import (
    _coerce_learn_type,
    _coerce_tags,
    _sync_learning_yaml_backup,
)

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateWriter

__all__ = ["execute_learn_update"]

logger = structlog.get_logger(__name__)


def execute_learn_update(
    *,
    trw_dir: Path,
    config: TRWConfig,
    writer: FileStateWriter,
    adapter_update: Callable[..., dict[str, str]],
    project_root: Callable[[], Path],
    learning_id: str,
    status: str | None,
    summary: str | None,
    detail: str | None,
    impact: float | None,
    tags: list[str] | str | None,
    type: str | None,
    confidence: str | None,
    evidence_level: str | None = None,  # PRD-CORE-312-FR01
    upd: LearnUpdateFields,
) -> dict[str, str]:
    """Apply one partial update; return ``{status, learning_id, changes}`` or ``{error, status}``."""
    # Only the string shape is coerced: an update REPLACES the tag set, so a list
    # with a stray non-string element must still fail validation loudly.
    # A blank string clears (the tool doc: `tags replace; "" or [] clears`), exactly like [].
    if isinstance(tags, str):
        tags = _coerce_tags(tags) or []
    # An empty summary is never a valid stored value (create rejects it too); some MCP
    # clients send "" for an unset optional string, so reject rather than blank the entry.
    if summary is not None and not summary.strip():
        return {
            "status": "rejected",
            "reason": "missing_summary",
            "message": "summary must not be empty; omit it to leave the summary unchanged. Nothing was changed.",
        }
    # INC-085: the same rule as create, in the same shape (status rejected + reason + message), before trw-memory's
    # patch parser would answer it as {"error", "status": "invalid"}.
    from trw_mcp.tools._learning_module_helpers import impact_rejection

    if (rejected := impact_rejection(impact, outcome="nothing was changed")) is not None:
        return cast("dict[str, str]", rejected)
    if detail is not None and not detail.strip():
        detail = None  # blank means unset (see the module docstring), never a clear
    if type is not None:
        type = _coerce_learn_type(type)
    fields: dict[str, object] = {
        "status": status,
        "summary": summary,
        "detail": detail,
        "impact": impact,
        "tags": tags,
        "type": type,
        "confidence": confidence,
        "evidence_level": evidence_level,
        **upd.model_dump(exclude={"reverify_anchors"}, exclude_none=True),
    }
    # PRD-CORE-362 FR02: replacement assertions are stamped with the checkout commit, before the patch is parsed.
    if "assertions" in fields:
        fields["assertions"] = stamp_assertions(cast("list[dict[str, str]]", fields["assertions"]), project_root())
    # Validate before any side effect: a rejected patch must not re-verify anchors.
    # trw-memory owns the patch contract (PRD-CORE-294 FR03); the adapter re-parses it.
    from trw_memory.lifecycle.correction import parse_patch

    patch = parse_patch(fields)
    if isinstance(patch, dict):
        return _patch_rejection(fields)

    # Refresh anchor validity against the current tree BEFORE any other field update.
    if upd.reverify_anchors:
        from trw_mcp.tools._learn_anchors import reverify_entry_anchors

        reverify_entry_anchors(trw_dir, project_root(), learning_id)

    result = adapter_update(trw_dir, learning_id=learning_id, **fields)

    # Dual-write: also update the YAML backup for rollback safety.
    if result.get("status") == "updated":
        logger.info("learn_update_ok", id=learning_id, changes=result.get("changes", ""))
        backup = patch.model_dump(mode="json", exclude={"supersedes", "tags_add"})
        if patch.tags_add:
            # The merged set exists only in the store (the append ran in its transaction).
            from trw_mcp.exceptions import NamespaceEnumerationError
            from trw_mcp.state._memory_update import stored_tags

            try:
                backup["tags"] = stored_tags(trw_dir, learning_id)
            except NamespaceEnumerationError:
                # The store update already succeeded; only the rollback copy's tags go stale.
                logger.warning("learn_update_backup_tags_unavailable", id=learning_id, exc_info=True)
        _sync_learning_yaml_backup(trw_dir, config, writer, learning_id, backup)
    return _as_rejection(result)


#: Store refusals that are a caller-visible "no" rather than a success; reported in create's rejection shape.
_REFUSED_STATUSES = frozenset({"invalid", "not_found", "conflict"})


def _as_rejection(result: dict[str, str]) -> dict[str, str]:
    """A store refusal (``{status: invalid|not_found|conflict, error}``) in create's shape; anything else as is."""
    status = str(result.get("status", ""))
    if status not in _REFUSED_STATUSES:
        return result
    # The store's text can quote a submitted value; it goes through the one secret/PII detector before the client
    # sees it (codex r1 KI: only the parse_patch path was rebuilt value-free).
    from trw_mcp.telemetry.anonymizer import redact_secrets

    rejected = {
        "status": "rejected",
        "reason": str(result.get("reason") or result.get("error_type") or status),
        "message": redact_secrets(str(result.get("error") or status)),
    }
    if result.get("learning_id"):
        rejected["learning_id"] = str(result["learning_id"])
    return rejected


def _patch_rejection(fields: dict[str, object]) -> dict[str, str]:
    """The first invalid patch field as ``{status rejected, reason invalid_<field>, message}`` -- never its value.

    trw-memory's ``parse_patch`` names the refused field WITH its raw input (``Invalid type 'x': ...``); a value the
    caller put in the wrong field must not come back in the transcript (the VALIDATION-ERROR-ECHO class), so the
    message is rebuilt from pydantic's errors without the input.
    """
    from pydantic import ValidationError
    from trw_memory.lifecycle.correction import LearningPatch

    try:
        LearningPatch.model_validate({k: v for k, v in fields.items() if v is not None})
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False, include_context=False)[0]
        field = ".".join(str(part) for part in first["loc"]) or "patch"
        return {
            "status": "rejected",
            "reason": f"invalid_{field.split('.')[0]}",
            "message": f"Invalid {field}: {first['msg']}. Nothing was changed.",
        }
    return {"status": "rejected", "reason": "invalid_update", "message": "The update was refused; nothing was changed."}
