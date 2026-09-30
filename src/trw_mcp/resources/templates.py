"""Template resources — PRD template, shard card template, AHR schema and AHR examples."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)

_AHR_DATA = Path(__file__).parent.parent / "data" / "ahr"


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

    @server.resource("trw://schemas/ahr/v1", mime_type="application/schema+json")
    def get_ahr_schema() -> str:
        """Agent Handoff Record 1.0-rc.1 JSON Schema (2020-12), for offline validation.

        Validate, digest or seal a record with `trw-mcp handoff`.
        """
        # PRD-CORE-347-FR04: the packaged file text, unchanged (pinned by the parity test).
        return (_AHR_DATA / "ahr.schema.json").read_text(encoding="utf-8")

    @server.resource("trw://templates/ahr", mime_type="application/json")
    def get_ahr_templates() -> str:
        """Example Agent Handoff Records per tier (minimal, standard) to copy and edit.

        Seal the edited copy with `trw-mcp handoff seal FILE`. The JSON record is normative.
        """
        # PRD-CORE-348-FR02: no critical example ships until PRD-EVAL-078 reports (operator decision 3).
        examples = {
            tier: json.loads((_AHR_DATA / "examples" / f"{tier}.json").read_text(encoding="utf-8"))
            for tier in ("minimal", "standard")
        }
        return json.dumps({"schema": "trw://schemas/ahr/v1", "examples": examples}, indent=2, ensure_ascii=False)


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
