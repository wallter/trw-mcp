"""Shared support for split requirements tool tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync, make_test_server


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all requirements tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    from trw_mcp.tools._prd_template_helpers import reset_template_cache

    reset_template_cache()
    (tmp_path / ".trw").mkdir()
    return tmp_path


def _get_tools() -> dict[str, Any]:
    """Create fresh server and return the requirements tool map."""
    return get_tools_sync(make_test_server("requirements"))
