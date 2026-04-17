"""Research document provenance lint helpers for PRD-QUAL-060."""

from __future__ import annotations

import re

from trw_mcp.state.prd_utils import _FRONTMATTER_RE, parse_frontmatter

_PROVENANCE_TAGS = ("[repo-verified]", "[upstream-verified]", "[hypothesis]")
_QUANTITATIVE_CLAIM_RE = re.compile(r"\b\d+(?:\.\d+)?(?:%|[Kk]|x)?\b")
_HYPOTHESIS_RE = re.compile(r"\b(may|might|could|likely|probably|appears?|suggests?)\b", re.IGNORECASE)


def lint_research_markdown(content: str) -> list[str]:
    """Return provenance lint failures for opted-in research markdown."""
    frontmatter = parse_frontmatter(content)
    research = frontmatter.get("research")
    if not isinstance(research, dict) or not bool(research.get("provenance_lint")):
        return []

    body = _strip_frontmatter(content)
    scope_name = str(research.get("provenance_scope", "document"))
    failures: list[str] = []

    if not any(tag in body for tag in _PROVENANCE_TAGS):
        failures.append("Research doc must include at least one provenance tag.")

    for line_number, line in _scope_lines(body, scope_name):
        normalized = line.strip()
        if _skip_line(normalized):
            continue
        if _looks_quantitative_claim(normalized) and not _has_provenance_tag(normalized):
            failures.append(f"{scope_name} line {line_number}: quantitative claim is missing a provenance tag.")
        if _looks_hypothesis(normalized) and not _has_provenance_tag(normalized):
            failures.append(f"{scope_name} line {line_number}: speculative claim is missing a provenance tag.")

    if "install-trw.py" in body and not any(
        marker in body for marker in ("build_installer.py", "install-trw.template.py")
    ):
        failures.append(
            "Generated installer references must cite build_installer.py or install-trw.template.py "
            "as the source of truth."
        )

    return failures


def _strip_frontmatter(content: str) -> str:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content
    return content[match.end() :].lstrip("\n")


def _scope_lines(body: str, scope_name: str) -> list[tuple[int, str]]:
    lines = body.splitlines()
    if scope_name != "executive_summary":
        return [(index, line) for index, line in enumerate(lines, start=1)]

    heading_index = next((index for index, line in enumerate(lines) if line.strip() == "## Executive Summary"), None)
    if heading_index is None:
        return []

    end_index = len(lines)
    for index in range(heading_index + 1, len(lines)):
        if lines[index].startswith("## "):
            end_index = index
            break

    return [(index + 1, lines[index]) for index in range(heading_index + 1, end_index)]


def _skip_line(line: str) -> bool:
    return not line or line.startswith(("#", "|", "```"))


def _looks_quantitative_claim(line: str) -> bool:
    return bool(_QUANTITATIVE_CLAIM_RE.search(line))


def _looks_hypothesis(line: str) -> bool:
    return bool(_HYPOTHESIS_RE.search(line))


def _has_provenance_tag(line: str) -> bool:
    return any(tag in line for tag in _PROVENANCE_TAGS)
