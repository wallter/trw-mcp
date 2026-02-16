"""Framework configuration resource — exposes current config via MCP."""

from __future__ import annotations

from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateReader, model_to_dict

_config = get_config()
_reader = FileStateReader()


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
        project_root = resolve_project_root()
        config_path = project_root / _config.trw_dir / "config.yaml"

        result = model_to_dict(_config)

        # Merge project overrides
        if _reader.exists(config_path):
            overrides = _reader.read_yaml(config_path)
            for key, value in overrides.items():
                if isinstance(key, str):
                    result[key] = value

        from io import StringIO
        from ruamel.yaml import YAML

        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(result, stream)
        return stream.getvalue()

    @server.resource("trw://framework/versions")
    def get_framework_versions() -> str:
        """Deployed framework versions from .trw/frameworks/VERSION.yaml.

        Returns version information for deployed FRAMEWORK.md and
        AARE-F-FRAMEWORK.md, including trw-mcp package version
        and deployment timestamp.
        """
        project_root = resolve_project_root()
        version_path = (
            project_root / _config.trw_dir / _config.frameworks_dir / "VERSION.yaml"
        )

        if not _reader.exists(version_path):
            return "# No frameworks deployed yet\n# Run trw_init to deploy.\n"

        from io import StringIO
        from ruamel.yaml import YAML

        data = _reader.read_yaml(version_path)
        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(dict(data), stream)
        return stream.getvalue()

    @server.resource("trw://learnings/summary")
    def get_learnings_summary() -> str:
        """High-impact learnings summary from .trw/ — top insights for current session.

        Returns a formatted summary of high-impact learnings, discovered
        patterns, and context (architecture + conventions) from .trw/.
        """
        project_root = resolve_project_root()
        trw_dir = project_root / _config.trw_dir

        lines: list[str] = ["# TRW Learnings Summary\n"]

        # High-impact learnings
        entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
        if entries_dir.exists():
            high_impact: list[dict[str, object]] = []
            for entry_file in sorted(entries_dir.glob("*.yaml")):
                try:
                    data = _reader.read_yaml(entry_file)
                    impact = data.get("impact", 0.0)
                    if isinstance(impact, (int, float)) and impact >= 0.7:
                        high_impact.append(data)
                except (StateError, ValueError, TypeError):
                    continue

            if high_impact:
                lines.append("## High-Impact Learnings\n")
                for entry in high_impact[:10]:
                    summary = entry.get("summary", "")
                    detail = entry.get("detail", "")
                    lines.append(f"- **{summary}**: {detail}\n")

        # Patterns
        patterns_dir = trw_dir / _config.patterns_dir
        if patterns_dir.exists():
            lines.append("\n## Discovered Patterns\n")
            for pattern_file in sorted(patterns_dir.glob("*.yaml")):
                if pattern_file.name == "index.yaml":
                    continue
                try:
                    data = _reader.read_yaml(pattern_file)
                    name = data.get("name", "")
                    desc = data.get("description", "")
                    lines.append(f"- **{name}**: {desc}\n")
                except (StateError, ValueError, TypeError):
                    continue

        # Analytics
        analytics_path = trw_dir / _config.context_dir / "analytics.yaml"
        if _reader.exists(analytics_path):
            data = _reader.read_yaml(analytics_path)
            lines.append("\n## Analytics\n")
            lines.append(f"- Sessions tracked: {data.get('sessions_tracked', 0)}\n")
            lines.append(f"- Total learnings: {data.get('total_learnings', 0)}\n")
            lines.append(f"- Avg per session: {data.get('avg_learnings_per_session', 0)}\n")

        return "".join(lines)
