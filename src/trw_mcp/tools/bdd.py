"""TRW BDD generation tool — Gherkin scenario generation from PRD acceptance criteria.

PRD-CORE-005: Provides trw_bdd_generate tool for the MCP server.
"""

from __future__ import annotations

import structlog
from fastmcp import FastMCP

from trw_mcp.state.bdd import run_bdd_pipeline

logger = structlog.get_logger()


def register_bdd_tools(server: FastMCP) -> None:
    """Register BDD generation tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_bdd_generate(
        prd_path: str,
        output_dir: str | None = None,
        include_background: bool = False,
        confidence_threshold: float = 0.0,
    ) -> dict[str, object]:
        """Generate BDD scenarios from PRD acceptance criteria.

        6-stage pipeline: parse PRD → extract FRs/ACs → classify EARS →
        generate scenarios → validate structure → render .feature file.

        Args:
            prd_path: Path to PRD markdown file.
            output_dir: Directory for .feature output (default: same as PRD).
            include_background: Extract shared Given steps to Background section.
            confidence_threshold: Minimum confidence to include scenario (0.0-1.0).
        """
        result = run_bdd_pipeline(
            prd_path=prd_path,
            output_dir=output_dir,
            include_background=include_background,
            confidence_threshold=confidence_threshold,
        )

        logger.info(
            "trw_bdd_generate_complete",
            prd_id=result.prd_id,
            scenarios=result.scenarios_generated,
            frs=result.frs_extracted,
            acs=result.acs_extracted,
        )

        return {
            "status": "complete",
            "prd_id": result.prd_id,
            "prd_title": result.prd_title,
            "feature_file": result.feature_file,
            "scenarios_generated": result.scenarios_generated,
            "frs_extracted": result.frs_extracted,
            "acs_extracted": result.acs_extracted,
            "structured_acs": result.structured_acs,
            "unstructured_acs": result.unstructured_acs,
            "validation_errors": result.validation_errors,
            "warnings": result.warnings,
        }
