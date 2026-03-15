"""AARE-F prompts registered as MCP prompts.

These 5 prompts expose AARE-F requirements engineering workflows
as Claude Code slash commands via MCP prompt registration.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

_DATA_DIR = Path(__file__).parent.parent / "data" / "prompts"


def _load_prompt_template(filename: str) -> str:
    """Load a prompt template from the bundled data directory.

    Args:
        filename: Template filename in data/prompts/.

    Returns:
        Template content as string, or fallback message if not found.
    """
    path = _DATA_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Template {filename} not found. Ensure trw-mcp data files are installed."


def register_aaref_prompts(server: FastMCP) -> None:
    """Register all 5 AARE-F prompts on the MCP server.

    Args:
        server: FastMCP server instance to register prompts on.
    """

    @server.prompt()
    def elicit(
        source_type: str = "documentation",
        content: str = "",
    ) -> str:
        """Extract and structure requirements from documentation, interviews, or code.

        AARE-F requirements elicitation — analyzes source material and
        produces structured requirements with IDs, confidence scores,
        and traceability links.

        Args:
            source_type: Type of source (documentation, interview, code, gap_analysis).
            content: Source content to analyze for requirements.

        Returns:
            Elicitation prompt with embedded source content.
        """
        template = _load_prompt_template("elicitation.md")
        return f"{template}\n\n## Source Type: {source_type}\n\n## Content to Analyze\n\n{content}"

    @server.prompt()
    def prd_create(
        requirements: str = "",
        category: str = "CORE",
        project_name: str = "",
    ) -> str:
        """Generate an AARE-F compliant PRD from requirements or feature description.

        Creates a complete PRD with YAML frontmatter, 12 required sections,
        confidence scores, and traceability matrix.

        Args:
            requirements: Input requirements or feature description.
            category: PRD category (CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH).
            project_name: Project name for PRD context.

        Returns:
            PRD creation prompt with embedded requirements.
        """
        template = _load_prompt_template("prd_creation.md")
        return (
            f"{template}\n\n"
            f"## Project: {project_name}\n"
            f"## Category: {category}\n\n"
            f"## Input Requirements\n\n{requirements}"
        )

    @server.prompt()
    def validate_quality(
        prd_content: str = "",
    ) -> str:
        """Validate a PRD against AARE-F quality gates — ambiguity, completeness, consistency.

        Performs comprehensive quality audit including ambiguity detection,
        completeness assessment, consistency checking, and traceability
        verification.

        Args:
            prd_content: PRD content to validate.

        Returns:
            Quality validation prompt with embedded PRD content.
        """
        template = _load_prompt_template("quality_validation.md")
        return f"{template}\n\n## PRD to Validate\n\n{prd_content}"

    @server.prompt()
    def resolve_conflicts(
        requirements: str = "",
        conflict_description: str = "",
    ) -> str:
        """Detect and resolve requirement conflicts using AARE-F strategies.

        Applies risk-based resolution, AHP-TOPSIS scoring, or IBIS
        structured argumentation depending on conflict type and severity.

        Args:
            requirements: Conflicting requirements text.
            conflict_description: Description of the conflict.

        Returns:
            Conflict resolution prompt with embedded requirements.
        """
        template = _load_prompt_template("conflict_resolution.md")
        return f"{template}\n\n## Conflict Description\n\n{conflict_description}\n\n## Requirements\n\n{requirements}"

    @server.prompt()
    def check_traceability(
        requirements: str = "",
        implementation_refs: str = "",
    ) -> str:
        """Analyze traceability coverage — source, implementation, test, and KE links.

        Identifies traceability gaps, orphan implementations, and missing
        test coverage against AARE-F C1 standards.

        Args:
            requirements: Requirements with current traceability data.
            implementation_refs: Implementation file references.

        Returns:
            Traceability analysis prompt with embedded data.
        """
        template = _load_prompt_template("traceability.md")
        return (
            f"{template}\n\n## Requirements\n\n{requirements}\n\n## Implementation References\n\n{implementation_refs}"
        )
