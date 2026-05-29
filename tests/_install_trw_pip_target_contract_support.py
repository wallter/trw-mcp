"""Shared support for install-trw.py --pip-target contract tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_INSTALLER_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_INSTALLER_ARTIFACT = Path(__file__).resolve().parent.parent / "dist" / "install-trw.py"
_INSTALLER_PATHS = [_INSTALLER_TEMPLATE, _INSTALLER_ARTIFACT]


def _load_installer_module(installer_path: Path):
    spec = importlib.util.spec_from_file_location(f"install_trw_test_{installer_path.stem}", installer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
