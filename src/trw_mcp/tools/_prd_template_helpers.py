"""PRD template processing helpers — extracted from requirements.py.

Private helpers for loading, substituting, prefilling, filtering, and
rendering PRD templates used by ``trw_prd_create``.
"""

from __future__ import annotations

import contextlib
import re
from io import StringIO
from pathlib import Path
from typing import cast

import structlog
from ruamel.yaml import YAML

from trw_mcp.models.typed_dicts import PrdFrontmatterDict
from trw_mcp.state.prd_utils import (
    _FRONTMATTER_RE,
    extract_prd_refs,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level caches and compiled regexes
# ---------------------------------------------------------------------------

_CACHED_TEMPLATE_BODY: str | None = None
_CACHED_TEMPLATE_VERSION: str | None = None


def reset_template_cache() -> None:
    """Reset the cached PRD template body and version.

    Call this to force a fresh load from ``data/prd_template.md`` on the
    next ``_load_template_body()`` invocation.  Useful in tests and when
    switching between projects with different template files.
    """
    global _CACHED_TEMPLATE_BODY, _CACHED_TEMPLATE_VERSION
    _CACHED_TEMPLATE_BODY = None
    _CACHED_TEMPLATE_VERSION = None


_TEMPLATE_VERSION_RE = re.compile(r"\*Template version:\s*([\d.]+)")
_FILE_REF_RE = re.compile(r"[\w/]+\.\w+")
_GOAL_KW_RE = re.compile(r"\b(goal|objective|achieve|deliver)\b", re.IGNORECASE)
_SLO_KW_RE = re.compile(r"\b(slo|latency|availability|throughput)\b", re.IGNORECASE)

# Fallback body used when data/prd_template.md is missing
_FALLBACK_BODY = """# PRD-CATEGORY-SEQ: Title

**Quick Reference**:
- **Status**: Draft
- **Priority**: P1
- **Evidence**: Moderate
- **Implementation Confidence**: 0.7

---

## 1. Problem Statement
## 2. Goals & Non-Goals
## 3. User Stories
## 4. Functional Requirements
## 5. Non-Functional Requirements
## 6. Technical Approach
## 7. Test Strategy
## 8. Rollout Plan
## 9. Success Metrics
## 10. Dependencies & Risks
## 11. Open Questions
## 12. Traceability Matrix
"""


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------


def _load_template_body() -> str:
    """Load PRD template body from data/prd_template.md, cached.

    Strips YAML frontmatter (everything between the first ``---`` pair)
    and caches both the body and the extracted template version.

    Returns:
        Template body as a string (markdown after frontmatter).
    """
    global _CACHED_TEMPLATE_BODY, _CACHED_TEMPLATE_VERSION

    if _CACHED_TEMPLATE_BODY is not None:
        return _CACHED_TEMPLATE_BODY

    template_path = Path(__file__).parent.parent / "data" / "prd_template.md"

    if not template_path.exists():
        logger.warning("prd_template_not_found", path=str(template_path))
        _CACHED_TEMPLATE_BODY = _FALLBACK_BODY
        _CACHED_TEMPLATE_VERSION = None
        return _CACHED_TEMPLATE_BODY

    raw = template_path.read_text(encoding="utf-8")

    # Strip YAML frontmatter (between first --- pair)
    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        body = raw[fm_match.end() :].lstrip("\n")
    else:
        body = raw

    # Extract template version from footer
    ver_match = _TEMPLATE_VERSION_RE.search(body)
    _CACHED_TEMPLATE_VERSION = ver_match.group(1) if ver_match else None

    _CACHED_TEMPLATE_BODY = body
    return _CACHED_TEMPLATE_BODY


# ---------------------------------------------------------------------------
# Template variable substitution
# ---------------------------------------------------------------------------


def _substitute_template(
    body: str,
    prd_id: str,
    title: str,
    category: str,
    sequence: int,
    priority: str,
    confidence: float,
) -> str:
    """Replace template variables with actual values.

    Uses explicit ``str.replace()`` for the known variables to avoid
    false positives on prose ``{...}`` placeholders in the template.

    Args:
        body: Raw template body.
        prd_id: Full PRD identifier (e.g. ``PRD-CORE-007``).
        title: PRD title.
        category: PRD category (e.g. ``CORE``).
        sequence: Sequence number.
        priority: Priority string (e.g. ``P1``).
        confidence: Base confidence score.

    Returns:
        Template body with variables substituted.
    """
    seq_str = f"{sequence:03d}"
    result = body
    result = result.replace("{CATEGORY}", category)
    result = result.replace("{SEQUENCE}", seq_str)
    result = result.replace("{CAT}", category)
    result = result.replace("{SEQ}", seq_str)
    result = result.replace("{Title}", title)

    # Set dynamic Quick Reference values
    result = result.replace(
        "- **Status**: Draft | Review | Approved | Implemented",
        "- **Status**: Draft",
    )
    result = result.replace(
        "- **Priority**: P0 | P1 | P2 | P3",
        f"- **Priority**: {priority}",
    )
    result = result.replace(
        "- **Evidence**: Strong | Moderate | Limited | Theoretical",
        "- **Evidence**: Moderate",
    )
    result = result.replace(
        "- **Implementation Confidence**: 0.8",
        f"- **Implementation Confidence**: {confidence}",
    )

    # FR04 (PRD-FIX-056): Inject **Status**: active after **Priority**: lines
    # inside FR blocks (### *-FRN: headers).  Applies only to lines that are
    # the FR-level Priority field (bold, not inside a table or list).
    # The replacement inserts "**Status**: active\n" after each such line,
    # but only when there is no **Status**: line already present in that block.
    result = re.sub(
        r"(\*\*Priority\*\*: Must Have \| Should Have \| Nice to Have)",
        r"\1\n**Status**: active",
        result,
    )

    return result


# ---------------------------------------------------------------------------
# Prefill extraction and application
# ---------------------------------------------------------------------------


def _extract_prefill(input_text: str) -> dict[str, list[str]]:
    """Extract structured prefill data from input text.

    Best-effort extraction -- never raises on failures.

    Args:
        input_text: User-supplied feature request / requirements text.

    Returns:
        Dict with keys ``file_refs``, ``prd_deps``, ``goals``, ``slos``.
    """
    prefill: dict[str, list[str]] = {
        "file_refs": [],
        "prd_deps": [],
        "goals": [],
        "slos": [],
    }

    with contextlib.suppress(re.error, TypeError):
        prefill["file_refs"] = sorted(set(_FILE_REF_RE.findall(input_text)))

    with contextlib.suppress(re.error, TypeError, ValueError):
        prefill["prd_deps"] = extract_prd_refs(input_text)

    # Extract goal-like and SLO sentences
    try:
        for sentence in re.split(r"[.\n]", input_text):
            stripped = sentence.strip()
            if not stripped:
                continue
            if _GOAL_KW_RE.search(stripped):
                prefill["goals"].append(stripped)
            if _SLO_KW_RE.search(stripped):
                prefill["slos"].append(stripped)
    except (re.error, TypeError):
        logger.debug("prefill_extraction_failed", exc_info=True)

    return prefill


def _apply_prefill(
    body: str,
    prefill: dict[str, list[str]],
    input_text: str,
) -> str:
    """Apply prefill data into the template body.

    Best-effort insertion -- no exceptions on failures.

    Args:
        body: Template body (after variable substitution).
        prefill: Extracted prefill data from :func:`_extract_prefill`.
        input_text: Original input text for background section.

    Returns:
        Template body with prefill content inserted.
    """
    # Insert input_text into Background section
    body = body.replace(
        "{Brief context explaining why this feature/fix is needed}",
        input_text,
    )

    if prefill.get("file_refs"):
        body = body.replace(
            "| `path/to/file.py` | {Description of changes} |",
            "\n".join(f"| `{f}` | <!-- changes needed --> |" for f in prefill["file_refs"]),
        )

    if prefill.get("prd_deps"):
        body = body.replace(
            "| DEP-001 | {Dependency} | Resolved/Pending | Yes/No |",
            "\n".join(f"| DEP-{i:03d} | {dep} | Pending | Yes |" for i, dep in enumerate(prefill["prd_deps"], 1)),
        )

    return body


# ---------------------------------------------------------------------------
# Section filtering by category
# ---------------------------------------------------------------------------


def _filter_sections_for_category(body: str, category: str) -> str:
    """Remove numbered sections not required by the category variant.

    Splits the body on ``## N. Section Name`` boundaries and retains only
    those section blocks whose heading name appears in the required list for
    the given category. Non-numbered blocks (the preamble, Appendix, Quality
    Checklist) are always retained.

    Section numbers are re-assigned sequentially after filtering so the
    output is always numbered 1, 2, 3, ... without gaps.

    Args:
        body: Template body (post variable-substitution).
        category: PRD category (e.g. ``"FIX"``, ``"CORE"``).

    Returns:
        Body with only the required section blocks, renumbered.
    """
    from trw_mcp.state.validation.template_variants import get_required_sections

    required = set(get_required_sections(category))

    # Regex: split on lines that start a numbered section "## N. Heading"
    _NUMBERED_SECTION_RE = re.compile(r"^(## \d+\. .+)$", re.MULTILINE)

    # Split into (preamble | section_header | section_body) triples
    parts = _NUMBERED_SECTION_RE.split(body)
    # parts[0] is preamble (before first ## N. heading)
    # parts[1], parts[2], parts[3], parts[4], ... alternate header, body pairs

    preamble = parts[0]
    kept_sections: list[tuple[str, str]] = []  # (header_line, body_text)

    for i in range(1, len(parts) - 1, 2):
        header_line = parts[i]  # e.g. "## 3. User Stories"
        section_body = parts[i + 1] if i + 1 < len(parts) else ""

        # Extract section name: strip "## N. " prefix
        name_match = re.match(r"## \d+\. (.+)", header_line)
        if name_match:
            section_name = name_match.group(1).strip()
            if section_name in required:
                kept_sections.append((section_name, section_body))
        else:
            # Non-standard header -- keep as-is (shouldn't occur in practice)
            kept_sections.append((header_line, section_body))

    # Extract trailing non-numbered content (Appendix, Quality Checklist)
    # from the last numbered section's body.  Non-numbered ## headings
    # (e.g. "## Appendix") are always preserved regardless of category.
    trailing = ""
    if kept_sections or parts:
        # The last element in parts is the body after the final numbered heading.
        # It may contain non-numbered sections like ## Appendix.
        last_body = parts[-1] if len(parts) > 1 else ""
        # Find the first non-numbered ## heading in the last body
        _NON_NUMBERED_RE = re.compile(r"^(## (?!\d+\.).+)$", re.MULTILINE)
        m = _NON_NUMBERED_RE.search(last_body)
        if m:
            trailing = last_body[m.start() :]
            # Trim trailing from the last section body if it was kept
            if kept_sections:
                last_name, last_body_text = kept_sections[-1]
                cut_m = _NON_NUMBERED_RE.search(last_body_text)
                if cut_m:
                    kept_sections[-1] = (last_name, last_body_text[: cut_m.start()])

    # Rebuild with renumbered headings
    result_parts = [preamble]
    for idx, (section_name, section_body) in enumerate(kept_sections, start=1):
        result_parts.append(f"## {idx}. {section_name}{section_body}")

    if trailing:
        result_parts.append(trailing)

    return "".join(result_parts)


# ---------------------------------------------------------------------------
# PRD body generation (orchestrates the above helpers)
# ---------------------------------------------------------------------------


def _generate_prd_body(
    prd_id: str,
    title: str,
    input_text: str,
    category: str,
    priority: str = "P1",
    confidence: float = 0.7,
) -> str:
    """Generate PRD body content from template + input text.

    Loads the canonical template from ``data/prd_template.md``, substitutes
    variables, prefills content from the input text, and filters sections
    to only those required by the category variant (PRD-CORE-080-FR01).

    Args:
        prd_id: PRD identifier (e.g. ``PRD-CORE-007``).
        title: PRD title.
        input_text: Source text for the PRD.
        category: PRD category.
        priority: Priority level (P0-P3).
        confidence: Base confidence score derived from priority.

    Returns:
        Markdown body content with only the category-appropriate sections.
    """
    body = _load_template_body()
    body = _substitute_template(
        body,
        prd_id,
        title,
        category,
        int(prd_id.split("-")[-1]),
        priority,
        confidence,
    )
    body = _apply_prefill(body, _extract_prefill(input_text), input_text)
    # PRD-CORE-080-FR01: filter to only sections required by category variant
    body = _filter_sections_for_category(body, category)
    return body


# ---------------------------------------------------------------------------
# Frontmatter stripping and PRD rendering
# ---------------------------------------------------------------------------


def _strip_deprecated_fields(frontmatter: PrdFrontmatterDict) -> PrdFrontmatterDict:
    """Remove deprecated / null frontmatter fields before YAML output.

    Strips ``None``-valued top-level keys and explicitly removes decorative
    fields (``aaref_components``, ``conflicts_with``) that have 0% usage and
    add no value to new PRDs (PRD-CORE-080-FR07).

    Also removes ``conflicts_with`` from the nested ``traceability`` dict
    if present, since it is always empty and decorative.

    Existing PRDs that contain these fields remain parseable -- the Pydantic
    model accepts them as ``Optional`` for backward compatibility.

    Args:
        frontmatter: Raw frontmatter dict from ``model_to_dict``.

    Returns:
        Cleaned dict with deprecated/null fields removed.
    """
    _DEPRECATED_TOP_KEYS = frozenset({"aaref_components"})
    result: dict[str, object] = {
        k: v for k, v in frontmatter.items() if v is not None and k not in _DEPRECATED_TOP_KEYS
    }
    # Strip conflicts_with from nested traceability dict
    traceability = result.get("traceability")
    if isinstance(traceability, dict) and "conflicts_with" in traceability:
        result["traceability"] = {k: v for k, v in traceability.items() if k != "conflicts_with"}
    return cast("PrdFrontmatterDict", result)


def _render_prd(frontmatter: PrdFrontmatterDict, body: str) -> str:
    """Render complete PRD with YAML frontmatter and markdown body.

    Strips deprecated fields (``aaref_components``, ``conflicts_with``) and
    ``None``-valued keys from frontmatter before YAML serialization so that
    newly generated PRDs are clean and minimal (PRD-CORE-080-FR07).

    Args:
        frontmatter: Frontmatter dictionary to serialize as YAML.
        body: Markdown body content.

    Returns:
        Complete PRD document as a string.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump({"prd": _strip_deprecated_fields(frontmatter)}, stream)
    yaml_str = stream.getvalue()

    return f"---\n{yaml_str}---\n\n{body}\n"
