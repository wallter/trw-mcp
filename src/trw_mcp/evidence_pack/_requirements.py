"""Requirements section of the evidence pack (PRD-CORE-323 FR01).

Soundness scope: proves the listed PRD bytes are the ones in the checkout at
export and that the mappings and Call chain cells were parsed by the same parsers
the validator and ``trw_deliver`` use. It does not prove the requirements are
complete or correct, or that they were in force when the run executed. Every
entry is ``as_of: export_snapshot``.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.evidence_pack._redaction import EntryWriter, JsonValue
from trw_mcp.evidence_pack._wording import AS_OF_EXPORT_SNAPSHOT


def _mappings(frontmatter: dict[str, object]) -> list[object]:
    verification = frontmatter.get("verification")
    if not isinstance(verification, dict):
        return []
    mappings = verification.get("mappings")
    return list(mappings) if isinstance(mappings, list) else []


def _prd_entry(path: Path, project_root: Path, writer: EntryWriter) -> dict[str, JsonValue]:
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_scoring_traceability import _extract_traceability_matrix_rows
    from trw_mcp.state.validation.chain_declarations import declared_chains

    relative = path.relative_to(project_root).as_posix()
    source: dict[str, JsonValue] = {"path": relative}
    try:
        raw = path.read_bytes()
    except OSError:  # trw-fail-silent-allow: an unreadable PRD becomes an unknown entry naming the reason
        return writer.seal(source=source, as_of=AS_OF_EXPORT_SNAPSHOT, fields={}, reason="prd_unreadable")
    text = raw.decode("utf-8", errors="replace")
    frontmatter = parse_frontmatter(text)
    chains = {
        requirement: {"chain": list(declaration.chain), "malformed": declaration.malformed}
        for requirement, declaration in sorted(declared_chains(text).items())
    }
    rows = _extract_traceability_matrix_rows(text)
    fields: dict[str, object] = {
        "prd_id": frontmatter.get("id"),
        "status": frontmatter.get("status"),
        **writer.file_digest(raw),
        "verification_mappings": _mappings(frontmatter),
        "matrix_rows": [{"requirement": fr, "row": rows[fr]} for fr in sorted(rows)],
        "call_chains": chains,
    }
    return writer.seal(source=source, as_of=AS_OF_EXPORT_SNAPSHOT, fields=fields)


def requirements_section(
    prd_scope: list[str],
    run_yaml_source: str,
    project_root: Path,
    writer: EntryWriter,
) -> dict[str, JsonValue]:
    """List every scoped PRD with digest, mappings, matrix rows and Call chain cells.

    Runtime caller: ``_pack.build_pack`` (``trw-mcp run evidence-pack`` -> ``run_run``
    -> ``build_pack`` -> here). Scope entries resolve through the plan-acceptance
    gate's resolver; an entry that does not resolve is listed as ``unknown`` with
    reason ``prd_not_found``, never dropped.
    """
    from trw_mcp.tools._plan_acceptance_gate import _resolve_prd_scope

    resolved, unresolved = _resolve_prd_scope(prd_scope)
    entries: list[JsonValue] = [_prd_entry(path, project_root, writer) for path in resolved]
    entries.extend(
        writer.seal(
            source={"path": run_yaml_source, "field": "prd_scope"},
            as_of=AS_OF_EXPORT_SNAPSHOT,
            fields={"scope_entry": entry},
            reason="prd_not_found",
        )
        for entry in unresolved
    )
    return {"section": "requirements", "scope_size": len(prd_scope), "entries": entries}
