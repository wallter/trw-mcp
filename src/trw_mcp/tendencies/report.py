"""Advisory tendency report builder (PRD-QUAL-109 FR-03).

Walks a configurable set of corpus roots, runs every registered detector
fail-open isolated, and renders the accumulated findings (human + ``--json``).
The report is **advisory** — it never mutates an artifact and the CLI always
exits 0. Output is deterministic (NFR-03) and reads only under the provided
corpus roots, emitting bounded evidence pointers, never full-file dumps (NFR-04).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from trw_mcp.tendencies.detectors import Corpus, Finding, run_detectors
from trw_mcp.tendencies.taxonomy import TENDENCY_METADATA


@dataclass(frozen=True)
class TendencyReport:
    """The collected findings across one or more corpus roots."""

    roots: tuple[Path, ...]
    findings: tuple[Finding, ...]
    files_scanned: int


def default_corpus_roots(project_root: Path, *, prds_relative_path: str) -> list[Path]:
    """Resolve the default corpus roots under ``project_root`` (config-overridable).

    Per the PRD-QUAL-109 coordinator decision: the project's
    ``.trw/distill/handoff-archive`` plus the configured PRD catalogue directory,
    each included only **when present** (NFR-04 safety — never invent a root).
    """
    project_root = Path(project_root)
    candidates = [
        project_root / ".trw" / "distill" / "handoff-archive",
        project_root / (prds_relative_path or "docs/requirements-aare-f/prds"),
    ]
    return [c for c in candidates if c.exists()]


def build_report(roots: list[Path]) -> TendencyReport:
    """Build a corpus from every root, run all detectors, and collect findings.

    Each root is walked independently and fail-open: a root that does not exist
    (or whose walk raises) contributes zero files rather than aborting the run.
    """
    all_findings: list[Finding] = []
    files_scanned = 0
    resolved: list[Path] = []
    for root in roots:
        root = Path(root)
        resolved.append(root)
        try:
            corpus = Corpus.from_root(root)
        except OSError:
            continue
        files_scanned += len(corpus.files)
        all_findings.extend(run_detectors(corpus))
    all_findings.sort(key=Finding.sort_key)
    return TendencyReport(
        roots=tuple(resolved),
        findings=tuple(all_findings),
        files_scanned=files_scanned,
    )


def _countermeasure_for(finding: Finding) -> str:
    meta = TENDENCY_METADATA.get(finding.tendency)
    return meta.countermeasure_pointer if meta is not None else ""


def render_json(report: TendencyReport) -> str:
    """Machine-readable output for CI/telemetry ingestion (FR-03 ``--json``)."""
    findings = [
        {
            "tendency": f.tendency.name,
            "detector": f.detector_name,
            "message": f.message,
            "evidence": list(f.evidence),
            "countermeasure": _countermeasure_for(f),
            "is_error": f.is_error,
        }
        for f in report.findings
    ]
    payload = {
        "roots": [str(r) for r in report.roots],
        "files_scanned": report.files_scanned,
        "findings": findings,
        "tendency_count": len({f.tendency for f in report.findings if not f.is_error}),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_human(report: TendencyReport) -> str:
    """Human-readable advisory report."""
    lines: list[str] = []
    roots = ", ".join(str(r) for r in report.roots) or "(none)"
    lines.append(f"trw-mcp tendencies — advisory report ({report.files_scanned} files scanned)")
    lines.append(f"corpus roots: {roots}")
    lines.append("")

    real = [f for f in report.findings if not f.is_error]
    errors = [f for f in report.findings if f.is_error]

    if not real:
        lines.append("no tendencies detected.")
    else:
        for f in real:
            lines.append(f"[{f.tendency.name}] {f.message}")
            lines.append(f"  countermeasure: {_countermeasure_for(f)}")
            lines.extend(f"  - {ev}" for ev in f.evidence)
            lines.append("")

    if errors:
        lines.append("detector errors (fail-open isolated — run still completed):")
        lines.extend(f"  ! {f.detector_name}: {f.message}" for f in errors)

    lines.append("")
    lines.append("advisory only — no artifact was modified; this report never blocks.")
    return "\n".join(lines)


__all__ = [
    "TendencyReport",
    "build_report",
    "default_corpus_roots",
    "render_human",
    "render_json",
]
