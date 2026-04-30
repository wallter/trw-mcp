from __future__ import annotations

import io
import tarfile
from unittest.mock import MagicMock

import pytest

from trw_mcp.models.config import _reset_config


@pytest.fixture(autouse=True)
def reset_cfg() -> None:
    _reset_config()
    yield  # type: ignore[misc]
    _reset_config()


def _make_tar_gz_bytes(members: dict[str, bytes] | None = None) -> bytes:
    """Build a tar.gz archive in memory."""
    if members is None:
        members = {"data/hello.txt": b"world"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _mock_urlopen_for_bytes(data: bytes) -> MagicMock:
    """Return a context-manager-compatible mock response that reads *data*."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp
