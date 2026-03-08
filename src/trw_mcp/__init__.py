"""TRW Framework MCP Server — orchestration, requirements, and self-learning tools."""

from importlib.metadata import version as _pkg_version

__version__: str = _pkg_version("trw-mcp")

__all__ = ["__version__"]
