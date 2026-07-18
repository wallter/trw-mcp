"""Shared support for context budget middleware test splits."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests._middleware_test_fakes import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeRequestContext,
    FakeToolResult,
)
from trw_mcp.middleware.context_budget import reset_state

__all__ = ["FakeContext", "FakeMessage", "FakeMiddlewareContext", "FakeRequestContext", "FakeToolResult"]


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Reset module-level session state before and after each test."""
    reset_state()
    try:
        yield
    finally:
        reset_state()
