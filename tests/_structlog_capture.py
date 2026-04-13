"""Shared capture_logs fixture that survives sibling tests' configure_logging() calls.

trw-mcp's ``_logging.py::configure_logging`` calls
``structlog.configure(wrapper_class=make_filtering_bound_logger(...))``,
replacing the default wrapper class with a filtering variant. That state
persists process-wide after any test that exercises the CLI / server startup
path. When a later test enters ``structlog.testing.capture_logs()``, the
LogCapture processor is installed but the already-bound filtering wrapper
continues to drop events before they reach processors — yielding empty
``logs`` lists and false test failures.

Use the ``captured_structlog`` fixture in this module when asserting that a
specific structlog event was emitted. The fixture resets structlog to a
capture-friendly default for the duration of the test, then restores the
prior config.

Example::

    from tests._structlog_capture import captured_structlog  # noqa: F401

    def test_my_event_emits(captured_structlog: list[dict]) -> None:
        my_function_under_test()
        assert any(e.get("event") == "my_event" for e in captured_structlog)
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs


@pytest.fixture
def captured_structlog() -> Any:
    """Reset structlog to defaults, enter capture_logs, restore on teardown.

    Yields:
        The mutable list that capture_logs() populates with emitted events.
        Each entry is a ``dict[str, object]`` containing at minimum the
        ``event`` key plus any structured fields the caller bound.
    """
    old_config = structlog.get_config()
    structlog.reset_defaults()
    with capture_logs() as logs:
        yield logs
    structlog.configure(**old_config)
