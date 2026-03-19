"""F05: Verify api.scoring import has no TRWConfig side effects.

Importing trw_mcp.api.scoring must NOT trigger TRWConfig() instantiation.
This prevents module-level singleton creation that would break test isolation
and cause expensive config file lookups on import.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch


def test_api_import_no_side_effects() -> None:
    """Importing trw_mcp.api.scoring must NOT call TRWConfig().__init__."""
    # Clear cached modules to force fresh import chain (Final Audit Fix B)
    mods_to_clear = [k for k in sys.modules if k.startswith("trw_mcp.api")]
    for mod in mods_to_clear:
        del sys.modules[mod]

    with patch(
        "trw_mcp.models.config._main.TRWConfig.__init__",
        return_value=None,
    ) as mock_init:
        importlib.import_module("trw_mcp.api.scoring")
        mock_init.assert_not_called()
