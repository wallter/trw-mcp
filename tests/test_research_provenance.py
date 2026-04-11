from __future__ import annotations

from pathlib import Path

from trw_mcp.state.validation.research_provenance import lint_research_markdown


def test_lint_rejects_unlabeled_quantitative_claim() -> None:
    content = """---
research:
  provenance_lint: true
  provenance_scope: executive_summary
---
# Research Doc

## Executive Summary

This audit found 6 major gaps in the integration surface.

## Details

Historical notes.
"""

    failures = lint_research_markdown(content)

    assert failures == [
        "Research doc must include at least one provenance tag.",
        "executive_summary line 5: quantitative claim is missing a provenance tag.",
    ]


def test_lint_accepts_labeled_quantitative_and_hypothesis_claims() -> None:
    content = """---
research:
  provenance_lint: true
  provenance_scope: executive_summary
---
# Research Doc

## Executive Summary

This audit found 6 major gaps in the integration surface. [repo-verified]
Plugin-based extension work may still be worthwhile for future OpenCode integrations. [hypothesis]

## Details

Historical notes.
"""

    assert lint_research_markdown(content) == []


def test_lint_rejects_generated_artifact_without_builder_reference() -> None:
    content = """---
research:
  provenance_lint: true
---
# Research Doc

## Executive Summary

The release flow should update install-trw.py directly. [repo-verified]
"""

    failures = lint_research_markdown(content)

    assert failures == [
        "Generated installer references must cite build_installer.py or install-trw.template.py as the source of truth."
    ]


def test_opted_in_research_docs_pass_lint() -> None:
    docs_root = Path(__file__).resolve().parents[2] / "docs" / "research"
    markdown_files = sorted(docs_root.rglob("*.md"))

    opted_in_files = [
        path for path in markdown_files if "provenance_lint: true" in path.read_text(encoding="utf-8")
    ]

    assert opted_in_files

    failures_by_path: dict[str, list[str]] = {}
    for path in opted_in_files:
        failures = lint_research_markdown(path.read_text(encoding="utf-8"))
        if failures:
            failures_by_path[str(path.relative_to(docs_root.parent))] = failures

    assert failures_by_path == {}
