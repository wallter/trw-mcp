"""Template resources — PRD template and shard card template."""

from __future__ import annotations

from pathlib import Path

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)


def register_template_resources(server: FastMCP) -> None:
    """Register template resources on the MCP server.

    Args:
        server: FastMCP server instance to register resources on.
    """

    @server.resource("trw://templates/prd")
    def get_prd_template() -> str:
        """AARE-F PRD template — YAML frontmatter + 12 sections with quality checklist.

        Returns the full AARE-F-compliant PRD template ready for
        filling in. Includes confidence scores, traceability matrix,
        and quality checklist.
        """
        data_dir = Path(__file__).parent.parent / "data"
        template_path = data_dir / "prd_template.md"
        if not template_path.is_file():
            raise FileNotFoundError(f"canonical AARE-F PRD template is unavailable: {template_path}")
        body = template_path.read_text(encoding="utf-8")
        if (
            not body.startswith("---\n")
            or 'template_version: "3.2"' not in body
            or "*Template version: 3.2 " not in body
        ):
            raise ValueError(f"canonical AARE-F PRD template is malformed or not version 3.2: {template_path}")
        return body

    @server.resource("trw://templates/shard-card")
    def get_shard_card_template() -> str:
        """Shard card YAML template — defines parallel work unit structure.

        Returns a YAML template for shard cards as defined in
        FRAMEWORK.md v18.0_TRW section SHARD-CARDS.
        """
        return _SHARD_CARD_TEMPLATE


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
