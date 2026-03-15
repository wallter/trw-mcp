"""Vulture whitelist for trw-mcp package.

Items listed here are used at runtime but appear unused to static analysis.
Vulture uses these entries to suppress false positives.
"""

# --- Pydantic BaseModel / TypedDict fields ---
# Pydantic model fields defined with Field() or as class-level annotations
# are used dynamically via serialization/validation, not direct attribute access.
# Vulture cannot trace this usage.
model_config = None  # noqa: Pydantic model_config class var
model_post_init = None  # noqa: Pydantic __init__ hook
model_validator = None  # noqa: Pydantic decorator
field_validator = None  # noqa: Pydantic decorator
model_dump = None  # noqa: Pydantic method

# --- FastMCP @mcp.tool() decorated functions ---
# These functions are registered via decorator and called by the MCP protocol,
# not via direct Python call. Vulture sees them as unused.
# The server.py registers them at startup.

# --- CLI entrypoints (pyproject.toml [project.scripts]) ---
# main() functions in server modules are called by the CLI entrypoint,
# not by direct import.
main = None

# --- structlog / logging ---
# Structured logging bound methods
log = None
msg = None

# --- pytest fixtures ---
# Fixtures used by tests via dependency injection, not direct call.
tmp_path = None

# --- TypedDict keys ---
# TypedDict fields are used via dict-style access, not attribute access.

# --- Pydantic Settings ---
# Settings classes read from environment variables at runtime.
