"""FR06 — predicate coverage. ``PREDICATE_COVERAGE``.

A gate can be enabled, correct, well-tested, and still verify nothing, because
its activation condition is met by almost none of its domain. The PRD-CORE-190
wiring gate is the fixture: it fires on a fraction of a percent of the PRD
corpus, so "the wiring gate is green" carries almost no information.

**This check is strictly read-only over PRD-CORE-190.** It imports the real
predicate (``_classify_fr_surface``) and runs it — it does not copy, reimplement,
modify, or configure it. Importing the live function is the point: a copied
predicate would drift and start measuring something else. NFR07 and PRD-CORE-231
ownership are preserved; nothing here writes to ``_prd_scoring_wiring.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

from trw_mcp.wiring.model import EdgeClass, Finding
from trw_mcp.wiring.registry import ArtifactContract

_IP_TIER_RE = re.compile(r"^ip_tier:\s*(\S+)", re.MULTILINE)


def measure_wiring_gate_coverage(repo_root: Path, prd_glob: str) -> tuple[int, int]:
    """Return ``(activating_prds, total_prds)`` for the PRD-CORE-190 wiring gate.

    A conservative prefilter skips PRDs that cannot produce an FR section at all
    (neither FR-heading form matches). It is a strict superset test — the same
    regexes ``_extract_fr_sections`` itself uses — so the measured number is
    identical to the unfiltered one, while avoiding a YAML frontmatter parse of
    every PRD in the corpus. Without it this single check costs ~9 s of NFR02's
    10 s budget; with it, ~3 s.
    """
    from trw_mcp.state.validation._prd_scoring_fr import (
        _FR_HEADING_RE,
        _FR_HYPHEN_HEADING_RE,
        _extract_fr_sections,
    )
    from trw_mcp.state.validation._prd_scoring_wiring import _classify_fr_surface

    total = 0
    activating = 0
    for path in sorted(repo_root.glob(prd_glob)):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += 1
        if not _FR_HEADING_RE.search(content) and not _FR_HYPHEN_HEADING_RE.search(content):
            continue
        match = _IP_TIER_RE.search(content)
        ip_tier = match.group(1) if match else ""
        if any(_classify_fr_surface(block, ip_tier) for _name, block in _extract_fr_sections(content)):
            activating += 1
    return activating, total


def check_predicate_coverage(
    repo_root: Path,
    contract: ArtifactContract,
    *,
    floor: float,
    prd_glob: str,
) -> list[Finding]:
    """Classify a gate whose activation covers a negligible share of its domain."""
    activating, total = measure_wiring_gate_coverage(repo_root, prd_glob)
    if total == 0:
        return [
            Finding(
                contract_id=contract.contract_id,
                edge_class=EdgeClass.PREDICATE_COVERAGE,
                producer_side=contract.producer,
                consumer_side=contract.consumer,
                evidence=f"glob {prd_glob!r} matched 0 PRDs, so the gate's domain is empty and its result is vacuous",
                remedy="point the detector at the real PRD corpus, or retire the gate",
            )
        ]

    coverage = activating / total
    if coverage >= floor:
        return []
    return [
        Finding(
            contract_id=contract.contract_id,
            edge_class=EdgeClass.PREDICATE_COVERAGE,
            producer_side=contract.producer,
            consumer_side=f"{contract.consumer} — {total} PRDs in the measured domain",
            evidence=(
                f"the gate's activation condition is met by {activating} of {total} PRDs "
                f"({coverage:.2%}), below the {floor:.0%} floor. A green result from this gate is "
                f"therefore evidence about {activating} documents, not about the corpus — measured "
                "read-only by importing the live predicate, not a copy of it"
            ),
            remedy=(
                "widen the activation condition (or make the declaration mandatory) so the gate covers a "
                "meaningful share of its domain, or state its narrow scope where its result is reported. "
                "Owned by PRD-CORE-231 R4 — this detector measures and never modifies it"
            ),
        )
    ]
