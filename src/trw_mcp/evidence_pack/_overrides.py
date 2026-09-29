"""Acceptable-failure override records for the verdict section (PRD-CORE-323 FR04 part 2).

The deliver gate writes one record per accepted override to
``.trw/overrides/YYYY-MM-DD-<run-id>-<epoch>-<uuid>.yaml``
(``tools/_acceptable_failure_validation.write_override_ledger``), where ``<run-id>`` is
``ledger_run_id`` of the resolved run directory: the sanitized directory name, not the
run.yaml ``run_id``. Two runs in different task directories can share that name, so a
record is bound to this run only when its body ``run_id`` equals ``ledger_run_id(run)``
AND its ``run_path``, made project-relative, equals the run's project-relative path.

Soundness scope: proves which override records naming this run exist at export and
what they recorded. It does not prove the override was justified, nor that no record
was deleted before export. Records are read, never modified (NFR03).
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.evidence_pack._redaction import EntryWriter, JsonValue
from trw_mcp.evidence_pack._wording import AS_OF_RUN_RECORD

#: NFR02: override records kept per run, applied to the name listing before any record is read.
MAX_OVERRIDE_RECORDS = 500
#: FR04: the fields exported from each record.
OVERRIDE_FIELDS: tuple[str, ...] = ("failed_command", "residual_risk", "owner", "expiry_iso", "gate_type", "timestamp")


def names_run(name: str, run_id: str) -> bool:
    """Whether a ledger file name carries *run_id* as a whole hyphen-delimited token.

    Runtime caller: :func:`override_records` (the listing filter). Soundness scope: a
    superset filter only; a name can match a longer run id that contains this one, so
    the body ``run_id`` decides (:func:`_override_entry`).
    """
    return name.endswith(".yaml") and (f"-{run_id}-" in name or name.endswith(f"-{run_id}.yaml"))


def _project_relative(value: object, project_root: Path) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(project_root).as_posix()
    except ValueError:  # trw-fail-silent-allow: a run_path outside the project cannot bind; reported as unverified
        return None


def _override_entry(
    path: Path, source: str, run_id: str, run_source: str, project_root: Path, writer: EntryWriter
) -> dict[str, JsonValue] | None:
    """One sealed record entry, or None when the body names another run."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateReader

    try:
        body = FileStateReader().read_yaml(path)
    except (StateError, OSError, ValueError):  # trw-fail-silent-allow: an unreadable record is an unknown entry
        return writer.seal(source={"path": source}, as_of=AS_OF_RUN_RECORD, fields={}, reason="override_unreadable")
    recorded_run_id = body.get("run_id")
    if isinstance(recorded_run_id, str) and recorded_run_id != run_id:
        return None
    fields: dict[str, object] = {key: body.get(key) for key in OVERRIDE_FIELDS}
    fields["run_id"] = recorded_run_id
    fields["run_path"] = body.get("run_path")
    bound = recorded_run_id == run_id and _project_relative(body.get("run_path"), project_root) == run_source
    reason = None if bound else "override_binding_unverified"
    return writer.seal(source={"path": source}, as_of=AS_OF_RUN_RECORD, fields=fields, reason=reason)


def override_records(
    run: Path, run_source: str, project_root: Path, trw_dir: Path, writer: EntryWriter
) -> dict[str, JsonValue]:
    """List the override records bound to the run, capped, with kept and total counts.

    Runtime caller: ``_verdict.verdict_section`` (``trw-mcp run evidence-pack`` ->
    ``run_run`` -> ``build_pack`` -> ``verdict_section`` -> here). ``total`` counts the
    ledger files whose name carries the run's ledger id; ``other_run_records`` counts
    those whose body names a different run and are therefore not listed. A record whose
    run binding cannot be confirmed is listed ``unknown`` / ``override_binding_unverified``
    and counted in ``unverified_bound`` (never ``confirmed_bound``): PRD-CORE-323 FR04
    requires that an unverified binding never produce a definitive delivery record on its
    own (:func:`trw_mcp.evidence_pack._verdict.delivery_record_status`).
    """
    from trw_mcp.tools._acceptable_failure_validation import ledger_run_id

    run_id = ledger_run_id(run)
    overrides = trw_dir / "overrides"
    try:
        trw_source = trw_dir.relative_to(project_root).as_posix()
    except ValueError:  # trw-fail-silent-allow: a trw_dir outside the project is cited by its conventional name
        trw_source = ".trw"
    try:
        names = sorted(entry.name for entry in overrides.iterdir() if names_run(entry.name, run_id))
    except OSError:  # trw-fail-silent-allow: no overrides directory means no override records
        names = []
    kept = names[:MAX_OVERRIDE_RECORDS]
    entries: list[JsonValue] = []
    other_run = 0
    confirmed = 0
    for name in kept:
        entry = _override_entry(
            overrides / name, f"{trw_source}/overrides/{name}", run_id, run_source, project_root, writer
        )
        if entry is None:
            other_run += 1
            continue
        entries.append(entry)
        if entry.get("reason") is None:
            confirmed += 1
    return {
        "total": len(names),
        "kept": len(kept),
        "other_run_records": other_run,
        "confirmed_bound": confirmed,
        "entries": entries,
    }
