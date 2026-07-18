"""Supported-Python compatibility guards for production tool registration."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "trw_mcp"


def test_runtime_typed_dicts_use_pydantic_compatible_backport() -> None:
    """Python 3.10/3.11 require typing_extensions for Pydantic TypedDict schemas."""
    offenders: list[str] = []
    for path in _PACKAGE_ROOT.rglob("*.py"):
        # Hook payloads are copied into user projects and remain stdlib-only.
        if "data/hooks" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module != "typing":
                continue
            if any(alias.name == "TypedDict" for alias in node.names):
                offenders.append(str(path.relative_to(_PACKAGE_ROOT)))
    assert offenders == [], f"runtime modules import typing.TypedDict: {sorted(offenders)}"


def test_server_tool_registration_on_supported_interpreter(tmp_path: Path) -> None:
    """Import and register every MCP tool under the active supported Python."""
    env = os.environ.copy()
    for key in ("TRW_META_TUNE_ENABLED", "TRW_META_TUNE__ENABLED"):
        env.pop(key, None)
    proc = subprocess.run(
        [sys.executable, "-c", "import trw_mcp.server; print('registration-ok')"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "registration-ok" in proc.stdout
