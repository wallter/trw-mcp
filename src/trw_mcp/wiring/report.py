"""FR09 — the reporter.

Every emitted finding names the contract, both of its sides, and the evidence
that produced it. That is Must-Have, not aspirational: the measured 47%
false-positive rate of the naive registrar check, and the 49-of-51 test-helper
noise from the generic dead-code scan, are what an unactionable detector looks
like in practice — and an unactionable detector gets disabled inside a week.
"""

from __future__ import annotations

from trw_mcp.wiring.baseline import BaselinePartition, entry_for
from trw_mcp.wiring.detector import DetectorResult
from trw_mcp.wiring.model import Finding, RegistryError

_RULE = "─" * 78


def validate_findings(findings: tuple[Finding, ...]) -> None:
    """Reject any finding a reader could not act on.

    ``Finding.__post_init__`` already enforces this at construction; the
    reporter re-asserts it because it is the last gate before output, and a
    silently-empty evidence field would make the whole run untrustworthy.
    """
    for finding in findings:
        if not finding.evidence.strip() or not finding.remedy.strip():
            raise RegistryError(f"finding {finding.key!r} has no evidence/remedy and cannot be reported (NFR04)")


def render_report(result: DetectorResult, partition: BaselinePartition) -> str:
    """Render the full human-readable run report."""
    validate_findings(result.findings)
    lines: list[str] = [
        _RULE,
        "TRW wiring detector (PRD-CORE-232 Phase A) — observation-based, enforcing",
        _RULE,
        (
            f"contracts registered: {len(result.registry)} "
            f"({result.observable_count} observable, {result.unobservable_count} declare no observable artifact)"
        ),
        f"findings: {len(result.findings)} total — {len(partition.blocking)} blocking, "
        f"{len(partition.acknowledged)} acknowledged in the baseline",
        f"elapsed: {result.duration_seconds:.2f}s",
        "",
    ]

    blind = _kinds_with_no_observable_subject(result)
    if blind:
        lines.append("CHECKS THAT EXAMINED NOTHING — a clean result below does NOT cover these:")
        for kind, total in blind:
            lines.append(
                f"  - {kind}: {total} contract(s) registered, 0 observable, so every "
                "check keyed on observability skipped all of them"
            )
        lines.append(
            "  This is not necessarily a defect — a contract kind can legitimately "
            "empty out. It is reported because 'the check passed' and 'the check had "
            "no subjects' must never look the same from the outside."
        )
        lines.append("")

    if partition.acknowledged:
        lines.append("ACKNOWLEDGED (pre-existing, tracked in DEFECT-LEDGER.md — not blocking):")
        for finding in partition.acknowledged:
            entry = entry_for(finding.key)
            ledger = f"{entry.ledger_id}: {entry.rationale}" if entry else "unknown ledger entry"
            lines.append(f"  - {finding.key}  [{ledger}]")
        lines.append("")

    if partition.stale_keys:
        lines.append("STALE BASELINE ENTRIES — these no longer fire and must be removed from baseline.py:")
        lines.extend(f"  - {key}" for key in partition.stale_keys)
        lines.append(
            "  A baseline that outlives its findings is a waiver list. Delete the entries above "
            "and record the disposition in DEFECT-LEDGER.md."
        )
        lines.append("")

    if partition.blocking:
        lines.append("BLOCKING FINDINGS:")
        lines.append("")
        for finding in partition.blocking:
            lines.append(finding.render())
            lines.append("")

    if partition.is_clean and not partition.acknowledged:
        lines.append("PASS — every registered contract produces its declared artifact.")
    elif partition.is_clean:
        # Never report a clean bill of health while findings exist. The count is
        # stated because the acknowledged set is meant to shrink and someone has
        # to keep seeing it.
        lines.append(
            f"PASS — no NEW findings. {len(partition.acknowledged)} pre-existing finding(s) remain "
            "acknowledged in baseline.py; each needs a disposition in DEFECT-LEDGER.md."
        )
    else:
        lines.append(
            f"FAIL — {len(partition.blocking)} blocking finding(s), {len(partition.stale_keys)} stale baseline entry(ies)."
        )
    lines.append(_RULE)
    return "\n".join(lines)


def _kinds_with_no_observable_subject(result: DetectorResult) -> list[tuple[str, int]]:
    """Contract kinds that are registered but have zero observable members.

    Every check keyed on `contract.observable` skips such a kind entirely, so
    the run reports no findings for it — indistinguishable, from the outside,
    from a kind that was examined and found clean.

    This is exactly how PRD-CORE-239 FR01 left the channel-render check: after
    the 12 marker-replace channels were removed, all 15 survivors declared
    EPHEMERAL_STDOUT / FULL_REWRITE / NONE / JSON_KEY_MERGE, so `is_observable`
    was False for every one and `_check_channel` ran zero times. The gate's
    `channel:*` PASS was an empty loop, and that PASS was cited as the
    verification for the whole removal.

    Deliberately NOT an error: a kind emptying out can be the correct outcome of
    a removal. It is surfaced so a reader cannot mistake silence for assurance.
    """
    by_kind: dict[str, list[bool]] = {}
    for contract in result.registry:
        by_kind.setdefault(contract.kind.value, []).append(contract.observable)
    return sorted((kind, len(flags)) for kind, flags in by_kind.items() if flags and not any(flags))
