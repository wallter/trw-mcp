"""PRD-CORE-191 — parse/validate/expiry + ledger for the trw_deliver override.

Belongs to the ``_ceremony_deliver_tool.py`` facade. Extracted as a sibling so
the deliver tool stays under the 350 effective-LOC gate.

Three responsibilities:
- ``parse_acceptable_failure`` — parse ``unverified_reason`` as JSON first, then
  a YAML key:value subset; validate against :class:`AcceptableFailureRecord`;
  enforce expiry. Returns ``(record, None)`` on accept or ``(None, error)`` on
  reject (FR01/FR02/FR05).
- ``write_override_ledger`` — append a structured YAML entry under
  ``.trw/overrides/YYYY-MM-DD-<run-id>-<epoch>-<uuid>.yaml`` (FR03). Fail-open (NFR02).
- ``ledger_run_id`` — derive a filesystem-safe run id from the run path.

Record-reuse posture (codex cross-model review): an ``AcceptableFailureRecord``
is a PER-CALL ATTESTATION, not a reusable capability/token. There is no hard
binding that prevents an agent from pasting the same JSON across multiple
deliveries until ``expiry_iso`` — doing so would break legitimate retry flows
(re-run deliver after fixing one of several gates). The honest mitigation is the
AUDIT TRAIL, not prevention: every accepted override writes a distinct ledger
file recording the ``run_id``, ``run_path``, bypassed ``gate_type``, and a
full-resolution ``timestamp``, so re-use is fully VISIBLE post-hoc. Reviewers
inspect the ``.trw/overrides/`` ledger to detect a record being replayed.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path

import structlog
from pydantic import ValidationError

from trw_mcp.models._acceptable_failure import AcceptableFailureRecord
from trw_mcp.state.persistence import FileStateWriter, _safe_yaml

logger = structlog.get_logger(__name__)

_REQUIRED_FIELDS: tuple[str, ...] = ("failed_command", "residual_risk", "owner", "expiry_iso")

# Copy-pasteable example surfaced in the FR05 deprecation error.
_EXAMPLE_JSON = json.dumps(
    {
        "failed_command": "pytest trw-mcp/tests/ -q",
        "residual_risk": "two flaky integration tests; core logic verified manually",
        "owner": "agent-run-id-or-operator-name",
        "expiry_iso": "2026-06-18",
    }
)

_SCHEMA_REQUIRED_MSG = (
    "acceptable-failure schema required: a plain string is no longer sufficient. "
    f"Provide a structured record with required fields {list(_REQUIRED_FIELDS)} as JSON, e.g. "
    f"{_EXAMPLE_JSON}"
)


def _try_parse_payload(reason: str) -> dict[str, object] | None:
    """Parse ``reason`` as JSON first, then a YAML key:value subset. None on failure."""
    text = reason.strip()
    if not text:
        return None
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return loaded
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        loaded_yaml = _safe_yaml().load(StringIO(text))
        if isinstance(loaded_yaml, dict):
            # ruamel auto-types scalars (an ISO date -> datetime.date, a number ->
            # int). All schema fields are str, so coerce scalar values back to
            # strings; leave non-scalars to fail validation.
            #
            # YAML-null coercion bug (codex cross-model review): a null field
            # (``failed_command: ~`` / ``: null``) used to be ``str(None)`` ->
            # the literal string "None", which slips past ``min_length=1`` and
            # silently blesses a hollow override. DROP None-valued keys instead so
            # the field is absent and Pydantic rejects it as missing — matching
            # how a JSON ``null`` already parses (json.loads keeps real None,
            # which fails validation). Non-scalars (dict/list) pass through
            # unstringified so the schema's str-type rejects them too.
            return {
                str(k): (v if isinstance(v, (dict, list)) else str(v)) for k, v in loaded_yaml.items() if v is not None
            }
    except Exception:  # justified: malformed YAML-subset falls through to the schema-required error
        logger.debug("acceptable_failure_yaml_parse_failed", exc_info=True)
    return None


def parse_acceptable_failure(reason: str) -> tuple[AcceptableFailureRecord | None, str | None]:
    """Parse + validate + expiry-check an ``unverified_reason`` override string.

    Returns ``(record, None)`` when the override is a structurally valid, unexpired
    :class:`AcceptableFailureRecord`; otherwise ``(None, error_message)`` where the
    message names the required fields (FR05) or the expiry (FR02).
    """
    payload = _try_parse_payload(reason)
    if payload is None:
        return None, _SCHEMA_REQUIRED_MSG

    try:
        record = AcceptableFailureRecord.model_validate(payload)
    except ValidationError as exc:
        missing = sorted({str(err["loc"][0]) for err in exc.errors() if err.get("loc")})
        detail = f" Missing/invalid fields: {missing}." if missing else ""
        return None, _SCHEMA_REQUIRED_MSG + detail

    expiry_error = _expiry_error(record.expiry_iso)
    if expiry_error is not None:
        return None, expiry_error
    return record, None


def _expiry_error(expiry_iso: str) -> str | None:
    """Return an error when ``expiry_iso`` is in the past (FR02); None otherwise.

    A same-day expiry is NOT expired (the override is valid through its expiry
    date). An unparseable date is treated as invalid (rejected) so a malformed
    expiry cannot silently bypass the gate.
    """
    try:
        expiry = date.fromisoformat(expiry_iso)
    except ValueError:
        return (
            f"acceptable-failure override expired: expiry_iso '{expiry_iso}' is not a valid "
            "ISO-8601 date (YYYY-MM-DD). Provide a new record with a valid future expiry."
        )
    today = datetime.now(timezone.utc).date()
    if expiry < today:
        return (
            f"acceptable-failure override expired: expiry_iso '{expiry_iso}' is before today "
            f"({today.isoformat()}). A new record with a future expiry is required."
        )
    return None


def ledger_run_id(run_path: Path | None) -> str:
    """Derive a filesystem-safe run id for the ledger filename."""
    if run_path is None:
        return "no-run"
    name = run_path.name.strip() or "no-run"
    return "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in name)


def apply_structured_override(
    *,
    results: dict[str, object],
    resolved_run: Path | None,
    trw_dir: Path,
    unverified_reason: str,
    gate_type: str,
) -> tuple[bool, str | None]:
    """Validate a hard-block override; on accept record + ledger it (FR01-FR04).

    Returns ``(proceed, error)``:
    - ``(True, None)`` when the reason is a structurally valid, unexpired record.
      Sets ``results['acceptable_failure_record']`` (the parsed dict) and echoes
      the structured record into ``results['truthfulness_gate_bypassed']`` (FR04),
      and appends the override ledger entry (FR03, fail-open).
    - ``(False, error)`` when parsing/expiry fails. Sets
      ``results['acceptable_failure_error']`` (FR05). Caller must block delivery.
    """
    record, error = parse_acceptable_failure(unverified_reason)
    if record is None:
        results["acceptable_failure_error"] = error
        logger.warning("acceptable_failure_override_rejected", gate_type=gate_type, reason=error)
        return False, error

    record_dict = record.model_dump()
    results["acceptable_failure_record"] = record_dict
    # FR04: truthfulness_gate_bypassed now echoes the structured record (compact
    # JSON) rather than an opaque free-text string.
    results["truthfulness_gate_bypassed"] = json.dumps(record_dict)
    write_override_ledger(
        trw_dir,
        ledger_run_id(resolved_run),
        record,
        gate_type=gate_type,
        run_path=str(resolved_run) if resolved_run else "",
    )
    logger.warning(
        "acceptable_failure_override_accepted",
        gate_type=gate_type,
        owner=record.owner,
        expiry_iso=record.expiry_iso,
        run=str(resolved_run),
    )
    return True, None


def write_override_ledger(
    trw_dir: Path,
    run_id: str,
    record: AcceptableFailureRecord,
    *,
    gate_type: str,
    run_path: str,
) -> None:
    """Append a structured override ledger entry (FR03). Fail-open (NFR02).

    Writes ``.trw/overrides/YYYY-MM-DD-<run-id>-<epoch>-<uuid>.yaml`` with the
    four schema fields, the bypassed gate type, the run path + run id, and a UTC
    timestamp. The epoch-second + uuid4-hex suffix makes the filename unique per
    override so two overrides on the same SECOND for the same run-id no longer
    clobber each other (FileStateWriter.write_yaml is overwrite, not append) —
    the PRD-CORE-191-FR03 audit trail keeps every record. The ``run_id`` field is
    also written into the body so the record's run binding is readable without
    parsing the filename (codex cross-model review — record-reuse auditability).
    Any I/O error is logged at WARNING and swallowed — delivery is never blocked.
    """
    try:
        now = datetime.now(timezone.utc)
        overrides_dir = trw_dir / "overrides"
        # Epoch-second + short uuid4 suffix uniquifies same-second same-run
        # overrides (codex cross-model review). int(epoch) alone collides for two
        # writes within the SAME second; the uuid4 hex makes a within-second
        # collision practically impossible so no override record is silently lost.
        unique = uuid.uuid4().hex[:8]
        ledger_path = overrides_dir / f"{now.date().isoformat()}-{run_id}-{int(now.timestamp())}-{unique}.yaml"
        data: dict[str, object] = {
            "failed_command": record.failed_command,
            "residual_risk": record.residual_risk,
            "owner": record.owner,
            "expiry_iso": record.expiry_iso,
            "gate_type": gate_type,
            "run_id": run_id,
            "run_path": run_path,
            "timestamp": now.isoformat(),
        }
        FileStateWriter().write_yaml(ledger_path, data)
        logger.info("acceptable_failure_ledger_written", path=str(ledger_path), gate_type=gate_type)
    except Exception:  # justified: fail-open (NFR02) — ledger write must not block delivery
        logger.warning("acceptable_failure_ledger_write_failed", gate_type=gate_type, exc_info=True)
