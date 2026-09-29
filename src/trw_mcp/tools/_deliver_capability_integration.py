"""PRD-CORE-320-FR08 — trw_deliver verifies the scoped PRDs' declared call chains.

Joins :func:`trw_mcp.state.validation.call_chain.verify_chain` to delivery. For
each PRD in the run's ``prd_scope`` (``meta/run.yaml``), locates the PRD file via
the same resolver the plan-acceptance and safety-critical gates already use
(``trw_mcp.tools._plan_acceptance_gate._resolve_prd_scope``), parses its
traceability "Call chain" column (:func:`declared_chains`), and verifies each
declared chain. Advisory only (PRD-CORE-320 OQ-001): this step never blocks
``trw_deliver``, mirroring the fail-open contract of the other deliver steps in
:mod:`trw_mcp.tools._ceremony_deliver_steps`.

``repo_root`` is the source root of the ``trw_mcp`` package this server is running
(the directory that contains ``trw_mcp/``). A chain must start at a registered
trw-mcp tool (D1), so its symbols live in that package. Using the running package
means the chain is checked against the code that actually serves the tools, which
works the same in this monorepo (editable install) and in an installed project,
where ``<project>/trw-mcp/src`` does not exist.

A ``hook:<stem>`` entry (FR04) also needs this run's real-path evidence:
:func:`_hook_evidence` gathers every ``hook_real_path`` marker from this run's
own ``meta/events.jsonl`` and the checkout-wide ``.trw/context/session-events.jsonl``,
then passes it to :func:`verify_chain` as ``hook_evidence``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import CapabilityIntegrationRow, DeliverResultDict

logger = structlog.get_logger(__name__)


def _source_root() -> Path:
    """The directory containing the running ``trw_mcp`` package (see the module docstring)."""
    import trw_mcp

    return Path(trw_mcp.__file__).resolve().parent.parent


def _isolated_row(fr: str, reason: str) -> CapabilityIntegrationRow:
    return {"fr": fr, "integration": "isolated", "call_sites": [], "first_unverified_hop": "", "reason": reason}


def _written_since(record: dict[str, object], since: datetime) -> bool:
    """Whether *record*'s ``ts`` (``YYYY-MM-DDTHH:MM:SSZ``, as ``append_event`` writes it) is at or after *since*."""
    raw = record.get("ts")
    try:
        written = datetime.strptime(str(raw), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        # trw-fail-silent-allow: an undated or unparsable ts is not provably this run's evidence, so it counts as none
        return False
    return written >= since


def _hook_evidence(resolved_run: Path) -> frozenset[str]:
    """Every hook stem whose FR04 real-path marker (``hook_real_path``) appears in this run's evidence.

    Reads this run's own ``meta/events.jsonl`` (a pinned session's hooks write
    there) plus the checkout-wide ``.trw/context/session-events.jsonl`` (an
    unpinned hook run has no run identity, so it lands there instead — the
    same fallback ``post-tool-event.sh`` already uses). Never raises: an
    unreadable or absent source simply contributes no evidence.
    """
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools._ceremony_runtime_helpers import _compute_run_age_hours

    # The run's own log is run-scoped. The session log spans every session, so a marker there counts
    # only if its ``ts`` is at or after this run's start (worker-3 review); an undated one never does.
    run_start = datetime.now(timezone.utc) - timedelta(hours=_compute_run_age_hours(resolved_run))
    paths: list[tuple[Path, datetime | None]] = [(resolved_run / "meta" / "events.jsonl", None)]
    try:
        paths.append((resolve_trw_dir() / "context" / "session-events.jsonl", run_start))
    except Exception:  # trw-fail-silent-allow: advisory evidence gathering, never blocks the step
        pass

    stems: set[str] = set()
    for path, since in paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            # trw-fail-silent-allow: advisory evidence gathering, never blocks the step
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                # trw-fail-silent-allow: a torn tail line in an append-only log
                continue
            stem = record.get("hook_real_path") if isinstance(record, dict) else None
            if isinstance(stem, str) and stem and (since is None or _written_since(record, since)):
                stems.add(stem)
    return frozenset(stems)


def step_capability_integration(resolved_run: Path, results: DeliverResultDict) -> None:
    """Verify every declared call chain for this run's scoped PRDs (FR08).

    Populates ``results["capability_integration"]`` (one row per declared FR/NFR
    chain) and, when any row is isolated, ``results["integration_isolated_warning"]``
    naming those FR ids. Both keys are absent when the run declares no ``prd_scope``
    or no scoped PRD declares any chain — never an empty list standing in for
    "checked and found nothing" (that distinction is what the ``capability_integration``
    key's presence/absence already carries).
    """
    try:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.state.validation.call_chain import registered_tool_sites, verify_chain
        from trw_mcp.state.validation.chain_declarations import declared_chains
        from trw_mcp.tools._plan_acceptance_gate import _resolve_prd_scope

        run_yaml = resolved_run / "meta" / "run.yaml"
        run_data = FileStateReader().read_yaml(run_yaml) if run_yaml.is_file() else {}
        raw_scope = run_data.get("prd_scope") if isinstance(run_data, dict) else None
        prd_scope = [str(entry) for entry in raw_scope] if isinstance(raw_scope, list) else []
        if not prd_scope:
            return

        prd_files, unresolved = _resolve_prd_scope(prd_scope)
        repo_root = _source_root()
        tools = registered_tool_sites()  # computed once (NFR01): registers every tool on a probe server
        hook_evidence = _hook_evidence(resolved_run)  # FR04: computed once, same reason

        rows: list[CapabilityIntegrationRow] = []
        isolated_frs: list[str] = []
        for entry in unresolved:
            rows.append(_isolated_row(entry, "prd not found"))
            isolated_frs.append(entry)
        for prd_file in prd_files:
            prd_id = prd_file.stem
            try:
                text = prd_file.read_text(encoding="utf-8")
            except OSError:
                rows.append(_isolated_row(prd_id, "prd not found"))
                isolated_frs.append(prd_id)
                continue
            for fr_id, declaration in declared_chains(text).items():
                row_id = f"{prd_id} {fr_id}"
                if declaration.malformed:
                    rows.append(_isolated_row(row_id, "malformed chain"))
                    isolated_frs.append(row_id)
                    continue
                verdict = verify_chain(repo_root, declaration.chain, tools=tools, hook_evidence=hook_evidence)
                rows.append(
                    {
                        "fr": row_id,
                        "integration": verdict.status,
                        "call_sites": list(verdict.call_sites),
                        "first_unverified_hop": verdict.first_unverified_hop,
                        "reason": verdict.reason,
                    }
                )
                if verdict.status == "isolated":
                    isolated_frs.append(row_id)

        if rows:
            results["capability_integration"] = rows
        if isolated_frs:
            results["integration_isolated_warning"] = (
                "Capability integration advisory (PRD-CORE-320): the declared call chain for "
                f"{', '.join(isolated_frs)} did not verify against a real production caller. "
                "Advisory only — this does not block delivery."
            )
    except Exception as exc:  # justified: fail-open — capability integration is advisory only (OQ-001)
        logger.warning("deliver_capability_integration_failed", error=str(exc), exc_info=True)


__all__ = ["step_capability_integration"]
