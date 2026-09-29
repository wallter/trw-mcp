"""``build_pack``: one run directory in, one canonical pack's bytes out (PRD-CORE-323 FR05).

Reads only: run.yaml, events.jsonl and checkpoints.jsonl, the run's receipts and
plans, the scoped PRD files, the run's ``.trw/overrides`` records, the delivery
journal on a read-only connection that never creates it, and ``git`` for the export
HEAD. Never opens the memory store, never starts a probe server, makes no network
call and writes nothing; the CLI writes the returned bytes (NFR03).
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.evidence_pack._manifest import PackRefusedError, build_header, render_pack
from trw_mcp.evidence_pack._redaction import EntryWriter, JsonValue
from trw_mcp.evidence_pack._wording import AS_OF_RUN_RECORD


def _contained_run(run_path: Path, project_root: Path) -> tuple[Path, str]:
    """Resolve *run_path* inside *project_root*, or refuse with a named reason."""
    run = run_path.resolve()
    try:
        relative = run.relative_to(project_root).as_posix()
    except ValueError:
        raise PackRefusedError("run_path_outside_project", str(run_path)) from None
    if not (run / "meta").is_dir():
        raise PackRefusedError("run_path_not_a_run", f"{relative} has no meta directory")
    return run, relative


def _prd_scope(run_yaml: Path) -> list[str] | None:
    """The run's ``prd_scope`` list, or ``None`` when run.yaml cannot be read as a mapping."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.persistence import FileStateReader

    try:
        state = FileStateReader().read_yaml(run_yaml)
    except (StateError, OSError, ValueError):  # trw-fail-silent-allow: reported as an unknown requirements section
        return None
    scope = state.get("prd_scope", [])
    return [str(entry) for entry in scope] if isinstance(scope, list) else None


def build_pack(run_path: Path) -> bytes:
    """Assemble the four sections for the run at *run_path* and render the canonical pack.

    Runtime caller: ``_cli.run_evidence_pack`` (``trw-mcp run evidence-pack`` ->
    ``SUBCOMMAND_HANDLERS["run"]`` -> ``tools._run_cli.run_run`` -> here). Raises
    :class:`PackRefusedError` for a run outside the project root, a directory that
    is not a run, or a pack over the size cap. events.jsonl is read and parsed once
    and shared by the decisions and verdict sections (one event source).
    """
    from trw_mcp.evidence_pack._decisions import decisions_section
    from trw_mcp.evidence_pack._evidence import evidence_section
    from trw_mcp.evidence_pack._requirements import requirements_section
    from trw_mcp.evidence_pack._run_log import MAX_EVENT_READ_LINES, read_run_log
    from trw_mcp.evidence_pack._verdict import verdict_section
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.tools._evidence_git import clean_git_sha
    from trw_mcp.tools._plan_acceptance_gate import _resolve_prd_scope

    project_root = resolve_project_root().resolve()
    run, run_source = _contained_run(run_path, project_root)
    writer = EntryWriter(project_root)
    head = clean_git_sha(project_root)
    run_yaml_source = f"{run_source}/meta/run.yaml"
    scope = _prd_scope(run / "meta" / "run.yaml")
    requirements: dict[str, JsonValue]
    if scope is None:
        requirements = {
            "section": "requirements",
            "entries": [
                writer.seal(
                    source={"path": run_yaml_source},
                    as_of=AS_OF_RUN_RECORD,
                    fields={},
                    reason="run_yaml_unreadable",
                )
            ],
        }
    else:
        requirements = requirements_section(scope, run_yaml_source, project_root, writer)
    events = read_run_log(run, run_source, "events.jsonl")
    # NFR02: the read itself is bounded, not just the entries kept — see MAX_EVENT_READ_LINES.
    event_rows = events.rows(MAX_EVENT_READ_LINES)
    checkpoints = read_run_log(run, run_source, "checkpoints.jsonl")
    prd_paths = _resolve_prd_scope(scope)[0] if scope else []
    sections: dict[str, JsonValue] = {
        "requirements": requirements,
        "decisions": decisions_section(events, event_rows, checkpoints, prd_paths, project_root, writer),
        "evidence": evidence_section(run, run_source, project_root, head, writer),
        "verdict": verdict_section(run, run_source, project_root, events, event_rows, writer),
    }
    # The run directory name is source-derived: it passes the chokepoint like every entry field.
    head_commit = writer.identifier(head) if head is not None else None
    return render_pack(build_header(writer.identifier(run_source), head_commit), sections)
