"""``commit-candidate`` + ``prd-state`` CLI handlers — production callers.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat.

Thin boundary only: argument marshalling, JSON output, exit codes.
``commit-candidate`` (PRD-CORE-219): the workflow lives in
``state/git_commit_workflow.py``. ``prd-state`` (PRD-QUAL-121-FR04): the
WIP-limit-enforcing ledger writer lives in ``state/requirements_registry.py``
— this handler is its production caller for operator PRD activations.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _run_prepare_candidate(args: argparse.Namespace) -> None:
    """Handle ``trw-mcp prepare-candidate`` before owned files are edited."""
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import prepare_candidate_claim

    try:
        manifest = prepare_candidate_claim(
            Path(args.repo_root).resolve(),
            tuple(args.paths),
            Path(args.run_dir),
            transaction_id=args.transaction_id,
        )
    except GitTransactionError as exc:
        print(json.dumps({"error": str(exc), "shared_state": "untouched"}))
        sys.exit(1)
    print(
        json.dumps(
            {
                "transaction_id": manifest.transaction_id,
                "run_id": manifest.run_id,
                "owned_paths": list(manifest.owned_paths),
                "parent_oid": manifest.parent_oid,
                "checked_out_ref": manifest.checked_out_ref,
                "repository_identity": manifest.repository_identity,
                "state": "prepared",
            },
            indent=2,
        )
    )


def _run_commit_candidate(args: argparse.Namespace) -> None:
    """Handle ``trw-mcp commit-candidate`` for a prepared ownership claim."""
    from trw_mcp.state.git_commit_transaction import GitTransactionError
    from trw_mcp.state.git_commit_workflow import run_candidate_commit

    message_file = Path(args.message_file)
    if not message_file.is_file():
        print(json.dumps({"error": f"message file not found: {args.message_file}"}))
        sys.exit(2)

    try:
        result = run_candidate_commit(
            Path(args.repo_root).resolve(),
            message_file.read_text(encoding="utf-8"),
            transaction_id=args.transaction_id,
            require_signature=bool(args.require_signature),
            run_dir=Path(args.run_dir),
        )
    except GitTransactionError as exc:
        print(json.dumps({"error": str(exc), "shared_state": "untouched"}))
        sys.exit(1)
    print(json.dumps(dict(result), indent=2))


def _run_prd_state(args: argparse.Namespace) -> None:
    """Handle ``trw-mcp prd-state`` — WIP-limited PRD execution-state transition.

    The sole production caller of ``RegistryWriter.set_execution_state``
    (PRD-QUAL-121-FR04): an ACTIVE/BLOCKED_EXTERNAL transition is refused with
    the occupied slots when the nested WIP limits are full; a refusal appends
    nothing to the ledger.
    """
    from trw_mcp.models.requirements import ExecutionState
    from trw_mcp.state.requirements_registry import (
        LEDGER_FILENAME,
        ActivationRefusedError,
        RegistryWriter,
        SchedulingLedgerError,
        build_registry,
        persist_registry,
    )

    root = Path(args.project_root).resolve()
    prds_dir = root / args.prds_dir
    registry_dir = root / ".trw" / "registry"
    ledger_path = registry_dir / LEDGER_FILENAME
    try:
        state = ExecutionState(args.state)
    except ValueError:
        allowed = ", ".join(member.value for member in ExecutionState)
        print(json.dumps({"error": f"invalid state {args.state!r}; allowed: {allowed}"}))
        sys.exit(2)
    writer = RegistryWriter(ledger_path)
    try:
        action = writer.set_execution_state(
            args.prd_id,
            state,
            prds_dir=prds_dir,
            authorization_receipt=args.receipt,
            actor=args.actor,
            owner=args.owner,
        )
        registry = build_registry(prds_dir, ledger_path)
        persist_registry(registry, registry_dir)
    except ActivationRefusedError as exc:
        print(json.dumps({"refused": True, "reason": str(exc), "occupied_slots": exc.occupied_slots}))
        sys.exit(1)
    except SchedulingLedgerError as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
    print(
        json.dumps(
            {
                "prd_id": args.prd_id,
                "state": state.value,
                "sequence": action.sequence,
                "registry_status": registry.status,
                "receipt_digest": registry.receipt_digest(),
            },
            indent=2,
        )
    )
