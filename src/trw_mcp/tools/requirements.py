"""TRW AARE-F requirements tools — prd_create, prd_validate, traceability_check.

These 3 tools codify the AARE-F Framework v1.1.0 requirements engineering
process as executable MCP tools.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import (
    EvidenceLevel,
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDQualityGates,
    PRDTraceability,
    Priority,
    TraceabilityResult,
    ValidationFailure,
    ValidationResult,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict
from trw_mcp.state.validation import validate_prd_quality

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()

# Section headings expected in an AARE-F compliant PRD
_EXPECTED_SECTIONS: list[str] = [
    "Problem Statement",
    "Goals & Non-Goals",
    "User Stories",
    "Functional Requirements",
    "Non-Functional Requirements",
    "Technical Approach",
    "Test Strategy",
    "Rollout Plan",
    "Success Metrics",
    "Dependencies & Risks",
    "Open Questions",
    "Traceability Matrix",
]


def register_requirements_tools(server: FastMCP) -> None:
    """Register all 3 AARE-F requirements tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_prd_create(
        input_text: str,
        category: str = "CORE",
        priority: str = "P1",
        title: str = "",
        sequence: int = 1,
    ) -> dict[str, object]:
        """Generate an AARE-F compliant PRD from a feature request or requirements text.

        Args:
            input_text: Feature request, requirements, or description to base the PRD on.
            category: PRD category (CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, FIX).
            priority: Priority level (P0, P1, P2, P3).
            title: PRD title. Auto-generated from input if not provided.
            sequence: Sequence number for PRD ID. Auto-increments from existing PRDs when default (1).
        """
        # Validate priority
        try:
            prd_priority = Priority(priority)
        except ValueError:
            valid = [p.value for p in Priority]
            raise ValidationError(
                f"Invalid priority: {priority!r}. Valid: {valid}",
                priority=priority,
            )

        # Auto-increment sequence when using default value (1)
        if sequence == 1:
            env_root = os.environ.get("TRW_PROJECT_ROOT")
            project_root_for_seq = (
                Path(env_root).resolve() if env_root else Path.cwd().resolve()
            )
            prds_dir_for_seq = (
                project_root_for_seq / "docs" / "requirements-aare-f" / "prds"
            )
            sequence = _next_sequence(prds_dir_for_seq, category.upper())

        # Generate PRD ID
        prd_id = f"PRD-{category.upper()}-{sequence:03d}"

        # Auto-generate title if not provided
        if not title:
            # Use first sentence or first 60 chars of input
            first_line = input_text.strip().split("\n")[0]
            title = first_line[:60].rstrip(".")

        # Map priority → base confidence score
        _priority_confidence: dict[str, float] = {
            "P0": 0.9, "P1": 0.7, "P2": 0.6, "P3": 0.5,
        }
        base_confidence = _priority_confidence.get(priority, 0.7)

        # Build frontmatter
        frontmatter = PRDFrontmatter(
            id=prd_id,
            title=title,
            version="1.0",
            priority=prd_priority,
            category=category.upper(),
            confidence=PRDConfidence(
                implementation_feasibility=base_confidence,
                requirement_clarity=base_confidence,
                estimate_confidence=max(base_confidence - 0.1, 0.4),
                test_coverage_target=0.85,
            ),
            evidence=PRDEvidence(
                level=EvidenceLevel.MODERATE,
                sources=["Input text analysis"],
            ),
            traceability=PRDTraceability(),
            quality_gates=PRDQualityGates(
                ambiguity_rate_max=_config.ambiguity_rate_max,
                completeness_min=_config.completeness_min,
                traceability_coverage_min=_config.traceability_coverage_min,
            ),
            dates=PRDDates(
                created=date.today(),
                updated=date.today(),
            ),
        )

        # Generate PRD body from template
        body = _generate_prd_body(
            prd_id, title, input_text, category,
            priority=priority, confidence=base_confidence,
        )

        # Combine frontmatter + body
        frontmatter_dict = model_to_dict(frontmatter)
        prd_content = _render_prd(frontmatter_dict, body)

        # Save to project if .trw/ exists
        output_path = ""
        env_root = os.environ.get("TRW_PROJECT_ROOT")
        project_root = Path(env_root).resolve() if env_root else Path.cwd().resolve()
        prds_dir = project_root / "docs" / "requirements-aare-f" / "prds"
        if prds_dir.exists() or (project_root / _config.trw_dir).exists():
            _writer.ensure_dir(prds_dir)
            prd_file = prds_dir / f"{prd_id}.md"
            prd_file.write_text(prd_content, encoding="utf-8")
            output_path = str(prd_file)

        logger.info(
            "trw_prd_created",
            prd_id=prd_id,
            category=category,
            priority=priority,
        )

        return {
            "prd_id": prd_id,
            "title": title,
            "category": category.upper(),
            "priority": priority,
            "output_path": output_path,
            "content": prd_content,
            "sections_generated": len(_EXPECTED_SECTIONS),
        }

    @server.tool()
    def trw_prd_validate(
        prd_path: str,
    ) -> dict[str, object]:
        """Validate a PRD against AARE-F quality gates — reports failures and scores.

        Args:
            prd_path: Path to the PRD markdown file to validate.
        """
        path = Path(prd_path).resolve()
        if not path.exists():
            raise StateError(f"PRD file not found: {path}", path=str(path))

        content = path.read_text(encoding="utf-8")

        # Parse YAML frontmatter
        frontmatter = _parse_frontmatter(content)

        # Extract section headings
        sections = _extract_sections(content)

        # Run validation
        gates = PRDQualityGates(
            ambiguity_rate_max=_config.ambiguity_rate_max,
            completeness_min=_config.completeness_min,
            traceability_coverage_min=_config.traceability_coverage_min,
        )
        result = validate_prd_quality(frontmatter, sections, gates)

        # Check for ambiguous terms
        ambiguous_terms = _detect_ambiguity(content)
        if ambiguous_terms:
            total_words = len(content.split())
            ambiguity_rate = len(ambiguous_terms) / max(total_words, 1)
            result.ambiguity_rate = ambiguity_rate
            if ambiguity_rate > _config.ambiguity_rate_max:
                result.failures.append(
                    ValidationFailure(
                        field="content",
                        rule="ambiguity_rate",
                        message=f"Ambiguity rate {ambiguity_rate:.2%} exceeds {_config.ambiguity_rate_max:.0%} threshold",
                        severity="warning",
                    )
                )

        logger.info(
            "trw_prd_validated",
            path=str(path),
            valid=result.valid,
            failures=len(result.failures),
        )

        return {
            "path": str(path),
            "valid": result.valid,
            "completeness_score": result.completeness_score,
            "traceability_coverage": result.traceability_coverage,
            "ambiguity_rate": result.ambiguity_rate,
            "sections_found": sections,
            "sections_expected": _EXPECTED_SECTIONS,
            "ambiguous_terms": ambiguous_terms,
            "failures": [
                {
                    "field": f.field,
                    "rule": f.rule,
                    "message": f.message,
                    "severity": f.severity,
                }
                for f in result.failures
            ],
        }

    @server.tool()
    def trw_traceability_check(
        prd_path: str | None = None,
        source_dir: str | None = None,
    ) -> dict[str, object]:
        """Verify requirement traceability coverage across PRDs and source code.

        Args:
            prd_path: Path to specific PRD file, or None to scan all PRDs.
            source_dir: Source directory to check for implementations.
        """
        env_root = os.environ.get("TRW_PROJECT_ROOT")
        project_root = Path(env_root).resolve() if env_root else Path.cwd().resolve()

        # Collect PRDs
        prd_files: list[Path] = []
        if prd_path:
            prd_files.append(Path(prd_path).resolve())
        else:
            prds_dir = project_root / "docs" / "requirements-aare-f" / "prds"
            if prds_dir.exists():
                prd_files = [
                    f for f in sorted(prds_dir.glob("*.md"))
                    if f.name != "TEMPLATE.md"
                ]

        if not prd_files:
            return {
                "total_requirements": 0,
                "traced_requirements": 0,
                "coverage": 0.0,
                "message": "No PRD files found to analyze",
            }

        # Extract requirements and their traces
        total_reqs = 0
        traced_reqs = 0
        untraced: list[str] = []

        for prd_file in prd_files:
            if not prd_file.exists():
                continue
            content = prd_file.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(content)

            # Count requirements from frontmatter traceability
            trace_data = frontmatter.get("traceability", {})
            if isinstance(trace_data, dict):
                implements = trace_data.get("implements", [])
                if isinstance(implements, list) and implements:
                    traced_reqs += 1
                    total_reqs += 1
                else:
                    total_reqs += 1
                    prd_id = str(frontmatter.get("id", prd_file.stem))
                    untraced.append(prd_id)

            # Count FR requirements in body
            fr_pattern = r"###\s+\S+-FR\d+"
            fr_matches = re.findall(fr_pattern, content)
            total_reqs += len(fr_matches)

            # Check traceability matrix section
            if "Traceability Matrix" in content:
                # Count rows with implementation links
                matrix_section = content.split("Traceability Matrix")[-1]
                impl_refs = re.findall(r"`\w+\.py[:\w]*`", matrix_section)
                traced_reqs += min(len(impl_refs), len(fr_matches))

        total_reqs = max(total_reqs, 1)
        coverage = traced_reqs / total_reqs

        result = TraceabilityResult(
            total_requirements=total_reqs,
            traced_requirements=traced_reqs,
            untraced_requirements=untraced,
            coverage=coverage,
        )

        logger.info(
            "trw_traceability_checked",
            total=total_reqs,
            traced=traced_reqs,
            coverage=f"{coverage:.0%}",
        )

        return {
            "total_requirements": result.total_requirements,
            "traced_requirements": result.traced_requirements,
            "untraced_requirements": result.untraced_requirements,
            "coverage": result.coverage,
            "coverage_threshold": _config.traceability_coverage_min,
            "passes_gate": coverage >= _config.traceability_coverage_min,
            "prd_files_analyzed": len(prd_files),
        }


# --- Private helpers ---


def _parse_frontmatter(content: str) -> dict[str, object]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Markdown content with optional YAML frontmatter between --- delimiters.

    Returns:
        Parsed YAML frontmatter dictionary, or empty dict if none found.
    """
    frontmatter_pattern = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
    match = frontmatter_pattern.match(content)
    if not match:
        return {}

    from ruamel.yaml import YAML
    yaml = YAML()
    try:
        data = yaml.load(match.group(1))
        if isinstance(data, dict):
            # Flatten nested 'prd' key if present (AARE-F template nests under 'prd')
            if "prd" in data and isinstance(data["prd"], dict):
                prd_data: dict[str, object] = dict(data["prd"])
                # Merge other top-level keys
                for key, val in data.items():
                    if key != "prd":
                        prd_data[key] = val
                return prd_data
            return dict(data)
    except Exception:
        pass
    return {}


def _extract_sections(content: str) -> list[str]:
    """Extract ## section headings from PRD markdown content.

    Args:
        content: Markdown content.

    Returns:
        List of section heading names found.
    """
    # Match ## N. Section Name pattern
    heading_pattern = re.compile(r"^##\s+\d+\.\s+(.+)$", re.MULTILINE)
    matches = heading_pattern.findall(content)
    return matches


def _detect_ambiguity(content: str) -> list[str]:
    """Detect ambiguous terms in PRD content.

    Args:
        content: PRD markdown content.

    Returns:
        List of ambiguous terms found.
    """
    ambiguous_patterns = [
        "fast", "quick", "efficient", "user-friendly", "robust",
        "scalable", "flexible", "easy", "simple", "intuitive",
        "adequate", "sufficient", "as appropriate", "etc.",
        "and so on", "various", "multiple", "many",
    ]
    found: list[str] = []
    content_lower = content.lower()
    for term in ambiguous_patterns:
        # Match whole words only
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, content_lower):
            found.append(term)
    return found


def _generate_prd_body(
    prd_id: str,
    title: str,
    input_text: str,
    category: str,
    priority: str = "P1",
    confidence: float = 0.7,
) -> str:
    """Generate PRD body content from input text.

    Args:
        prd_id: PRD identifier.
        title: PRD title.
        input_text: Source text for the PRD.
        category: PRD category.
        priority: Priority level (P0-P3).
        confidence: Base confidence score derived from priority.

    Returns:
        Markdown body content with 12 sections.
    """
    return f"""# {prd_id}: {title}

**Quick Reference**:
- **Status**: Draft
- **Priority**: {priority}
- **Evidence**: Moderate
- **Implementation Confidence**: {confidence}

---

## 1. Problem Statement

### Background
{input_text}

### Problem
<!-- Specify the core problem being solved -->

### Impact
<!-- Who is affected and how -->

---

## 2. Goals & Non-Goals

### Goals
- [ ] <!-- Goal 1 - specific, measurable -->

### Non-Goals
- <!-- What this PRD explicitly does NOT address -->

---

## 3. User Stories

### US-001: Primary User Story
**As a** user
**I want** <!-- capability -->
**So that** <!-- benefit -->

**Confidence Expectation**: medium
**Evidence Required**: <!-- What validates this story -->
**Uncertainty Notes**: <!-- Known unknowns -->

**Acceptance Criteria**:
- [ ] Given <!-- context -->, When <!-- action -->, Then <!-- outcome --> `[confidence: 0.8]`

---

## 4. Functional Requirements

### {prd_id}-FR01: <!-- Requirement Title -->
**Priority**: Must Have
**Description**: <!-- Detailed description -->
**Acceptance**: <!-- Testable criteria -->
**Dependencies**: None
**Confidence**: 0.8

---

## 5. Non-Functional Requirements

### {prd_id}-NFR01: Performance
- <!-- Response time targets -->

### {prd_id}-NFR02: Reliability
- <!-- Error handling requirements -->

---

## 6. Technical Approach

### Architecture Impact
<!-- How this affects existing architecture -->

### Key Files
| File | Changes |
|------|---------|
| <!-- path --> | <!-- description --> |

---

## 7. Test Strategy

### Unit Tests
- [ ] <!-- Test case -->

### Integration Tests
- [ ] <!-- Integration test -->

---

## 8. Rollout Plan

### Phase 1: Development
- <!-- Tasks -->

### Phase 2: Testing
- <!-- Tasks -->

### Rollback Plan
<!-- How to revert if issues arise -->

---

## 9. Success Metrics

| Metric | Target | Measurement Method | Confidence |
|--------|--------|-------------------|------------|
| <!-- Metric --> | <!-- Target --> | <!-- Method --> | 0.8 |

---

## 10. Dependencies & Risks

### Dependencies
| ID | Description | Status | Blocking |
|----|-------------|--------|----------|
| DEP-001 | <!-- Dependency --> | Pending | No |

### Risks
| ID | Risk | Probability | Impact | Mitigation | Residual Risk |
|----|------|-------------|--------|------------|---------------|
| RISK-001 | <!-- Risk --> | Low | Medium | <!-- Mitigation --> | Low |

---

## 11. Open Questions

- [ ] <!-- Question --> `[blocking: no]`

---

## 12. Traceability Matrix

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | Input text | <!-- impl --> | <!-- test --> | Pending |

### Knowledge Entry Links
- **Implements**: <!-- KE entries -->
- **Informs**: <!-- KE entries that informed this PRD -->
"""


def _next_sequence(prds_dir: Path, category: str) -> int:
    """Scan existing PRD files and return max sequence + 1 for the given category.

    Args:
        prds_dir: Directory containing PRD markdown files.
        category: PRD category (e.g. 'CORE', 'FIX').

    Returns:
        Next available sequence number (minimum 1).
    """
    max_seq = 0
    prefix = f"PRD-{category}-"
    if prds_dir.exists():
        for prd_file in prds_dir.glob("*.md"):
            name = prd_file.stem  # e.g. "PRD-CORE-001"
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                try:
                    seq = int(suffix)
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    continue
    return max_seq + 1


def _render_prd(frontmatter: dict[str, object], body: str) -> str:
    """Render complete PRD with YAML frontmatter and markdown body.

    Args:
        frontmatter: Frontmatter dictionary to serialize as YAML.
        body: Markdown body content.

    Returns:
        Complete PRD document as a string.
    """
    from io import StringIO
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump({"prd": frontmatter}, stream)
    yaml_str = stream.getvalue()

    return f"---\n{yaml_str}---\n\n{body}\n"
