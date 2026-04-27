"""Documentation-facing helpers for TRW client profiles."""

from trw_mcp.client_profiles.catalog import ClientProfileDocRow, build_client_profile_rows, tool_preset_counts
from trw_mcp.client_profiles.markdown import render_matrix_page, render_quick_reference_table

__all__ = [
    "ClientProfileDocRow",
    "build_client_profile_rows",
    "render_matrix_page",
    "render_quick_reference_table",
    "tool_preset_counts",
]
