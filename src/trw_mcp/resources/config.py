"""Framework configuration resource — exposes current config via MCP."""

from __future__ import annotations

from io import StringIO

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)
from ruamel.yaml import YAML

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.memory_adapter import list_active_learnings
from trw_mcp.state.persistence import FileStateReader, model_to_dict


def _dump_yaml(data: dict[str, object]) -> str:
    """Serialize a dict to YAML text.

    Creates a fresh YAML instance per call for thread safety
    (PRD-CORE-014 FR03).
    """
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()


def register_config_resources(server: FastMCP) -> None:
    """Register config resource on the MCP server.

    Args:
        server: FastMCP server instance to register resources on.
    """

    @server.resource("trw://framework/config")
    def get_framework_config() -> str:
        """Current framework config — defaults merged with .trw/config.yaml overrides.

        Returns merged configuration as YAML text. Project-level overrides
        from .trw/config.yaml take precedence over built-in defaults.
        """
        config = get_config()
        reader = FileStateReader()
        project_root = resolve_project_root()
        config_path = project_root / config.trw_dir / "config.yaml"

        result = model_to_dict(config)

        # Merge project overrides
        if reader.exists(config_path):
            overrides = reader.read_yaml(config_path)
            result.update(overrides)

        return _dump_yaml(result)

    @server.resource("trw://framework/versions")
    def get_framework_versions() -> str:
        """Deployed framework versions from .trw/frameworks/VERSION.yaml.

        Returns version information for deployed FRAMEWORK.md and
        AARE-F-FRAMEWORK.md, including trw-mcp package version
        and deployment timestamp.
        """
        config = get_config()
        reader = FileStateReader()
        project_root = resolve_project_root()
        version_path = project_root / config.trw_dir / config.frameworks_dir / "VERSION.yaml"

        if not reader.exists(version_path):
            return "# No frameworks deployed yet\n# Run trw_init to deploy.\n"

        data = reader.read_yaml(version_path)
        return _dump_yaml(dict(data))

    @server.resource("trw://learnings/summary")
    def get_learnings_summary() -> str:
        """High-impact learnings summary from .trw/ — top insights for current session.

        Returns a formatted summary of high-impact learnings, discovered
        patterns, and context (architecture + conventions) from .trw/.
        """
        config = get_config()
        reader = FileStateReader()
        project_root = resolve_project_root()
        trw_dir = project_root / config.trw_dir

        lines: list[str] = ["# TRW Learnings Summary\n"]

        # High-impact learnings (SQLite-backed via memory_adapter)
        high_impact = list_active_learnings(trw_dir, min_impact=0.7, limit=10)
        if high_impact:
            lines.append("## High-Impact Learnings\n")
            for entry in high_impact:
                summary = entry.get("summary", "")
                detail = entry.get("detail", "")
                lines.append(f"- **{summary}**: {detail}\n")

        # Patterns
        patterns_dir = trw_dir / config.patterns_dir
        if patterns_dir.exists():
            lines.append("\n## Discovered Patterns\n")
            for pattern_file in sorted(patterns_dir.glob("*.yaml")):
                if pattern_file.name == "index.yaml":
                    continue
                try:
                    data = reader.read_yaml(pattern_file)
                    name = data.get("name", "")
                    desc = data.get("description", "")
                    lines.append(f"- **{name}**: {desc}\n")
                except (StateError, ValueError, TypeError):
                    continue

        # Analytics
        analytics_path = trw_dir / config.context_dir / "analytics.yaml"
        if reader.exists(analytics_path):
            data = reader.read_yaml(analytics_path)
            lines.append("\n## Analytics\n")
            lines.append(f"- Sessions tracked: {data.get('sessions_tracked', 0)}\n")
            lines.append(f"- Total learnings: {data.get('total_learnings', 0)}\n")
            lines.append(f"- Avg per session: {data.get('avg_learnings_per_session', 0)}\n")

        return "".join(lines)
