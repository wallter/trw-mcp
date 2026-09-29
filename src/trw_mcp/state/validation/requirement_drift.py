"""Requirement drift, orphan detection and amendment matching against a git-derived baseline.

PRD-CORE-321 FR02, FR03 and FR04.

:func:`detect_requirement_drift` compares every requirement of a resolved
:class:`~trw_mcp.state.validation.requirement_baseline.BaselineResolution` with
the same ``requirement_id`` in the PRD's current text (FR02), and, once the PRD
is or was implemented, checks each baselined requirement's evidence file and
call chain from the BASELINE's own inputs (FR03), so a dropped requirement is
checked too. :func:`amendment_rows` and :func:`match_amendment` decide whether a
``## Requirement Amendments`` row records a finding (FR04). It writes nothing.

Runtime caller: ``trw_mcp.tools._deliver_requirement_drift.compute_requirement_drift``
-> :func:`detect_requirement_drift`, :func:`amendment_rows` and
:func:`match_amendment`, reached from ``trw_deliver`` through
``evaluate_delivery_gates``.

Soundness scope:

* FR02 proves the compared fields (``acceptance_criteria`` as an ordered list of
  stripped items, ``evidence_artifact`` stripped, the call chain as a tuple)
  differ as strings from the baseline's. It does not prove intent was preserved
  or weakened: a typo fix is flagged like a narrowing.
* FR03 proves the baseline evidence file exists under the repo root and the
  baseline chain still resolves as a static, direct, non-test edge in the
  running package (PRD-CORE-320's ``verify_chain`` scope). It does not prove the
  test named after ``::`` still exists or still asserts the original behaviour;
  anything about prose evidence (``evidence_not_checkable``); that a file at the
  same path is the original rather than an unrelated one recreated there; or
  that the running package is the checkout under delivery when the two differ.
* FR04 proves a row with the matching id, a qualifying date and (under block)
  an owner and an unexpired expiry exists. It does not prove the reason is
  true, reviewed or adequate.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Literal

from trw_mcp.state.validation.chain_declarations import markdown_tables
from trw_mcp.state.validation.requirement_baseline import (
    APPROVED_FAMILY,
    BaselineRequirement,
    BaselineResolution,
    requirements_from_text,
)

if TYPE_CHECKING:
    from trw_mcp.state.validation.call_chain import ToolSite

DriftKind = Literal["changed", "dropped", "orphaned", "evidence_not_checkable"]

#: FR03 orphan reasons, stated verbatim by the PRD.
ORPHAN_EVIDENCE_MISSING = "evidence artifact missing"
ORPHAN_CHAIN_BROKEN = "call chain no longer verifies"
EVIDENCE_NOT_A_PATH = "evidence_artifact is not a checkable repository path"

#: A status at which the promised evidence must already exist (FR03 applicability).
_IMPLEMENTED_FAMILY: frozenset[str] = APPROVED_FAMILY - {"approved"}
_PATH_SEPARATORS = re.compile(r"[\\/]")


class DuplicateRequirementIdError(ValueError):
    """Two mappings in one PRD version share a requirement id, so drift cannot be judged.

    Keeping either row would make detection depend on row order: a weakened
    criterion can hide behind a second, unchanged mapping with the same id
    (core321-s2 r1). ``compute_requirement_drift`` turns it into the PRD's
    ``not_evaluated`` entry (reason ``DuplicateRequirementIdError``), which is
    block-eligible, never into "no finding".
    """


def _by_id(requirements: Sequence[BaselineRequirement], side: str) -> dict[str, BaselineRequirement]:
    """Index *requirements* by id; any repeated id raises :class:`DuplicateRequirementIdError` naming *side*."""
    seen: dict[str, BaselineRequirement] = {}
    for req in requirements:
        if req.requirement_id in seen:
            raise DuplicateRequirementIdError(f"{side} maps {req.requirement_id} more than once")
        seen[req.requirement_id] = req
    return seen


@dataclass(frozen=True)
class DriftFinding:
    """One requirement-level finding: ``changed`` lists ``changed_fields``; ``orphaned`` names its ``reason``."""

    requirement_id: str
    kind: DriftKind
    changed_fields: tuple[str, ...] = ()
    reason: str = ""


def evidence_path(value: str) -> str | None:
    """The repository-relative path an ``evidence_artifact`` value names, or ``None`` for prose.

    *value* is already YAML-parsed, so a plain or folded scalar that continued onto
    a second line arrives joined by one space. A pytest ``::`` node-id suffix is
    stripped first, then a ``#`` fragment. A remainder that is empty, contains
    whitespace, is absolute, or has a ``..`` segment is not a path (FR03(a)).
    """
    text = value.strip().partition("::")[0].partition("#")[0].strip()
    if not text or any(char.isspace() for char in text):
        return None
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).drive:
        return None
    if ".." in _PATH_SEPARATORS.split(text):
        return None
    return text


def orphan_check_applies(baseline: BaselineResolution, current_status: str | None) -> bool:
    """FR03: the current status is in the implemented family, or history holds an implemented version."""
    return current_status in _IMPLEMENTED_FAMILY or baseline.implemented_in_history


def _changed_fields(base: BaselineRequirement, now: BaselineRequirement) -> tuple[str, ...]:
    fields: list[str] = []
    if [item.strip() for item in base.acceptance_criteria] != [item.strip() for item in now.acceptance_criteria]:
        fields.append("acceptance_criteria")
    if base.evidence_artifact.strip() != now.evidence_artifact.strip():
        fields.append("evidence_artifact")
    if base.call_chain != now.call_chain:
        fields.append("call_chain")
    return tuple(fields)


class _Chains:
    """Verifies baseline chains against *source_root*, registering the tool sites at most once."""

    def __init__(self, source_root: Path, tools: Mapping[str, ToolSite] | None, hook_evidence: Collection[str]) -> None:
        self.source_root = source_root
        self.tools = tools
        self.hook_evidence = hook_evidence

    def wired(self, chain: tuple[str, ...]) -> bool:
        from trw_mcp.state.validation.call_chain import registered_tool_sites, verify_chain

        if self.tools is None:
            self.tools = registered_tool_sites()
        verdict = verify_chain(self.source_root, chain, tools=self.tools, hook_evidence=self.hook_evidence)
        return verdict.status == "wired"


def _orphan_findings(base: BaselineRequirement, repo_root: Path, chains: _Chains) -> list[DriftFinding]:
    findings: list[DriftFinding] = []
    path = evidence_path(base.evidence_artifact)
    if path is None:
        findings.append(DriftFinding(base.requirement_id, "evidence_not_checkable", reason=EVIDENCE_NOT_A_PATH))
    elif not (repo_root / path).exists():
        findings.append(DriftFinding(base.requirement_id, "orphaned", reason=ORPHAN_EVIDENCE_MISSING))
    if base.call_chain.chain and not chains.wired(base.call_chain.chain):
        findings.append(DriftFinding(base.requirement_id, "orphaned", reason=ORPHAN_CHAIN_BROKEN))
    return findings


def detect_requirement_drift(
    baseline: BaselineResolution,
    current_text: str,
    *,
    current_status: str | None,
    repo_root: Path,
    source_root: Path,
    tools: Mapping[str, ToolSite] | None = None,
    hook_evidence: Collection[str] = (),
) -> list[DriftFinding]:
    """FR02 drift plus FR03 orphans for every requirement *baseline* carries, in baseline order.

    *repo_root* (the FR01 git root) is where evidence paths are checked;
    *source_root* is the directory containing the running ``trw_mcp`` package,
    the root ``verify_chain`` resolves dotted modules under (never the git root).
    A requirement added after approval is not drift. A baseline with no
    requirements (an approved PRD without ``verification.mappings``) yields no
    finding.
    """
    _by_id(baseline.requirements, "baseline")
    current = _by_id(requirements_from_text(baseline.prd_id, current_text), "current text")
    check_orphans = orphan_check_applies(baseline, current_status)
    chains = _Chains(source_root, tools, hook_evidence)
    findings: list[DriftFinding] = []
    for base in baseline.requirements:
        now = current.get(base.requirement_id)
        if now is None:
            findings.append(DriftFinding(base.requirement_id, "dropped"))
        elif changed := _changed_fields(base, now):
            findings.append(DriftFinding(base.requirement_id, "changed", changed))
        if check_orphans:
            findings.extend(_orphan_findings(base, repo_root, chains))
    return findings


# --------------------------------------------------------------------------- #
# FR04 — the Requirement Amendments record
# --------------------------------------------------------------------------- #

#: Finding kinds a row naming the REQUIREMENT id records, dated against the first approval.
REQUIREMENT_ROW_KINDS: frozenset[str] = frozenset({"changed", "dropped", "orphaned"})
#: Finding kinds a row naming the PRD id records, dated against the NEWEST approval. Every other
#: kind (``shallow_clone``, and the entry statuses ``baseline_unresolvable``/``not_evaluated``) is
#: never recordable: the checker could not read what a row would record against.
PRD_ROW_KINDS: frozenset[str] = frozenset({"baseline_reapproved", "status_regressed"})
_AMENDMENTS_HEADING = "## requirement amendments"
_AMENDMENT_COLUMNS = ("requirement", "date", "reason", "owner", "expiry")


@dataclass(frozen=True)
class AmendmentRow:
    """One ``| Requirement | Date | Reason | Owner | Expiry |`` row, cells trimmed and unparsed."""

    requirement: str
    date: str
    reason: str
    owner: str
    expiry: str


@dataclass(frozen=True)
class AmendmentMatch:
    """``recorded`` with the qualifying ``row``, or unrecorded with the first failing row's ``reason``.

    ``reason`` is ``amendment_incomplete`` (no Reason, an unparseable Date, or under
    block no Owner or no parseable Expiry), ``amendment_predates_approval`` or
    ``amendment_expired``; ``None`` when no row names the id.
    """

    recorded: bool
    reason: str | None = None
    row: AmendmentRow | None = None


def _amendment_section(text: str) -> str:
    """The lines under every ``## Requirement Amendments`` heading, up to the next level-1 or level-2 heading."""
    kept: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith(("# ", "## ")):
            inside = line.strip().lower() == _AMENDMENTS_HEADING
            continue
        if inside:
            kept.append(line)
    return "\n".join(kept)


def amendment_rows(text: str) -> tuple[AmendmentRow, ...]:
    """Every data row of the Amendments tables in PRD *text*, in document order. Never raises.

    Only a table inside the ``## Requirement Amendments`` section whose header is
    exactly the five FR04 columns (case-insensitive) is read; a row with fewer
    cells than the header matches nothing (class A: an unparseable row records nothing).
    """
    rows: list[AmendmentRow] = []
    for table in markdown_tables(_amendment_section(text)):
        if tuple(cell.lower() for cell in table.header) != _AMENDMENT_COLUMNS:
            continue
        rows += [AmendmentRow(*cells[:5]) for cells in table.rows if len(cells) >= len(_AMENDMENT_COLUMNS)]
    return tuple(rows)


def _iso_date(text: str) -> date | None:
    """*text* as a calendar date (an ISO date, or the date part of an ISO datetime); ``None`` if neither."""
    try:
        return datetime.fromisoformat(text).date()  # a bare ISO date parses as midnight (Python >= 3.11)
    except ValueError:  # trw-fail-silent-allow: None is the named amendment_incomplete reason, never a match
        return None


def _row_failure(row: AmendmentRow, approval_date: date, block: bool, today: date) -> str | None:
    row_date = _iso_date(row.date)
    if not row.reason or row_date is None:
        return "amendment_incomplete"
    if row_date < approval_date:
        return "amendment_predates_approval"
    if not block:
        return None
    expiry = _iso_date(row.expiry)
    if not row.owner or expiry is None:
        return "amendment_incomplete"
    return "amendment_expired" if expiry < today else None


def match_amendment(
    requirement_id: str,
    rows: Sequence[AmendmentRow],
    approval_date: date,
    mode: Literal["warn", "block"],
    today: date,
) -> AmendmentMatch:
    """FR04: whether a row records the finding named *requirement_id*.

    A row records it when its Requirement cell equals the id, its Date (dates
    only) is on or after *approval_date*, and its Reason is non-empty; under
    ``mode="block"`` it also needs a non-empty Owner and an Expiry not before
    *today* (both inclusive, PRD-CORE-191's ``expiry_iso`` rule). Any
    qualifying row records; otherwise the first failing row names the reason.

    Runtime caller: ``trw_mcp.tools._deliver_requirement_drift._record`` (through
    ``compute_requirement_drift``), which picks the id and the approval date per
    finding kind (:data:`REQUIREMENT_ROW_KINDS`, :data:`PRD_ROW_KINDS`).
    """
    failure: str | None = None
    for row in rows:
        if row.requirement != requirement_id:
            continue
        reason = _row_failure(row, approval_date, mode == "block", today)
        if reason is None:
            return AmendmentMatch(recorded=True, row=row)
        failure = failure or reason
    return AmendmentMatch(recorded=False, reason=failure)


__all__ = [
    "EVIDENCE_NOT_A_PATH",
    "ORPHAN_CHAIN_BROKEN",
    "ORPHAN_EVIDENCE_MISSING",
    "PRD_ROW_KINDS",
    "REQUIREMENT_ROW_KINDS",
    "AmendmentMatch",
    "AmendmentRow",
    "DriftFinding",
    "amendment_rows",
    "detect_requirement_drift",
    "evidence_path",
    "match_amendment",
    "orphan_check_applies",
]
