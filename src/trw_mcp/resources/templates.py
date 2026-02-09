"""Template resources — PRD template and shard card template."""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP


def register_template_resources(server: FastMCP) -> None:
    """Register template resources on the MCP server.

    Args:
        server: FastMCP server instance to register resources on.
    """

    @server.resource("trw://templates/prd")
    def get_prd_template() -> str:
        """AARE-F PRD template — YAML frontmatter + 12 sections with quality checklist.

        Returns the full AARE-F v1.1.0 compliant PRD template ready for
        filling in. Includes confidence scores, traceability matrix,
        and quality checklist.
        """
        data_dir = Path(__file__).parent.parent / "data"
        template_path = data_dir / "prd_template.md"
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")

        # Fallback inline template
        return _INLINE_PRD_TEMPLATE

    @server.resource("trw://templates/shard-card")
    def get_shard_card_template() -> str:
        """Shard card YAML template — defines parallel work unit structure.

        Returns a YAML template for shard cards as defined in
        FRAMEWORK.md v18.0_TRW section SHARD-CARDS.
        """
        return _SHARD_CARD_TEMPLATE


_INLINE_PRD_TEMPLATE = """---
prd:
  id: PRD-{CATEGORY}-{SEQUENCE}
  title: "{Title}"
  version: "1.0"
  status: draft
  priority: P1

aaref_components: []

evidence:
  level: moderate
  sources: []

confidence:
  implementation_feasibility: 0.8
  requirement_clarity: 0.8
  estimate_confidence: 0.7
  test_coverage_target: 0.85

traceability:
  implements: []
  depends_on: []
  enables: []
  conflicts_with: []

metrics:
  success_criteria: []
  measurement_method: []

dates:
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  target_completion: null

quality_gates:
  ambiguity_rate_max: 0.05
  completeness_min: 0.85
  traceability_coverage_min: 0.90
---

# PRD-{CATEGORY}-{SEQUENCE}: {Title}

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

_SHARD_CARD_TEMPLATE = """# Shard Card Template (FRAMEWORK.md v18.0_TRW)

id: shard-001
title: "Brief description"
wave: 1
goals:
  - "Goal 1"
planned_outputs:
  - "output.yaml"
output_contract:
  file: "scratch/shard-001/result.yaml"
  keys:
    - summary
    - findings
  required: true
  optional_keys:
    - recommendations
input_refs: []
self_decompose: true
max_child_depth: 2
confidence: medium  # high | medium | low
"""
