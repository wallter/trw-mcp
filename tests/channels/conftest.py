"""Shared fixtures for channels/ tests."""

from __future__ import annotations

import pytest
from pathlib import Path


@pytest.fixture()
def tmp_channels_dir(tmp_path: Path) -> Path:
    """Return a temp directory set up as a .trw/channels workspace."""
    channels = tmp_path / ".trw" / "channels"
    channels.mkdir(parents=True, exist_ok=True)
    return channels
