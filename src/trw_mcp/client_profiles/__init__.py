"""Documentation-facing helpers for TRW client profiles.

The Markdown renderers that consumed these rows moved to
``scripts/render_client_profile_docs.py`` (PRD-CORE-313 FR05); the rows stay here
because they are derived from the runtime registry.
"""

from trw_mcp.client_profiles.catalog import ClientProfileDocRow, build_client_profile_rows

__all__ = [
    "ClientProfileDocRow",
    "build_client_profile_rows",
]
