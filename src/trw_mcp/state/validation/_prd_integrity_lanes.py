"""Typed changed-scope and full-corpus validation lanes (PRD-QUAL-121-FR05).

Belongs to the ``prd_integrity.py`` facade; re-exported there.

Fast feedback evaluates only the changed PRDs plus their dependency closure
(NFR02: work proportional to the closure). Release proof evaluates the full
corpus and exposes duration, item counts, and truncation. A changed-scope
pass NEVER substitutes for full-corpus proof: only a ``full_corpus`` lane
result with outcome ``pass`` can satisfy release, and a changed-scope result
always reports the full corpus as ``unknown``.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

from trw_mcp.state.prd_utils import parse_frontmatter

logger = structlog.get_logger(__name__)

LANE_SCHEMA = "prd-validation-lane/v1"


@dataclass(slots=True)
class ValidationLaneResult:
    """Typed lane outcome carrying lane, scope digest, skipped groups, and
    baseline comparison (PRD-QUAL-121-FR05)."""

    lane: Literal["changed_scope", "full_corpus"]
    outcome: Literal["pass", "fail", "unknown"]
    scope_digest: str
    evaluated: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    skipped_groups: list[str] = field(default_factory=list)
    full_corpus_status: Literal["pass", "fail", "unknown"] = "unknown"
    baseline_comparison: dict[str, object] | None = None
    duration_seconds: float = 0.0
    item_count: int = 0
    truncated: bool = False

    def satisfies_release(self) -> bool:
        """Only a passing FULL-CORPUS lane is release proof — a scoped pass
        with an unrelated corpus failure can never satisfy release."""
        return self.lane == "full_corpus" and self.outcome == "pass" and not self.truncated


def _prd_files(prds_dir: Path) -> list[Path]:
    return sorted(prds_dir.glob("PRD-*.md")) if prds_dir.exists() else []


def _scope_digest(paths: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in paths:
        hasher.update(path.name.encode("utf-8"))
        try:
            hasher.update(hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            hasher.update(b"unreadable")
    return "sha256:" + hasher.hexdigest()


def _frontmatter_map(files: list[Path]) -> dict[Path, dict[str, object] | None]:
    """Map each file to its frontmatter; ``None`` marks a PARSE DEFECT only.

    A file with no ``---`` block at all is a legitimate frontmatter-less PRD
    (same rule as ``_check_frontmatter_parses``) and maps to ``{}`` — its
    identity comes from the filename. Only an existing block that fails to
    parse is a finding.
    """
    result: dict[Path, dict[str, object] | None] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            result[path] = None
            continue
        fm = parse_frontmatter(text)
        if fm:
            result[path] = fm
        else:
            result[path] = None if text.lstrip().startswith("---") else {}
    return result


def _integrity_findings(files: list[Path], frontmatters: dict[Path, dict[str, object] | None]) -> list[str]:
    """Shared lane rule set: parse failures + duplicate identifiers."""
    findings: list[str] = []
    owners: dict[str, list[str]] = {}
    for path in files:
        fm = frontmatters.get(path)
        if fm is None:
            findings.append(f"frontmatter_unparseable: {path.name}")
            continue
        prd_id = str(fm.get("id", path.stem))
        owners.setdefault(prd_id, []).append(path.name)
    for prd_id, names in sorted(owners.items()):
        if len(names) > 1:
            findings.append(f"duplicate_identifier: {prd_id} owned by {', '.join(sorted(names))}")
    return findings


def _dependency_closure(changed_ids: set[str], frontmatters: dict[Path, dict[str, object] | None]) -> set[str]:
    """Transitive depends_on closure over the corpus, seeded by ``changed_ids``."""
    depends: dict[str, set[str]] = {}
    for fm in frontmatters.values():
        if not fm:
            continue
        prd_id = str(fm.get("id", ""))
        traceability = fm.get("traceability")
        if prd_id and isinstance(traceability, dict) and isinstance(traceability.get("depends_on"), list):
            depends[prd_id] = {str(dep) for dep in traceability["depends_on"]}
    closure = set(changed_ids)
    frontier = set(changed_ids)
    while frontier:
        next_frontier: set[str] = set()
        for prd_id in frontier:
            next_frontier |= depends.get(prd_id, set()) - closure
        closure |= next_frontier
        frontier = next_frontier
    return closure


def evaluate_changed_scope(prds_dir: Path, changed_prd_ids: list[str]) -> ValidationLaneResult:
    """Fast-feedback lane: changed PRDs plus dependency closure (FR05/NFR02).

    The result is scoped truth only — ``full_corpus_status`` stays ``unknown``
    and :meth:`ValidationLaneResult.satisfies_release` is always False.
    """
    started = time.monotonic()
    all_files = _prd_files(prds_dir)
    frontmatters = _frontmatter_map(all_files)
    closure = _dependency_closure(set(changed_prd_ids), frontmatters)
    scoped_files = [
        path
        for path in all_files
        if str((frontmatters.get(path) or {}).get("id", path.stem)) in closure
        or path.stem in closure
        or any(path.stem.startswith(f"{prd_id}-") for prd_id in closure)
    ]
    findings = _integrity_findings(scoped_files, frontmatters)
    return ValidationLaneResult(
        lane="changed_scope",
        outcome="fail" if findings else "pass",
        scope_digest=_scope_digest(scoped_files),
        evaluated=[path.name for path in scoped_files],
        findings=findings,
        full_corpus_status="unknown",
        duration_seconds=round(time.monotonic() - started, 3),
        item_count=len(scoped_files),
    )


def evaluate_full_corpus(
    prds_dir: Path,
    *,
    baseline_receipt_digest: str = "",
    max_items: int | None = None,
) -> ValidationLaneResult:
    """Release-proof lane over the full corpus (FR05/NFR02).

    Exposes duration, item counts, and truncation; a truncated run can never
    satisfy release. ``baseline_receipt_digest`` (the FR01 committed-tree
    BaselineReceipt digest) is echoed into ``baseline_comparison`` so the
    consumer can bind this run to the named immutable baseline.
    """
    started = time.monotonic()
    all_files = _prd_files(prds_dir)
    truncated = max_items is not None and len(all_files) > max_items
    evaluated_files = all_files[:max_items] if truncated else all_files
    frontmatters = _frontmatter_map(evaluated_files)
    findings = _integrity_findings(evaluated_files, frontmatters)
    if truncated:
        findings.append(f"truncated: evaluated {len(evaluated_files)} of {len(all_files)} corpus files")
    outcome: Literal["pass", "fail", "unknown"]
    if truncated:
        outcome = "unknown"  # skipped work is never claimed as passed (NFR02)
    else:
        outcome = "fail" if findings else "pass"
    return ValidationLaneResult(
        lane="full_corpus",
        outcome=outcome,
        scope_digest=_scope_digest(evaluated_files),
        evaluated=[path.name for path in evaluated_files],
        findings=findings,
        full_corpus_status=outcome,
        baseline_comparison=({"baseline_receipt_digest": baseline_receipt_digest} if baseline_receipt_digest else None),
        duration_seconds=round(time.monotonic() - started, 3),
        item_count=len(evaluated_files),
        truncated=truncated,
    )
