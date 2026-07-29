"""Shared plumbing for the four intent-contract entry points.

:func:`enrollment_gate` is the preamble ALL of them open with — FR05 pre-write,
FR07 post-edit, FR02 pre-commit and FR01+FR02 pre-push. It answers one question
("is this project opted in, and is its marker current?") and is parameterized on
the caller's block code, because the two families disagree only on that number.

The edit-time pair additionally shares strict contract loading, no-follow
root-confined target resolution and telemetry via :func:`read_hook_input`, so
that boundary lives here too and cannot drift between them.

Exit-code contract: 0 = allow everywhere. Fail-closed is 2 for the edit-time
hooks (NFR01) and 1 for the two pre-commit-stage entry points (pre-commit's own
convention). Once a protected invocation is RECOGNIZED (the project is enrolled),
every unexpected condition maps to the caller's block code; before recognition
the caller's generic fail-open trap applies.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from trw_mcp.models.config._sub_models import IntentContractConfig
from trw_mcp.security.intent_contract._models import Contract
from trw_mcp.security.intent_contract.enrollment import check_enrollment_status, stale_enrollment_warning
from trw_mcp.security.intent_contract.loader import ContractLoadError, load_contract
from trw_mcp.security.intent_contract.paths import classify_target, repo_root
from trw_mcp.security.intent_contract.telemetry import Outcome, record_firing

__all__ = [
    "ALLOW",
    "BLOCK",
    "EnrolledRoot",
    "HookDecision",
    "HookInput",
    "enrollment_gate",
    "intent_config",
    "read_hook_input",
    "telemetry",
]

ALLOW = 0
BLOCK = 2


@dataclass(frozen=True)
class HookDecision:
    """A resolved allow/block with the operator-facing message (stderr on block)."""

    code: int
    message: str = ""


@dataclass(frozen=True)
class EnrolledRoot:
    """A project that IS opted in, with a current marker: the gate's success value."""

    root: Path
    config: IntentContractConfig


@dataclass(frozen=True)
class HookInput:
    """Everything both entry points need after the shared preamble."""

    root: Path
    config: IntentContractConfig
    contract: Contract
    target: Path
    rel_path: str
    #: True when the target is lexically inside the repo but symlink-resolves out
    #: of it. Anchored + escaped is a fail-closed condition for both entry points.
    escaped: bool = False


def intent_config() -> IntentContractConfig:
    from trw_mcp.models.config._loader import get_config

    return get_config().security.intent


def telemetry(root: Path, outcome: Outcome, configured: str | None) -> None:
    """Record a firing; telemetry I/O must never change an allow/block decision."""
    try:
        record_firing(root, outcome, configured)
    except OSError:
        return


def enrollment_gate(block: int) -> EnrolledRoot | HookDecision:
    """Is this project opted in, with a marker that still matches the tree?

    The one preamble all four entry points share. Returns a terminal
    :class:`HookDecision` for the two non-enforcing states — never enrolled, stale
    marker — or the resolved root/config to work against.

    *block* is the caller's fail-closed code (2 for the edit-time hooks, 1 for the
    pre-commit-stage pair); it is the ONLY thing the two families differ on here.
    Inert-by-default is load-bearing in the ``never_enrolled`` branch: a project
    that never opted in must be indistinguishable from one with no contract.

    ``security.intent.enabled`` is deliberately NOT consulted. It used to
    short-circuit here BEFORE enrollment was resolved, which made
    ``security: {intent: {enabled: false}}`` in ``.trw/config.yaml`` a one-line
    total disarm of all four control points — and a WORKING-TREE edit is enough,
    so C9 never sees it and the marker still reads ``current`` (the enrollment
    digests cover the contract, the hooks and the pre-commit registration, not
    config.yaml). Same ruling as the ``HOOKS_ENABLED`` finding: enrollment is the
    opt-in for this control, so the documented off switch for an enrolled project
    is UN-ENROLLMENT — an auditable, committed act — and the non-committing escape
    hatch is an operator-minted break-glass token, which ledgers. The flag lost
    nothing it could still switch off: the only state it governed after this
    reordering is ``never_enrolled``, which is already inert either way.
    """
    config = intent_config()
    root = repo_root()

    status = check_enrollment_status(root, config.contract_path)
    if status == "never_enrolled":
        # Identical to the absent-contract case: nothing has been opted in yet.
        return HookDecision(ALLOW)
    if status == "stale":
        # The warning is unsuppressible and prints even when the block is
        # subsequently overridden by the caller's break-glass path.
        print(stale_enrollment_warning(root, config.contract_path), file=sys.stderr)
        return HookDecision(block, "enrollment marker is stale")
    return EnrolledRoot(root=root, config=config)


def read_hook_input(stream: IO[str] | None = None) -> HookInput | HookDecision:
    """Run the shared preamble. Returns a terminal decision or a resolved input."""
    gate = enrollment_gate(BLOCK)
    if isinstance(gate, HookDecision):
        return gate
    root, config = gate.root, gate.config

    try:
        payload = json.load(stream or sys.stdin)
    except (ValueError, OSError):
        return HookDecision(BLOCK, "hook payload was not readable JSON")
    if not isinstance(payload, dict):
        return HookDecision(BLOCK, "hook payload was not a JSON object")

    try:
        contract = load_contract(root / config.contract_path, tuple(config.falsifier_allowed_commands))
    except ContractLoadError as exc:
        return HookDecision(BLOCK, f"contract is malformed or unloadable ({exc.reason})")
    except OSError as exc:
        return HookDecision(BLOCK, f"contract could not be read ({type(exc).__name__})")
    if contract is None:
        return HookDecision(ALLOW)

    tool_input = payload.get("tool_input")
    raw_path = str(tool_input.get("file_path", "")) if isinstance(tool_input, dict) else ""
    classification, target = classify_target(root, raw_path)
    if target is None:
        # Outside the repo entirely: no claim can anchor it — a positive answer.
        #
        # KNOWN PERMISSIVE FALLBACK, kept deliberately: a payload with no
        # `tool_input.file_path` also lands here, and "we could not tell which
        # file is being written" is NOT evidence that the write is safe. It stays
        # ALLOW because it is not agent-reachable — the payload is built by the
        # client, and the registered matcher (Write|Edit|MultiEdit) always carries
        # file_path — while failing closed on an unrecognized payload shape would
        # block every edit on any client whose payload differs, which is a real
        # cost for a client-agnostic framework. Re-evaluate the moment a non-
        # Claude-Code client registers these hooks. # trw:intentional
        telemetry(root, "allowed_no_match", config.telemetry_path)
        return HookDecision(ALLOW)

    return HookInput(
        root=root,
        config=config,
        contract=contract,
        target=target,
        rel_path=os.path.relpath(str(target), str(root)).replace(os.sep, "/"),
        escaped=classification == "escaped",
    )
