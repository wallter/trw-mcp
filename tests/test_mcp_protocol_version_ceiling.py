"""PRD-CORE-317-FR01: the installed mcp SDK must not yet speak the 2026-07-28 protocol.

trw-mcp pins ``mcp>=1.26.0`` and trw-memory pins ``fastmcp>=3.2.0,<4.0.0``. Both
pins currently admit only pre-2026-07-28 protocol versions. If a future patch
release backports 2026-07-28 support (or the pin is loosened), this test turns
red the moment that happens -- forcing a deliberate review of the compatibility
matrix in ``docs/requirements-aare-f/prds/PRD-CORE-317.md`` section 6, rather
than the gap being discovered by an unrelated runtime failure.

NFR01: this module imports only ``mcp.shared.version`` (a third-party SDK
module) at test time -- never ``trw_mcp.server`` or ``trw_memory.daemon``
production modules -- so it adds no runtime cost to either server's boot path.
"""

from __future__ import annotations

import pytest
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

#: Pure logic, no filesystem I/O or subprocess -- unit tier (tiering is by
#: explicit marker only; see conftest.py's Test Tiering Philosophy docstring).
pytestmark = pytest.mark.unit

#: The protocol revision this PRD assesses; must stay absent from the
#: installed SDK's supported-version list until a reviewed upgrade lands.
_UNSUPPORTED_REVISION = "2026-07-28"

_MATRIX_POINTER = (
    "PRD-CORE-317 (docs/requirements-aare-f/prds/PRD-CORE-317.md section 6, "
    "the 2026-07-28 changelog matrix) must be re-reviewed before this pin is "
    "loosened or a patch release that adds 2026-07-28 support is accepted."
)


def _assert_ceiling_holds(supported_versions: list[str]) -> None:
    """Shared assertion so the guard body and its own negative test run identical logic."""
    if _UNSUPPORTED_REVISION in supported_versions:
        pytest.fail(
            f"The installed mcp SDK now supports protocol revision "
            f"{_UNSUPPORTED_REVISION!r} (SUPPORTED_PROTOCOL_VERSIONS="
            f"{supported_versions!r}). {_MATRIX_POINTER}"
        )


def test_installed_protocol_ceiling_excludes_2026_07_28() -> None:
    """The real, installed SDK does not support 2026-07-28 today."""
    assert isinstance(SUPPORTED_PROTOCOL_VERSIONS, list)
    assert SUPPORTED_PROTOCOL_VERSIONS, "installed mcp SDK reported an empty SUPPORTED_PROTOCOL_VERSIONS"
    _assert_ceiling_holds(list(SUPPORTED_PROTOCOL_VERSIONS))


def test_ceiling_guard_fails_loudly_once_2026_07_28_is_supported() -> None:
    """Non-vacuity + failure-message check: simulate the future SDK state.

    Monkeypatches the imported list to include ``2026-07-28`` (as a future
    dependency bump would) and asserts the guard's own failure path fires with
    a message naming PRD-CORE-317 and the matrix location -- proving the test
    is the mechanism that turns red, not a person noticing.
    """
    future_versions = ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", _UNSUPPORTED_REVISION]

    with pytest.raises(pytest.fail.Exception) as excinfo:
        _assert_ceiling_holds(future_versions)

    message = str(excinfo.value)
    assert "PRD-CORE-317" in message
    assert "PRD-CORE-317.md" in message
    assert "2026-07-28" in message
