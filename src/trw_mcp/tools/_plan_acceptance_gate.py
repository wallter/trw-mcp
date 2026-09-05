"""PRD-CORE-249-FR04 — the plan-acceptance gate.

Belongs to the ``_deliver_gate_dispatch.py`` cascade, which composes
:func:`evaluate_plan_acceptance` immediately after the PRD-CORE-213
acceptance-integrity gate and shares its PRD-CORE-191 override contract.

**What it closes.** Every gate the deliver cascade ran before this one certifies
the code that was written or the PRD status that was edited. None read the plan.
A run could therefore satisfy the build gate, the review gate, and the
transition gate while its governing acceptance matrix was materially untouched —
"delivered" meaning "the code I chose to write compiles".

**Enumeration is deliberately narrow, in two legs.**

1. ``reports/plan.md`` — ``P-``/``X-``/``AC-`` identifiers at an ANCHORED
   position only: line start, optionally behind a list marker, checkbox, or a
   leading table pipe, and followed by a delimiter. A mid-sentence mention is
   not an identifier. This is the same whole-line discipline as
   ``index_sync._find_marker_line``, which exists because loose substring
   matching once truncated 705 lines of a checked-in document.
2. PRDs named in ``prd_scope`` — ``FR\\d+`` taken only from FR HEADINGS, reusing
   the ``_prd_scoring_fr._FR_HEADING_RE`` shape. A prose ``FR12`` in a PRD body
   is not an identifier.

Under-enumeration therefore degrades toward today's behaviour, never toward a
spurious block.

**The unresolved-scope rule.** A ``prd_scope`` entry that names no resolvable
PRD is reported in ``unresolved_scope_entries``. Zero enumerated identifiers is
never reported as a pass while ``prd_scope`` was non-empty and nothing resolved:
absence of a measurement is not a measurement of absence.

**Fail posture.** Unreadable or malformed ``reports/acceptance.yaml`` is
fail-CLOSED — the one deliberate departure from the surrounding fail-open
convention (NFR02). A declaration that cannot be read is indistinguishable from
one that was never written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import structlog
import yaml

from trw_mcp.models.plan_acceptance import (
    AcceptanceDeclarationError,
    AcceptanceStatus,
    parse_declaration,
)

# Task types that carry a build artifact. Imported rather than redeclared so the
# build gate and this gate cannot drift into two notions of "build-bearing".
from trw_mcp.tools._deliver_gate_mode import _BUILD_ARTIFACT_TASK_TYPES, resolve_gate_mode

logger = structlog.get_logger(__name__)

#: Deliver-gate modes under which an unmet identifier is a hard block.
_BLOCKING_MODES: Final[frozenset[str]] = frozenset({"block_coding", "block_all"})

#: Anchored plan identifier. The optional prefix is a leading table pipe, or a
#: list marker with an optional checkbox; the lookahead demands a delimiter so
#: ``see X-1 for details`` inside a sentence never matches.
_PLAN_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*(?:\|[ \t]*|[-*][ \t]+(?:\[[ xX]\][ \t]+)?)?((?:P|X|AC)-\d+)(?=[ \t]*(?::|\||—|$)|[ \t])",
    re.MULTILINE,
)

#: FR identifier taken from an FR HEADING only (the ``_FR_HEADING_RE`` shape).
_FR_HEADING_ID_RE: Final[re.Pattern[str]] = re.compile(r"^###\s+(?:PRD-[\w-]+-)?(FR\d+)\b", re.MULTILINE)

#: Bound on the bytes read from any single enumerated document. A plan or PRD
#: larger than this is an authoring accident, not a gate input; the regex scan
#: stays linear and bounded rather than unbounded in file size (NFR01).
MAX_ENUMERATION_BYTES: Final[int] = 1024 * 1024


@dataclass(frozen=True)
class PlanAcceptanceOutcome:
    """Result of one gate evaluation. ``should_block`` is the only hard signal."""

    should_block: bool = False
    message: str = ""
    warning: str = ""
    enumerated: tuple[str, ...] = ()
    unresolved_scope_entries: tuple[str, ...] = ()
    accepted_blocked: tuple[AcceptanceStatus, ...] = field(default=())
    unmet: tuple[AcceptanceStatus, ...] = field(default=())


def enumerate_plan_identifiers(plan_text: str) -> list[str]:
    """Anchored ``P-``/``X-``/``AC-`` identifiers from a plan body, in order."""
    seen: dict[str, None] = {}
    for match in _PLAN_IDENTIFIER_RE.finditer(plan_text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def enumerate_prd_fr_identifiers(prd_text: str) -> list[str]:
    """``FR\\d+`` identifiers taken from FR headings only, in order."""
    seen: dict[str, None] = {}
    for match in _FR_HEADING_ID_RE.finditer(prd_text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def unmet_identifiers(
    enumerated: list[str],
    declared: dict[str, AcceptanceStatus],
) -> list[AcceptanceStatus]:
    """Enumerated identifiers whose status is ``unaddressed`` or automatable.

    An enumerated identifier with NO declaration entry is ``unaddressed`` —
    omission must not be a way past the gate.
    """
    unmet: list[AcceptanceStatus] = []
    for gate_id in enumerated:
        status = declared.get(gate_id) or AcceptanceStatus(gate_id=gate_id, kind="unaddressed")
        if status.is_unmet:
            unmet.append(status)
    return unmet


def resolve_plan_acceptance_block(
    *,
    mode: str,
    task_type: str,
    enumerated: list[str],
    declared: dict[str, AcceptanceStatus],
) -> bool:
    """The pure FR04 block predicate — no disk, no config, no clock.

    Delivery blocks exactly when ALL FOUR hold: the resolved mode is
    ``block_coding`` or ``block_all``; the task type carries a build artifact;
    at least one identifier was enumerated; and at least one status is
    ``unaddressed`` or ``blocked:automatable``. Every other combination is
    advisory at most, so the gate reuses the PRD-CORE-184 policy and introduces
    no new mode field.
    """
    if mode not in _BLOCKING_MODES:
        return False
    if task_type not in _BUILD_ARTIFACT_TASK_TYPES:
        return False
    if not enumerated:
        return False
    return bool(unmet_identifiers(enumerated, declared))


def _read_bounded(path: Path) -> str:
    """Read a document, capped at :data:`MAX_ENUMERATION_BYTES`."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.read(MAX_ENUMERATION_BYTES)


def _resolve_prd_scope(prd_scope: list[str]) -> tuple[list[Path], list[str]]:
    """Split ``prd_scope`` into resolved PRD files and unresolved entries."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.prd_utils import extract_prd_identifier

    prds_dir = resolve_project_root() / get_config().prds_relative_path
    resolved: list[Path] = []
    unresolved: list[str] = []
    for entry in prd_scope:
        prd_id = extract_prd_identifier(str(entry))
        matches = sorted(prds_dir.glob(f"{prd_id}*.md")) if prd_id else []
        # The glob is a prefix match, so ``PRD-CORE-249*`` also catches
        # ``PRD-CORE-2490-...``. Require a NON-DIGIT (or end) immediately after
        # the id, so a longer sequence number is a different requirement and the
        # entry is reported unresolved rather than silently enumerating another
        # PRD's FRs as this run's gates.
        boundary = re.compile(rf"^{re.escape(prd_id)}(?!\d)") if prd_id else None
        exact = [m for m in matches if boundary is not None and boundary.match(m.name)]
        if exact:
            resolved.append(exact[0])
        else:
            unresolved.append(str(entry))
    return resolved, unresolved


def _enumerate(run_path: Path, prd_scope: list[str]) -> tuple[list[str], list[str]]:
    """Enumerate both legs. Returns ``(identifiers, unresolved_scope_entries)``."""
    identifiers: dict[str, None] = {}
    plan_path = run_path / "reports" / "plan.md"
    if plan_path.is_file():
        for gate_id in enumerate_plan_identifiers(_read_bounded(plan_path)):
            identifiers.setdefault(gate_id, None)
    prd_files, unresolved = _resolve_prd_scope(prd_scope)
    for prd_file in prd_files:
        for gate_id in enumerate_prd_fr_identifiers(_read_bounded(prd_file)):
            identifiers.setdefault(gate_id, None)
    return list(identifiers), unresolved


def load_declaration(run_path: Path) -> dict[str, AcceptanceStatus]:
    """Load and parse ``{run}/reports/acceptance.yaml``.

    An absent file is an EMPTY declaration, not an error — every enumerated
    identifier is then ``unaddressed``, which is the intended signal for a run
    that never declared. An unreadable or malformed file raises
    :class:`AcceptanceDeclarationError` so the caller can fail closed.
    """
    path = run_path / "reports" / "acceptance.yaml"
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AcceptanceDeclarationError(f"reports/acceptance.yaml could not be read: {exc}") from exc
    return parse_declaration(raw)


def _block_message(unmet: list[AcceptanceStatus], task_type: str, mode: str) -> str:
    """The refusal, naming every offending identifier and what to do next.

    This is where submission suggestion 4's continuation nudge lands: at the
    decision point, in the refusal, rather than as a background poller.
    """
    named = ", ".join(f"{s.gate_id}={s.label}" for s in unmet)
    automatable = [s.gate_id for s in unmet if s.blocking_class == "automatable"]
    lines = [
        f"Delivery blocked: {len(unmet)} governing acceptance identifier(s) are not addressed "
        f"for task_type={task_type} under deliver_gate_mode={mode} — {named}.",
        "Declare each one in reports/acceptance.yaml as 'satisfied', or as "
        "'blocked:human-only:<owner>' / 'blocked:ops-only:<owner>' with a reason.",
    ]
    if automatable:
        lines.append(
            f"Automatable work is not an accepted blocker: {', '.join(automatable)}. "
            "Dispatch it to an available delegate or re-classify it as human-only/ops-only "
            "with the owner who actually holds it."
        )
    lines.append(
        "Or override with allow_unverified=true + an unexpired acceptable-failure record "
        "(failed_command, residual_risk, owner, expiry_iso)."
    )
    return " ".join(lines)


def _warning_message(unmet: list[AcceptanceStatus], unresolved: list[str]) -> str:
    parts: list[str] = []
    if unmet:
        parts.append(
            "Plan acceptance advisory: "
            + ", ".join(f"{s.gate_id}={s.label}" for s in unmet)
            + " (declare each in reports/acceptance.yaml)."
        )
    if unresolved:
        parts.append(
            f"{len(unresolved)} prd_scope entr(y/ies) resolve to no PRD file and enumerated nothing: "
            + ", ".join(unresolved[:10])
            + ". This is NOT a pass — the scope names something the gate could not measure."
        )
    return " ".join(parts)


def evaluate_plan_acceptance(run_path: Path, run_data: dict[str, object]) -> PlanAcceptanceOutcome:
    """Evaluate the gate for one run. Never raises.

    ``run_data`` is the already-parsed ``meta/run.yaml`` mapping, so the caller
    reads it once for both this gate and the surrounding cascade.
    """
    task_type = str(run_data.get("task_type", "unknown")) or "unknown"
    raw_scope = run_data.get("prd_scope") or []
    prd_scope = [str(entry) for entry in raw_scope] if isinstance(raw_scope, list) else []
    try:
        declared = load_declaration(run_path)
    except AcceptanceDeclarationError as exc:
        # Fail-CLOSED (NFR02): name the parse failure and refuse.
        logger.warning("plan_acceptance_declaration_unreadable", run=str(run_path), outcome="fail_closed")
        return PlanAcceptanceOutcome(
            should_block=True,
            message=(
                f"Delivery blocked: the plan-acceptance declaration is unreadable — {exc}. "
                "An unreadable declaration is indistinguishable from an absent one, so this gate "
                "fails closed. Fix reports/acceptance.yaml, or override with allow_unverified=true "
                "+ an unexpired acceptable-failure record."
            ),
        )
    enumerated, unresolved = _enumerate(run_path, prd_scope)
    if not enumerated and not unresolved:
        return PlanAcceptanceOutcome()
    unmet = unmet_identifiers(enumerated, declared)
    accepted = tuple(
        status for status in (declared.get(gate_id) for gate_id in enumerated) if status and status.is_accepted_blocked
    )
    mode = resolve_gate_mode(task_type)
    if resolve_plan_acceptance_block(mode=mode, task_type=task_type, enumerated=enumerated, declared=declared):
        logger.info(
            "plan_acceptance_blocked",
            run=str(run_path),
            task_type=task_type,
            deliver_gate_mode=mode,
            unmet=[status.gate_id for status in unmet],
        )
        return PlanAcceptanceOutcome(
            should_block=True,
            message=_block_message(unmet, task_type, mode),
            enumerated=tuple(enumerated),
            unresolved_scope_entries=tuple(unresolved),
            accepted_blocked=accepted,
            unmet=tuple(unmet),
        )
    return PlanAcceptanceOutcome(
        warning=_warning_message(unmet, unresolved),
        enumerated=tuple(enumerated),
        unresolved_scope_entries=tuple(unresolved),
        accepted_blocked=accepted,
        unmet=tuple(unmet),
    )


__all__ = [
    "MAX_ENUMERATION_BYTES",
    "PlanAcceptanceOutcome",
    "enumerate_plan_identifiers",
    "enumerate_prd_fr_identifiers",
    "evaluate_plan_acceptance",
    "load_declaration",
    "resolve_plan_acceptance_block",
    "unmet_identifiers",
]
